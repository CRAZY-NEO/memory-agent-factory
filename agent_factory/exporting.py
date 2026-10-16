"""Export a verified, frozen algorithm as a self-contained, relocatable deployment ZIP.

The bundle contains only an explicit allowlist: code, summarized evidence, a small
synthetic input, and (for classification) the frozen training examples. Neither
model traces, access tokens nor held-out evaluation records are copied.
"""

import ast
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .classification import effective_training_examples
from .requirements import requirement_contracts
from .tasks import TASKS, task_spec

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,100}")
TASK_FILES = {name: (adapter.selected_filename, adapter.entrypoint) for name, adapter in TASKS.items()}


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _read(root, relative, limit=8 * 1024 * 1024):
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe export artifact path")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"Missing or oversized export artifact: {relative}")
    return path.read_bytes()


def _json(content):
    return json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _evaluation(report):
    """Keep numeric aggregates; never export query text, labels, prompts or errors."""
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict) or any(
            not isinstance(key, str) or type(value) not in {int, float} or not math.isfinite(value)
            for key, value in metrics.items()):
        raise ValueError("Invalid validation metrics")
    result = {"split": report["split"], "passed": report["passed"], "metrics": metrics}
    thresholds = {}
    for name, rule in report.get("gates", {}).get("metrics", {}).items():
        if not isinstance(name, str) or not isinstance(rule, dict):
            raise ValueError("Invalid metric threshold evidence")
        if any(value is not None and (type(value) not in {int, float} or not math.isfinite(value))
               for value in (rule.get("min"), rule.get("max"))):
            raise ValueError("Invalid metric threshold evidence")
        thresholds[name] = {"min": rule.get("min"), "max": rule.get("max")}
    if thresholds:
        result["thresholds"] = thresholds
    checks = report.get("requirement_checks")
    if checks is not None:
        result["business_requirements"] = {"passed": checks.get("passed") is True,
                                           "ids": checks.get("requirements", []),
                                           "case_count": checks.get("case_count", 0)}
    return result


def _requirements(code, plugin, report):
    dependencies = plugin.get("dependencies", [])
    if not isinstance(dependencies, list) or not set(dependencies) <= {"scikit-learn"}:
        raise ValueError("Unsupported deployment dependencies")
    imports = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    needed = {"scikit-learn"} if any(name.split(".")[0] == "sklearn" for name in imports) else set()
    if not needed <= set(dependencies):
        raise ValueError("Generated code requires an undeclared dependency")
    versions = report.get("environment", {}).get("dependencies", {})
    pinned = []
    for name in sorted(needed):
        version = versions.get(name, "")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:[a-z]+[0-9]+)?", version):
            raise ValueError(f"Missing recorded dependency version: {name}")
        pinned.append(f"{name}=={version}")
    if task_spec(plugin['task']).portable_validation() is not None:
        pinned.append('pydantic==' + versions['pydantic'])
    return "\n".join(pinned) + ("\n" if pinned else "# This algorithm uses Python standard library only.\n")


def _source_files(workspace, algorithm):
    provenance = algorithm.get("provenance")
    if not provenance:
        if str(algorithm.get("id", "")).startswith("github_"):
            raise ValueError("Source algorithm provenance is missing")
        return {}
    identity, version = algorithm.get("id", ""), algorithm.get("version", "")
    if not IDENTIFIER.fullmatch(identity) or not IDENTIFIER.fullmatch(version):
        raise ValueError("Invalid source algorithm identity")
    root = workspace / "state" / "algorithm_registry"
    if not root.resolve().is_relative_to(workspace) or root.is_symlink():
        raise ValueError("Unsafe source registry path")
    directory = root / identity / version
    if not directory.resolve().is_relative_to(root.resolve()) or directory.is_symlink():
        raise ValueError("Unsafe source release path")
    record = json.loads(_read(directory, "manifest.json"))
    if (record.get("status") != "approved" or record.get("id") != identity
            or record.get("version") != version or not record.get("license_hashes")):
        raise ValueError("Source algorithm lacks an approved license record")
    for key in ("repository", "url", "commit", "license"):
        if not provenance.get(key) or provenance[key] != record.get("source", {}).get(key):
            raise ValueError("Source provenance differs from approved release")
    artifacts = {}
    for name in ("reference.py", "material.md", "verification.json"):
        content = _read(directory, name)
        if _hash(content) != record.get("hashes", {}).get(name):
            raise ValueError("Source release artifact hash mismatch")
        artifacts[name] = content
    proof = json.loads(artifacts["verification.json"])
    if (proof.get("validation", {}).get("passed") is not True
            or proof.get("test", {}).get("passed") is not True
            or proof.get("test", {}).get("split") != "test"
            or proof.get("runner") != "docker"
            or proof.get("code_hash") != record["hashes"]["reference.py"]):
        raise ValueError("Source release lacks verified final-test evidence")
    files = {}
    for name, expected in record["license_hashes"].items():
        if Path(name).name != name or name in {".", ".."} or "\\" in name:
            raise ValueError("Invalid source license filename")
        content = _read(directory, "source-licenses/" + name)
        if _hash(content) != expected:
            raise ValueError("Source license hash mismatch")
        files["licenses/" + name] = content
    files["source.json"] = _json({
        **{key: provenance[key] for key in ("repository", "url", "commit", "license")},
        "algorithm_id": identity, "version": version, "license_hashes": record["license_hashes"],
        "reference_code_hash": proof["code_hash"],
        "validation": _evaluation(proof["validation"]), "test": _evaluation(proof["test"]),
    })
    return files


# This runner imports only the already validated local artifact, after checking
# its digest. It needs no factory installation or LLM service. Container use is
# documented for isolation; Python execution itself is not an OS sandbox.
STANDALONE_RUNNER = '''"""Run the frozen algorithm on a JSON input; no factory or LLM required."""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_verified(name, expected):
    path = ROOT / name
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise ValueError("Unsafe bundle path")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Bundle hash mismatch: " + name)
    return content


def invoke(payload, manifest):
    source = read_verified("algorithm.py", manifest["code_hash"])
    spec = importlib.util.spec_from_file_location("frozen_algorithm", ROOT / "algorithm.py")
    module = importlib.util.module_from_spec(spec)
    # Execute the exact verified bytes, avoiding a second file read during load.
    exec(compile(source, str(ROOT / "algorithm.py"), "exec"), module.__dict__)
    if manifest.get('runtime_validation'):
        validator_bytes = read_verified('runtime_contracts.py', manifest['runtime_validation_hash'])
        validator = {'__name__': 'portable_contracts'}
        exec(compile(validator_bytes, 'runtime_contracts.py', 'exec'), validator)
        normalized = validator['parse_input'](manifest['task'], payload)
        before = copy.deepcopy(normalized)
        result = getattr(module, manifest['entrypoint'])(normalized)
        if normalized != before:
            raise ValueError('Capability mutated inputs')
        return validator['check_output'](manifest['task'], result, normalized)
    if manifest["task"] == "memory_classification":
        texts = payload.get("texts")
        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a list of strings")
        training = json.loads(read_verified("training_examples.json", manifest["training_data_hash"]))
        if not isinstance(training, list) or not training or any(
                not isinstance(row, dict) or not {"text", "label"} <= set(row)
                or not set(row) <= {"text", "label", "enabled"}
                or any(not isinstance(row[key], str) or not row[key].strip() for key in ("text", "label"))
                or ("enabled" in row and type(row["enabled"]) is not bool) for row in training):
            raise ValueError("Invalid frozen training examples or enabled flag")
        if (any(row.get("enabled") is False for row in training)
                and "classification_ignore_disabled" not in manifest.get("business_requirements", [])):
            raise ValueError("Disabled training examples require the ignore-disabled business contract")
        before = copy.deepcopy((texts, training))
        result = module.classify(texts, training)
        if (texts, training) != before:
            raise ValueError("Algorithm mutated classification inputs")
        labels = {row["label"] for row in training if row.get("enabled") is not False}
        if (not isinstance(result, list) or len(result) != len(texts)
                or any(not isinstance(label, str) or label not in labels for label in result)):
            raise ValueError("Classification output violates the label contract")
        return result
    query, memories, top_k = payload.get("query"), payload.get("memories"), payload.get("top_k", 5)
    if not isinstance(query, str) or type(top_k) is not int or not isinstance(memories, list):
        raise ValueError("Expected query: string, memories: list, top_k: integer")
    if any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
           or not row["id"] or not isinstance(row.get("text"), str) or not row["text"]
           or ("archived" in row and type(row["archived"]) is not bool) for row in memories):
        raise ValueError("Each memory needs nonempty string id and text")
    ids = {row["id"] for row in memories}
    if len(ids) != len(memories):
        raise ValueError("Memory IDs must be unique")
    before = copy.deepcopy(memories)
    result = module.retrieve(query, memories, top_k)
    if memories != before:
        raise ValueError("Algorithm mutated memories")
    if not isinstance(result, list) or len(result) > max(top_k, 0):
        raise ValueError("Invalid retrieval result count")
    if any(not isinstance(hit, dict) or set(hit) != {"id", "score"}
           or not isinstance(hit["id"], str) or hit["id"] not in ids
           or type(hit["score"]) not in {int, float} or not math.isfinite(hit["score"])
           or hit["score"] < 0 for hit in result):
        raise ValueError("Invalid retrieval output fields")
    if len({hit["id"] for hit in result}) != len(result):
        raise ValueError("Duplicate retrieval result IDs")
    if result != sorted(result, key=lambda hit: (-hit["score"], hit["id"])):
        raise ValueError("Results must be sorted by descending score, then ID")
    if (not query.strip() or not memories or top_k <= 0) and result:
        raise ValueError("Empty input must return []")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(ROOT / "example_input.json"), help="JSON path, or - for stdin")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    if (manifest.get("task") not in {"memory_retrieval", "memory_classification"} and not manifest.get("runtime_validation")) or manifest.get("interface_version") != "1":
        raise ValueError("Unsupported bundle interface")
    payload = json.load(sys.stdin) if args.input == "-" else json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Input must be a JSON object")
    print(json.dumps(invoke(payload, manifest), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        sys.exit(1)
'''

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 65532:65532
ENTRYPOINT ["python", "-B", "run.py"]
"""


def _readme(task):
    adapter = task_spec(task)
    if adapter.portable_validation() is not None:
        return """# 已验证的结构化文档检查组件

本包包含真实生成的冻结实现、可信输入输出检查、接口契约及验证摘要，不需要工厂或大模型服务。
需要 Python 3.11+。安装 requirements.txt 后执行 `python run.py --input example_input.json`。
`--input -` 从标准输入读取 JSON。接口见 capability_contract.json，缺失字段与引用状态含义见其 behaviors。
引用位置以 Python Unicode 字符索引计数，右端不包含；这不是语义事实核查或 OCR。

建议在 Docker 中运行：构建 `docker build -t document-capability .`，执行
`docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges document-capability`。
真实部署可复用同一代码及契约；最终测试通过仅表示在所记录样例和门槛下合格。
哈希检查检测意外修改，不是数字签名。包内仅有合成使用示例，不包含评估样例、文档、密钥或模型提示。
"""
    training = ("分类包包含验证时使用的全部训练样本 `training_examples.json`（文本、标签及可选 enabled 标记）。"
                "运行时基于这些样本训练后预测，不需要下载数据。分享 ZIP 会同时分享训练文本；"
                "禁用样本也会随包保存，enabled=false 表示不参与训练，不表示已从包中删除。"
                "没有打包验证集或最终测试集。\n\n"
                "输入格式：`{\"texts\": [\"待分类的技术记忆\"]}`，输出与输入等长的标签列表。"
                if task == "memory_classification" else
                "输入包含 `query`、`memories`（每条含 `id`、`text`，可含 `source`、布尔 archived）、`top_k`；"
                "输出为按分数降序排列的 `{id, score}` 列表。包内示例为合成数据，未包含工作区的记忆库。")
    return f"""# 冻结算法部署包

本包导出的是已经通过验证集和最终测试的选定算法。运行不需要安装 Agent Factory、连接 Ollama 或 Jetson。
代码、接口、训练样本（分类任务）及指标摘要见 `plugin.json` 和 `report.json`。
`plugin.json` 的 `business_requirements` 记录经过验证的扩展行为；`requirements` 指向 Python 依赖文件。

## 本地运行

需要 Python 3.11+（推荐 3.12）。在解压目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
.venv/bin/python run.py --input example_input.json
```

Windows 对应使用 `.venv\\Scripts\\python.exe`。`--input -` 可从标准输入读取 JSON。
运行器检查代码哈希、输入输出接口及输入未被修改。依赖只列算法直接使用的包，固定到验证环境记录的版本；pip 会安装其传递依赖。
首次安装依赖或构建镜像需要网络。安装后执行算法无需联网。跨操作系统和 CPU 架构仍取决于依赖包提供的 wheel。

{training}

## Docker 运行

```bash
docker build -t exported-memory-algorithm .
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1 --pids-limit 64 --tmpfs /tmp:rw,noexec,nosuid,size=32m exported-memory-algorithm
```

使用自有输入：增加 `-v "$PWD/input.json:/input.json:ro"`，在镜像名后加 `--input /input.json`。
本地 Python 调用不会建立安全沙箱，Docker 示例提供进程资源与网络隔离。容器命令不会自动终止长期运行的任务；按 Ctrl+C 可取消。

## 来源与验证范围

`report.json` 是脱敏后的指标摘要，不包含模型提示词、访问密钥、查询/测试文本或验证标签。
若算法来自外部仓库，`source.json` 与 `licenses/` 保留固定提交、许可证和原始许可文本。
最终测试通过表示在原数据和门槛下通过，不保证任意新数据上的效果。哈希用于检测意外篡改，不是数字签名。
"""


def export_run(workspace, run_id):
    """Create runs/<run_id>/deployment.zip; reject failed or inconsistent artifacts."""
    workspace = Path(workspace).resolve()
    if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
        raise ValueError("Invalid run ID")
    root = workspace / "runs"
    if not root.resolve().is_relative_to(workspace) or root.is_symlink():
        raise ValueError("Unsafe runs path")
    directory = root / run_id
    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Unsafe run path")
    report = json.loads(_read(directory, "report.json", limit=32 * 1024 * 1024))
    plugin = json.loads(_read(directory, "plugin.json"))
    selected, final_test = report.get("selected") or {}, report.get("test") or {}
    validation = selected.get("validation") or {}
    if (report.get("status") != "passed" or final_test.get("passed") is not True
            or final_test.get("split") != "test" or validation.get("passed") is not True
            or validation.get("split") != "validation"):
        raise ValueError("Only algorithms passing validation and final test can be exported")
    task = plugin.get("task")
    if task not in TASK_FILES or plugin.get("interface_version") != "1":
        raise ValueError("Unsupported deployment interface")
    adapter = task_spec(task)
    filename, entrypoint = adapter.selected_filename, adapter.entrypoint
    if (plugin.get("run_id") != run_id or report.get("run_id") != run_id
            or plugin.get("entrypoint") != entrypoint
            or report.get("request", {}).get("task") != task):
        raise ValueError("Plugin and report identity mismatch")
    code = _read(directory, filename, limit=512 * 1024)
    code_hash = _hash(code)
    if code_hash != plugin.get("code_hash") or code_hash != selected.get("code_hash"):
        raise ValueError("Frozen algorithm code hash mismatch")
    algorithm = plugin.get("algorithm_plugin", {})
    if algorithm.get("id") != selected.get("algorithm") or algorithm.get("task") != task:
        raise ValueError("Selected algorithm metadata mismatch")
    requirements = _requirements(code, plugin, report)
    business_requirements = plugin.get("requirements", [])
    requirement_contracts(task, business_requirements)
    if report.get("requirements", []) != business_requirements:
        raise ValueError("Business requirements differ between plugin and report")
    if business_requirements:
        for evidence, split in ((validation, "validation"), (final_test, "test")):
            checks = evidence.get("requirement_checks", {})
            if (checks.get("passed") is not True or checks.get("split") != split
                    or checks.get("requirements") != business_requirements
                    or type(checks.get("case_count")) is not int or checks["case_count"] <= 0):
                raise ValueError("Business requirements lack matching validation/final-test evidence")
    contract = plugin.get('capability_contract')
    if contract is not None:
        from .contracts import contract_hash
        saved = json.loads(_read(directory, 'capability_contract.json'))
        if (saved != contract or contract != adapter.contract.model_dump() or report.get('capability_contract') != contract
                or plugin.get('contract_hash') != contract_hash(contract)
                or report.get('contract_hash') != contract_hash(contract)):
            raise ValueError('Capability contract does not match frozen publication evidence')
    elif adapter.portable_validation() is not None:
        raise ValueError('Document capabilities require a versioned contract')
    generation = {}
    for key in ("generation_policy", "generation_mode", "history_mode", "generation_backend", "model_seed"):
        value = report.get(key, plugin.get(key))
        if value is not None:
            if key == "model_seed":
                if type(value) is not int:
                    raise ValueError("Invalid generation seed evidence")
            elif not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
                raise ValueError("Invalid generation policy evidence")
            generation[key] = value
    for key in ("generation_mode", "history_mode", "model_seed"):
        if key in plugin and key in report and plugin[key] != report[key]:
            raise ValueError("Generation policy differs between plugin and report")
    manifest = {"task": task, "entrypoint": entrypoint, "interface_version": "1",
                "run_id": run_id, "code_hash": code_hash, "code_path": "algorithm.py",
                "validation_report": "report.json", "algorithm": {
                    key: algorithm[key] for key in ("id", "name", "version")},
                "requirements": "requirements.txt", "business_requirements": business_requirements,
                **generation}
    files = {"algorithm.py": code, "run.py": STANDALONE_RUNNER.encode(),
             "requirements.txt": requirements.encode(), "Dockerfile": DOCKERFILE.encode(),
             ".dockerignore": b".venv\n__pycache__\n*.zip\n.git\n", "README.md": _readme(task).encode()}
    if contract is not None:
        manifest.update(capability_contract=contract, contract_hash=plugin['contract_hash'])
        files['capability_contract.json'] = _json(contract)
    portable = adapter.portable_validation()
    if portable is not None:
        content = portable.read_bytes()
        manifest.update(runtime_validation='runtime_contracts.py', runtime_validation_hash=_hash(content))
        files['runtime_contracts.py'] = content
        example = adapter.example_input()
    elif task == "memory_classification":
        if plugin.get("training_data") != "training_examples.json":
            raise ValueError("Classification requires frozen training examples")
        training_bytes = _read(directory, "training_examples.json")
        if (_hash(training_bytes) != plugin.get("training_data_hash")
                or _hash(training_bytes) != report.get("training_data_hash")):
            raise ValueError("Frozen training data hash mismatch")
        training = json.loads(training_bytes)
        effective = effective_training_examples(training, business_requirements, nonempty=True, nonblank=True)
        if (any(row.get("enabled") is False for row in training)
                and "classification_ignore_disabled" not in business_requirements):
            raise ValueError("Disabled training examples require the ignore-disabled business contract")
        manifest.update(training_data="training_examples.json", training_data_hash=_hash(training_bytes),
                        includes_training_text=True)
        files["training_examples.json"] = training_bytes
        example = {"texts": [row["text"] for row in effective[:3]]}
    else:
        example = {"query": "Python 日志排查", "top_k": 2, "memories": [
            {"id": "example_1", "text": "Python 日志记录错误堆栈，帮助排查问题", "source": "synthetic://example"},
            {"id": "example_2", "text": "MySQL 连接池设置超时与容量", "source": "synthetic://example"}]}
    files["example_input.json"] = _json(example)
    files["plugin.json"] = _json(manifest)
    files["report.json"] = _json({
        "run_id": run_id, "status": "passed", "task": task, "code_hash": code_hash,
        "algorithm": manifest["algorithm"], "validation": _evaluation(validation), "test": _evaluation(final_test),
        "runner": report.get("runner"), "dataset_hashes": report.get("dataset_hashes", {}),
        "business_requirements": business_requirements, **generation,
        "note": "Aggregate evidence only; evaluation text, labels and model traces are not exported.",
    })
    files.update(_source_files(workspace, algorithm))
    files["checksums.json"] = _json({name: _hash(content) for name, content in sorted(files.items())})
    temporary = directory / (".deployment-" + uuid.uuid4().hex + ".tmp")
    destination = directory / "deployment.zip"
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination

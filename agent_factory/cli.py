"""CLI separates factory requests from querying the plugin that the factory produced."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .knowledge import KnowledgeStore, ingest_sources
from .llm import LLMClient
from .models import ValidationConfig
from .registry import load_registry
from .repair import load_failed_codes
from .repository import ingest_repository
from .routing import AutomaticFactory
from .runtime import MemoryRetriever
from .validation import load_dataset, validate
from .visualization import write_graph_html


def provider_options(parser):
    parser.add_argument("--provider", choices=["mock", "ollama"], default=os.getenv("FACTORY_PROVIDER", "mock"))
    parser.add_argument("--model", default=os.getenv("OLLAMA_CHAT_MODEL"), help="Ollama 中已安装的准确模型名称")
    parser.add_argument("--base-url", default=os.getenv("OLLAMA_URL", "http://localhost:11434"), help="Jetson Ollama 服务地址")


def resolve_model(arguments):
    if arguments.provider == "ollama" and not arguments.model:
        raise ValueError("Ollama 模式必须指定 --model；本项目不假设你的 Jetson 已安装哪一种模型。")
    return arguments.model or "mock-template-v1"


def parser():
    root = argparse.ArgumentParser(description="个人技术记忆检索算法能力工厂")
    root.add_argument("--workspace", type=Path, default=Path.cwd(), help="包含 data/config/knowledge 的项目目录")
    root.add_argument("--verbose", action="store_true")
    root.add_argument("--config", type=Path, help="覆盖工作目录的 config.json")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("plugins", help="列出已发现的算法、指标及版本")
    ingest = commands.add_parser("ingest", help="从算法材料抽取能力并写入知识库")
    provider_options(ingest)
    repository = commands.add_parser("ingest-repo", help="静态抽取本地 Python 仓库的函数、依赖与来源证据")
    repository.add_argument("repository", type=Path, help="本地代码仓库目录；不导入或运行目标源码")
    repository.add_argument("--algorithm", help="人工关联一个已启用算法，不自动注册或认证目标代码")
    repository.add_argument("--include", action="append", help="相对仓库路径的 glob，可重复；默认 *.py")
    repository.add_argument("--output", type=Path, help="默认 knowledge/extracted_repository.json")
    repository.add_argument("--max-files", type=int, default=200)
    repository.add_argument("--max-file-bytes", type=int, default=262144)
    repository.add_argument("--max-total-bytes", type=int, default=5242880)
    repository.add_argument("--max-symbols", type=int, default=1000)
    discover = commands.add_parser('discover', help='GitHub 搜索 → 下载源码 → 复刻新算法 → Docker 验证 → 自动登记')
    provider_options(discover)
    discover.set_defaults(provider=os.getenv('FACTORY_PROVIDER', 'ollama'),
                          model=os.getenv('OLLAMA_CHAT_MODEL', 'qwen3.5:2b'),
                          base_url=os.getenv('OLLAMA_URL', 'http://127.0.0.1:11435'))
    discover.add_argument('--description', required=True, help='描述要发现的新检索方法；不需要预先编写插件')
    run = commands.add_parser("run", help="需求 → 检索知识 → 生成 → 验证 → 修复 → 沉淀")
    provider_options(run)
    request = run.add_mutually_exclusive_group(required=True)
    request.add_argument("--description", help="自然语言算法开发需求")
    request.add_argument("--request-file", type=Path, help="UTF-8 算法开发需求文件")
    run.add_argument("--runner", choices=["local", "docker"], help="mock 默认 local；Ollama 默认 docker")
    run.add_argument("--allow-local", action="store_true", help="明确接受本地执行边界，允许真实模型代码在本机子进程运行")
    run.add_argument("--repair-from", type=Path, help="重放该运行目录中哈希校验通过的历史失败代码并修复")
    run.add_argument("--inject-fault", action="store_true", help="仅 mock：故意破坏 BM25 首版，演示真实检测和修复")
    run.add_argument('--mode', choices=['auto', 'reuse', 'discover'], default='auto',
                     help='默认自动判断：已有能力优先，缺少方法时发现；可手动限制模式')
    query = commands.add_parser("query", help="调用已验证插件，供未来个人记忆应用复用")
    query.add_argument("text")
    query.add_argument("--run", default="latest", help="运行 ID，默认最近一次通过并发布插件的运行")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--memories", type=Path, help="自己的 id/text/source JSON 记忆列表")
    query.add_argument("--runner", choices=["local", "docker"], help="默认使用插件生成时的执行器")
    export = commands.add_parser("export", help="导出 JSON/GraphML/离线 HTML 图谱")
    export.add_argument("--output", type=Path)
    check = commands.add_parser("validate", help="独立运行可信算法验证器")
    check.add_argument("--algorithm", help="可选：检查注册算法的公开公式样例")
    check.add_argument("--code", type=Path, required=True)
    check.add_argument("--split", choices=["validation", "test"], default="validation")
    check.add_argument("--runner", choices=["local", "docker"], default="docker", help="local 不提供 OS 安全隔离")
    check.add_argument("--top-k", type=int, default=5)
    return root


def main(argv=None):
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    workspace = arguments.workspace.resolve()
    try:
        config_path = arguments.config or workspace / "config.json"
        config = ValidationConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
        registry = load_registry(config.plugin_packages, workspace=workspace)
        registry.validate_config(config)
        if arguments.command == "plugins":
            print(json.dumps(registry.catalog(), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == 'discover':
            if arguments.provider != 'ollama':
                raise ValueError('GitHub 新算法发现需要真实 LLM；离线网络与模型替身只用于自动测试')
            from .discovery import DiscoveryFactory
            factory = DiscoveryFactory(workspace, arguments.provider, resolve_model(arguments), arguments.base_url,
                                       config=config, registry=registry)
            result = factory.run(arguments.description)
            result['report'] = str(factory.directory / 'report.md')
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'passed' else 1
        if arguments.command == "run":
            description = arguments.description or arguments.request_file.read_text(encoding="utf-8")
            factory = AutomaticFactory(workspace, arguments.provider, resolve_model(arguments), arguments.base_url,
                              arguments.runner, arguments.inject_fault, arguments.allow_local,
                              config=config, registry=registry, mode=arguments.mode)
            if arguments.repair_from and arguments.inject_fault:
                raise ValueError('Historical replay cannot be combined with mock fault injection')
            seeds = load_failed_codes(arguments.repair_from, config.algorithms) if arguments.repair_from else None
            result = factory.run(description, initial_codes=seeds)
            print(json.dumps({"status": result["status"], "run_id": result["run_id"],
                              "report": str(Path(result["directory"]) / "report.md"),
                              "algorithm": result.get('algorithm') or (result.get('selected') or {}).get('algorithm'),
                              'routing': result['routing'], 'discovery_run_id': result.get('discovery_run_id')},
                             ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else 1
        if arguments.command == "ingest":
            store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
            client = LLMClient(arguments.provider, resolve_model(arguments), arguments.base_url,
                               workspace / "state" / "ingest_traces", registry=registry)
            output = workspace / "knowledge" / "extracted_capabilities.json"
            result = ingest_sources(store, client, workspace / "knowledge" / "sources", output,
                                    registry=registry, algorithm_ids=config.algorithms)
            print(json.dumps({"capabilities": len(result), "output": str(output)}, ensure_ascii=False))
        elif arguments.command == "ingest-repo":
            if arguments.algorithm and arguments.algorithm not in config.algorithms:
                raise ValueError("Repository association requires an enabled algorithm")
            store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
            output = arguments.output or workspace / "knowledge" / "extracted_repository.json"
            result = ingest_repository(store, arguments.repository, output, registry=registry,
                                       algorithm=arguments.algorithm, include=arguments.include,
                                       max_files=arguments.max_files, max_file_bytes=arguments.max_file_bytes,
                                       max_total_bytes=arguments.max_total_bytes, max_symbols=arguments.max_symbols)
            snapshot = result["snapshot"]
            print(json.dumps({"snapshot_id": snapshot["id"], "status": snapshot["status"],
                              "files_parsed": snapshot["files_parsed"], "candidates": len(result["candidates"]),
                              "limited": snapshot["limited"], "issues": snapshot["issues"],
                              "output": str(output)}, ensure_ascii=False, indent=2))
        elif arguments.command == "export":
            store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
            output = arguments.output or workspace / "state" / "export"
            store.export(output)
            write_graph_html(store, output / "graph.html")
            print(str(output))
        elif arguments.command == "query":
            if arguments.run == "latest":
                runs = sorted((workspace / "runs").glob("*/plugin.json"))
                if not runs:
                    raise ValueError("没有通过验证的插件，请先运行 run")
                manifest = runs[-1]
            else:
                if Path(arguments.run).name != arguments.run:
                    raise ValueError("--run must be a run ID, not a path")
                manifest = workspace / "runs" / arguments.run / "plugin.json"
            memories = json.loads((arguments.memories or workspace / "data" / "memories.json").read_text(encoding="utf-8"))
            hits = MemoryRetriever(manifest, arguments.runner).retrieve(arguments.text, memories, arguments.top_k)
            lookup = {memory["id"]: memory for memory in memories}
            print(json.dumps([{**hit, "text": lookup[hit["id"]]["text"], "source": lookup[hit["id"]]["source"]}
                              for hit in hits], ensure_ascii=False, indent=2))
        elif arguments.command == "validate":
            from .runner import Runner
            memories, queries = load_dataset(workspace / "data", arguments.split)
            result = validate(arguments.code.read_text(encoding="utf-8"),
                              Runner(arguments.runner, config.timeout_seconds), memories, queries,
                              arguments.top_k, config, arguments.split, registry=registry, algorithm=arguments.algorithm)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

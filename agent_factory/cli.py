"""CLI separates factory requests from querying the plugin that the factory produced."""

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .exporting import export_run
from .knowledge import KnowledgeStore, ingest_sources
from .llm import LLMClient
from .models import ValidationConfig
from .providers import PROVIDERS, REAL_PROVIDERS, load_project_env, resolve_settings
from .registry import load_registry
from .repair import load_failed_codes
from .repository import ingest_repository
from .routing import AutomaticFactory
from .runtime import CapabilityPlugin, MemoryRetriever
from .tasks import TASKS, load_task_config, task_catalog, task_spec
from .visualization import write_graph_html


def provider_options(parser):
    parser.add_argument("--provider", choices=PROVIDERS, default=os.getenv("FACTORY_PROVIDER", "mock"))
    parser.add_argument("--model", help="模型名称；默认读取对应 provider 的环境配置")
    parser.add_argument("--base-url", help="模型服务地址；默认读取对应 provider 的环境配置")


def resolve_model(arguments):
    return resolve_settings(arguments.provider, arguments.model, arguments.base_url)[0]


def parser():
    root = argparse.ArgumentParser(description="个人技术记忆检索与分类算法能力工厂")
    root.add_argument("--workspace", type=Path, default=Path.cwd(), help="包含 data/config/knowledge 的项目目录")
    root.add_argument("--verbose", action="store_true")
    root.add_argument("--config", type=Path, help="覆盖工作目录的 config.json")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="列出统一能力契约及可信任务适配器")
    invoke = commands.add_parser("invoke", help="使用统一 JSON 接口调用已验证能力组件")
    invoke.add_argument("--run", required=True, help="已通过最终测试的运行 ID")
    invoke.add_argument("--input", type=Path, required=True, help="JSON 输入文件")
    invoke.add_argument("--runner", choices=["local", "docker"])
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
    discover.set_defaults(provider=os.getenv('FACTORY_PROVIDER', 'ollama'))
    discover.add_argument('--description', required=True, help='描述要发现的新检索方法；不需要预先编写插件')
    run = commands.add_parser("run", help="需求 → 检索知识 → 生成 → 验证 → 修复 → 沉淀")
    provider_options(run)
    request = run.add_mutually_exclusive_group(required=True)
    request.add_argument("--description", help="自然语言算法开发需求")
    request.add_argument("--request-file", type=Path, help="UTF-8 算法开发需求文件")
    run.add_argument("--task", choices=list(TASKS), help="可选：明确任务类型，并核对自然语言路由结果")
    run.add_argument("--runner", choices=["local", "docker"], help="mock 默认 local；真实模型默认 docker")
    run.add_argument("--allow-local", action="store_true", help="明确接受本地执行边界，允许真实模型代码在本机子进程运行")
    run.add_argument("--repair-from", type=Path, help="重放该运行目录中哈希校验通过的历史失败代码并修复")
    run.add_argument("--inject-fault", action="store_true", help="仅 mock：故意破坏 BM25 首版，演示真实检测和修复")
    run.add_argument('--mode', choices=['auto', 'reuse', 'discover'], default='auto',
                     help='默认自动判断：已有能力优先，缺少方法时发现；可手动限制模式')
    run.add_argument('--generation-mode', choices=['spec', 'reference'], help='spec 根据规格生成；reference 提供完整参考实现')
    run.add_argument('--history-mode', choices=['on', 'off'], help='是否提供跨运行历史经验；不影响本轮错误反馈')
    run.add_argument('--seed', type=int, help='Ollama 采样种子；DeepSeek 不支持，报告标注未应用')
    run.add_argument('--requirement', action='append', help='启用可信业务扩展要求 ID，可重复指定')
    query = commands.add_parser("query", help="调用已验证插件，供未来个人记忆应用复用")
    query.add_argument("text")
    query.add_argument("--run", default="latest", help="运行 ID，默认最近一次通过并发布插件的运行")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--memories", type=Path, help="自己的 id/text/source JSON 记忆列表")
    query.add_argument("--runner", choices=["local", "docker"], help="默认使用插件生成时的执行器")
    export = commands.add_parser("export", help="--run-id 导出算法 ZIP；不指定时导出知识图谱")
    export.add_argument("--run-id", help="将该运行已通过最终测试的选定算法导出为可运行 ZIP")
    export.add_argument("--output", type=Path, help="ZIP 输出文件路径，或知识图谱输出目录")
    check = commands.add_parser("validate", help="独立运行可信算法验证器")
    check.add_argument('--task', choices=list(TASKS), help='选择验证任务及对应配置；默认沿用 --config 或检索配置')
    check.add_argument("--algorithm", help="可选：检查注册算法的公开公式样例")
    check.add_argument("--code", type=Path, required=True)
    check.add_argument("--split", choices=["validation", "test"], default="validation")
    check.add_argument("--runner", choices=["local", "docker"], default="docker", help="local 不提供 OS 安全隔离")
    check.add_argument("--top-k", type=int, default=5)
    check.add_argument('--requirement', action='append', help='额外验证的可信业务要求 ID，可重复指定')
    return root


def main(argv=None):
    workspace_options = argparse.ArgumentParser(add_help=False)
    workspace_options.add_argument('--workspace', type=Path, default=Path.cwd())
    preliminary, _ = workspace_options.parse_known_args(argv)
    try:
        load_project_env(preliminary.workspace.resolve())
    except (ValueError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if arguments.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    workspace = arguments.workspace.resolve()
    try:
        if hasattr(arguments, 'provider'):
            if arguments.command == 'discover' and arguments.provider == 'ollama' and not arguments.base_url:
                arguments.base_url = os.getenv('OLLAMA_URL') or 'http://127.0.0.1:11435'
            arguments.model, arguments.base_url = resolve_settings(
                arguments.provider, arguments.model, arguments.base_url)
        if arguments.command == 'run' and arguments.provider == 'deepseek':
            if arguments.runner == 'local' or arguments.allow_local:
                raise ValueError('DeepSeek 生成的代码必须使用 Docker 执行')
            arguments.runner = 'docker'
        # Export is based on the frozen run, independent of today's workspace config.
        if arguments.command == 'export' and arguments.run_id:
            output = export_run(workspace, arguments.run_id)
            if arguments.output:
                destination = arguments.output.resolve()
                if destination.suffix.lower() != '.zip':
                    raise ValueError('--output must be a .zip file for deployment export')
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination != output.resolve():
                    shutil.copyfile(output, destination)
                output = destination
            print(str(output))
            return 0
        config_path = arguments.config or workspace / "config.json"
        history = None
        history_task = None
        if arguments.command == 'run' and arguments.repair_from:
            if arguments.inject_fault:
                raise ValueError('Historical replay cannot be combined with mock fault injection')
            history = json.loads((arguments.repair_from / 'report.json').read_text(encoding='utf-8'))
            history_task = history.get('request', {}).get('task', 'memory_retrieval')
            if history_task not in TASKS:
                raise ValueError('Historical report has an unsupported task')
            if history.get('validation_config', {}).get('task', history_task) != history_task:
                raise ValueError('Historical request and validation configuration specify different tasks')
        if history_task and not arguments.config:
            # Keep today's trusted task gates; do not take thresholds from historical evidence.
            config = load_task_config(workspace, history_task)
        elif arguments.command in {'validate', 'run'} and arguments.task and not arguments.config:
            config = load_task_config(workspace, arguments.task)
        else:
            config = ValidationConfig.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
        if arguments.command == 'validate' and arguments.task and arguments.task != config.task:
            raise ValueError('--task and --config specify different tasks')
        if history_task and config.task != history_task:
            raise ValueError('--config and historical report specify different tasks')
        registry = load_registry(config.plugin_packages, workspace=workspace)
        registry.validate_config(config)
        if history:
            for attempt in history['attempts']:
                algorithm = attempt['algorithm']
                if algorithm in registry.algorithms and registry.algorithm(algorithm).task != history_task:
                    raise ValueError('Historical candidate is incompatible with its task: ' + algorithm)
        if arguments.command == 'capabilities':
            print(json.dumps(task_catalog(), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == 'invoke':
            import re
            if not re.fullmatch(r'\d{8}T\d{6}_[0-9a-f]{8}', arguments.run):
                raise ValueError('Invalid run ID')
            component = CapabilityPlugin(workspace / 'runs' / arguments.run / 'plugin.json', arguments.runner)
            print(json.dumps(component.invoke(json.loads(arguments.input.read_text(encoding='utf-8'))), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == "plugins":
            print(json.dumps(registry.catalog(), ensure_ascii=False, indent=2))
            return 0
        if arguments.command == 'discover':
            if arguments.provider not in REAL_PROVIDERS:
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
            seeds = load_failed_codes(arguments.repair_from, config.algorithms) if arguments.repair_from else None
            factory = AutomaticFactory(workspace, arguments.provider, resolve_model(arguments), arguments.base_url,
                              arguments.runner, arguments.inject_fault, arguments.allow_local,
                              config=config, registry=registry, mode=arguments.mode,
                              policy_overrides={'generation_mode': arguments.generation_mode,
                                                'history_mode': arguments.history_mode,
                                                'model_seed': arguments.seed,
                                                'requirements': arguments.requirement})
            result = factory.run(description, initial_codes=seeds, expected_task=history_task or arguments.task)
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
                runs = [path for path in sorted((workspace / "runs").glob("*/plugin.json"))
                        if json.loads(path.read_text(encoding='utf-8')).get('task') == 'memory_retrieval']
                if not runs:
                    raise ValueError("没有通过验证的检索插件，请先运行检索任务")
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
            from .requirements import requirement_contracts, validate_requirements
            from .runner import Runner
            requirements = arguments.requirement if arguments.requirement is not None else config.requirements
            requirement_contracts(config.task, requirements)
            task = task_spec(config.task)
            if arguments.requirement is not None:
                config = config.model_copy(update={'requirements': requirements})
            task.preflight(workspace / 'data', config)
            inputs, examples = task.load(workspace / 'data', arguments.split, config)
            code = arguments.code.read_text(encoding="utf-8")
            runner = Runner(arguments.runner, config.timeout_seconds)
            result = task.evaluate(code, runner, inputs, examples,
                                   {'top_k': arguments.top_k}, config, registry, arguments.algorithm, arguments.split)
            if requirements:
                checks = validate_requirements(code, runner, config.task, requirements, split=arguments.split)
                result['requirement_checks'] = checks
                result['errors'].extend(checks['errors'])
                result['passed'] = result['passed'] and checks['passed']
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

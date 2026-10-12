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
from .runtime import MemoryRetriever
from .validation import load_dataset, validate
from .visualization import write_graph_html
from .workflow import Factory


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
    commands = root.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="从算法材料抽取能力并写入知识库")
    provider_options(ingest)
    run = commands.add_parser("run", help="需求 → 检索知识 → 生成 → 验证 → 修复 → 沉淀")
    provider_options(run)
    request = run.add_mutually_exclusive_group(required=True)
    request.add_argument("--description", help="自然语言算法开发需求")
    request.add_argument("--request-file", type=Path, help="UTF-8 算法开发需求文件")
    run.add_argument("--runner", choices=["local", "docker"], help="mock 默认 local；Ollama 默认 docker")
    run.add_argument("--allow-local", action="store_true", help="明确接受本地执行边界，允许真实模型代码在本机子进程运行")
    run.add_argument("--inject-fault", action="store_true", help="仅 mock：故意破坏 BM25 首版，演示真实检测和修复")
    query = commands.add_parser("query", help="调用已验证插件，供未来个人记忆应用复用")
    query.add_argument("text")
    query.add_argument("--run", default="latest", help="运行 ID，默认最近一次通过并发布插件的运行")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--memories", type=Path, help="自己的 id/text/source JSON 记忆列表")
    query.add_argument("--runner", choices=["local", "docker"], help="默认使用插件生成时的执行器")
    export = commands.add_parser("export", help="导出 JSON/GraphML/离线 HTML 图谱")
    export.add_argument("--output", type=Path)
    check = commands.add_parser("validate", help="独立运行可信算法验证器")
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
        if arguments.command == "run":
            description = arguments.description or arguments.request_file.read_text(encoding="utf-8")
            factory = Factory(workspace, arguments.provider, resolve_model(arguments), arguments.base_url,
                              arguments.runner, arguments.inject_fault, arguments.allow_local)
            result = factory.run(description)
            print(json.dumps({"status": result["status"], "run_id": result["run_id"],
                              "report": str(Path(result["directory"]) / "report.md"),
                              "algorithm": result["selected"]["algorithm"] if result["selected"] else None},
                             ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else 1
        if arguments.command == "ingest":
            store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
            client = LLMClient(arguments.provider, resolve_model(arguments), arguments.base_url,
                               workspace / "state" / "ingest_traces")
            output = workspace / "knowledge" / "extracted_capabilities.json"
            result = ingest_sources(store, client, workspace / "knowledge" / "sources", output)
            print(json.dumps({"capabilities": len(result), "output": str(output)}, ensure_ascii=False))
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
            config = ValidationConfig.model_validate(json.loads((workspace / "config.json").read_text(encoding="utf-8")))
            from .runner import Runner
            memories, queries = load_dataset(workspace / "data", arguments.split)
            result = validate(arguments.code.read_text(encoding="utf-8"),
                              Runner(arguments.runner, config.timeout_seconds), memories, queries,
                              arguments.top_k, config, arguments.split)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 1
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

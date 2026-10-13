"""Jetson-host worker consumes API jobs and manages isolated algorithm containers."""

import argparse
import json
import logging
import os
import signal
import threading
from pathlib import Path

from .jobs import JobStore
from .knowledge import KnowledgeStore, ingest_sources
from .llm import LLMClient
from .models import ValidationConfig
from .registry import load_registry
from .runtime import MemoryRetriever
from .workflow import Factory

LOGGER = logging.getLogger(__name__)


def execute_job(workspace, job, provider, model, base_url, runner, store=None):
    kind, payload = job["kind"], job["payload"]
    if kind == "factory":
        factory = Factory(workspace, provider, model, base_url, runner)
        if store:
            store.attach_run(job['id'], factory.run_id)
        result = factory.run(payload["description"])
        return {"status": result["status"], "run_id": result["run_id"],
                "algorithm": result["selected"]["algorithm"] if result["selected"] else None,
                "report_url": f"/v1/runs/{result['run_id']}/report"}
    if kind == "ingest":
        config = ValidationConfig.model_validate(json.loads((workspace / "config.json").read_text(encoding="utf-8")))
        registry = load_registry(config.plugin_packages)
        registry.validate_config(config)
        client = LLMClient(provider, model, base_url, workspace / "state" / "ingest" / job["id"],
                           registry=registry)
        store = KnowledgeStore(workspace / "state" / "knowledge.sqlite")
        records = ingest_sources(store, client, workspace / "knowledge" / "sources",
                                 workspace / "state" / "extracted_capabilities.json",
                                 registry=registry, algorithm_ids=config.algorithms)
        return {"status": "passed", "capabilities": len(records)}
    if kind == "search":
        if payload.get("run_id"):
            manifest = workspace / "runs" / payload["run_id"] / "plugin.json"
        else:
            manifests = sorted((workspace / "runs").glob("*/plugin.json"))
            if not manifests:
                raise ValueError("No approved retrieval plugin; submit a factory job first")
            manifest = manifests[-1]
        memories = payload.get("memories")
        if memories is None:
            memories = json.loads((workspace / "data" / "memories.json").read_text(encoding="utf-8"))
        hits = MemoryRetriever(manifest, runner).retrieve(payload["query"], memories, payload["top_k"])
        lookup = {memory["id"]: memory for memory in memories}
        return {"status": "passed", "run_id": manifest.parent.name,
                "hits": [{**hit, "text": lookup[hit["id"]]["text"], "source": lookup[hit["id"]]["source"]}
                         for hit in hits]}
    raise ValueError("Unsupported job kind")


def process_one(store, workspace, provider="mock", model="mock-template-v1",
                base_url="http://localhost:11434", runner="local"):
    job = store.claim()
    if job is None:
        return False
    stop = threading.Event()

    def keep_lease():
        while not stop.wait(10):
            store.heartbeat(job["id"])

    heartbeat = threading.Thread(target=keep_lease, daemon=True)
    heartbeat.start()
    try:
        result = execute_job(Path(workspace), job, provider, model, base_url, runner, store=store)
        error = None if result["status"] == "passed" else "Factory validation did not pass"
        store.finish(job["id"], result=result, error=error)
        LOGGER.info("Job %s finished: %s", job["id"], result["status"])
    except Exception as exc:
        # Full details stay on the host log; API errors do not disclose host paths or service URLs.
        LOGGER.exception("Job %s failed", job["id"])
        store.finish(job["id"], error=f"{type(exc).__name__}: job failed; inspect worker logs")
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    return True


def main():
    parser = argparse.ArgumentParser(description="算法工厂后台任务 worker，建议在 Jetson 宿主机运行")
    parser.add_argument("--workspace", type=Path, default=Path(os.getenv("FACTORY_WORKSPACE", ".")))
    parser.add_argument("--once", action="store_true", help="处理一个任务后退出，供演示和测试")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    workspace = arguments.workspace.resolve()
    provider = os.getenv("FACTORY_PROVIDER", "ollama")
    model = os.getenv("OLLAMA_CHAT_MODEL", "qwen3.5:2b" if provider == "ollama" else "mock-template-v1")
    base_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    runner = os.getenv("FACTORY_RUNNER", "docker" if provider == "ollama" else "local")
    if provider not in {"mock", "ollama"} or runner not in {"local", "docker"}:
        parser.error("Invalid FACTORY_PROVIDER or FACTORY_RUNNER")
    if provider == "ollama" and runner != "docker":
        parser.error("The API worker requires Docker for real generated code")
    store = JobStore(workspace)
    stop = threading.Event()
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda signum, frame: stop.set())
    while not stop.is_set():
        processed = process_one(store, workspace, provider, model, base_url, runner)
        if arguments.once:
            break
        if not processed:
            stop.wait(1)


if __name__ == "__main__":
    main()

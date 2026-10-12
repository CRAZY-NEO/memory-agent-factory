"""Real HTTP/container smoke test with a private ephemeral token and mock host worker.

Prerequisite: docker build -f Dockerfile.api -t agent-factory-api:0.1 .
The temporary container and workspace are removed; no API remains exposed afterwards.
"""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore

ROOT = Path(__file__).resolve().parents[1]


def docker(*arguments, **kwargs):
    return subprocess.run(["docker", *arguments], capture_output=True, text=True,
                          check=True, timeout=30, **kwargs).stdout.strip()


def main():
    name = "factory-api-smoke-" + uuid.uuid4().hex[:8]
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="factory-api-smoke-") as directory:
        workspace = Path(directory)
        for folder in ["data", "knowledge"]:
            shutil.copytree(ROOT / folder, workspace / folder)
        shutil.copyfile(ROOT / "config.json", workspace / "config.json")
        (workspace / "runs").mkdir()
        (workspace / "state" / "jobs").mkdir(parents=True)
        try:
            docker("run", "-d", "--rm", "--name", name, "--read-only", "--cap-drop=ALL",
                   "--security-opt=no-new-privileges", "--memory=512m", "--pids-limit=64",
                   "--user", f"{os.getuid()}:{os.getgid()}",
                   "-p", "127.0.0.1::8000", "--env", "FACTORY_API_TOKEN",
                   "--env", "FACTORY_WORKSPACE=/workspace", "--tmpfs", "/tmp:size=16m",
                   "-v", f"{workspace / 'state' / 'jobs'}:/workspace/state/jobs",
                   "-v", f"{workspace / 'runs'}:/workspace/runs:ro", "agent-factory-api:0.1",
                   env={**os.environ, "FACTORY_API_TOKEN": token})
            address = docker("port", name, "8000").splitlines()[0]
            with httpx.Client(base_url="http://" + address, timeout=10, trust_env=False) as client:
                for _ in range(40):
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("API container failed to start: " + docker("logs", name))
                headers = {"Authorization": "Bearer " + token}
                assert client.post("/v1/knowledge/ingest").status_code == 401
                response = client.post("/v1/factory/runs", headers=headers,
                                       json={"description": "生成中文技术记忆检索算法，返回前 5 条"})
                assert response.status_code == 202, response.text
                store = JobStore(workspace)
                assert process_one(store, workspace)
                job = client.get(response.json()["status_url"], headers=headers).json()
                assert job["status"] == "succeeded", job
                report = client.get(job["result"]["report_url"], headers=headers)
                assert report.status_code == 200
                response = client.post("/v1/memory/search", headers=headers,
                                       json={"query": "MySQL 连接池耗尽", "top_k": 1})
                assert process_one(store, workspace)
                search = client.get(response.json()["status_url"], headers=headers).json()
                assert search["result"]["hits"][0]["id"] == "m01"
                evidence = {"gateway": "Docker ARM64 Linux; actual HTTP requests",
                            "worker_provider": "mock", "worker_runner": "local",
                            "authentication": "unauthorized request rejected",
                            "factory_job": job["status"], "search_job": search["status"],
                            "top_result": "m01", "report_available": True,
                            "jetson_tested": False}
                (ROOT / "examples" / "api_validation.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(evidence, ensure_ascii=False, indent=2))
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False)


if __name__ == "__main__":
    main()

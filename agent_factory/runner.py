"""Bounded subprocess/Docker transport; generated code never receives relevance labels."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from .safety import inspect_code


class Runner:
    def __init__(self, mode="local", timeout=30, image="agent-factory-runner:0.1"):
        if mode not in {"local", "docker"}:
            raise ValueError("runner must be local or docker")
        self.mode, self.timeout, self.image = mode, timeout, image

    def execute(self, code, cases, task="memory_retrieval", contract_version='1'):
        errors = inspect_code(code, task=task)
        if errors:
            return {"error": "; ".join(errors), "wall_seconds": 0, "runner": self.mode}
        from .tasks import task_spec
        adapter = task_spec(task, contract_version)
        adapter.validate_cases(cases)
        invocation = {key: adapter.contract.model_dump()[key]
                      for key in ('entrypoint', 'parameters', 'defaults')}
        return self._execute(code, cases, task, invocation)

    def execute_draft(self, code, inputs):
        """Fixed JSON-in/JSON-out envelope; no claim of business correctness."""
        errors = inspect_code(code, task='python_draft')
        if errors:
            return {'error': '; '.join(errors), 'runner': self.mode}
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 16 or any(not isinstance(row, dict) for row in inputs):
            raise ValueError('Draft inputs must be 1–16 JSON objects')
        if len(json.dumps(inputs, allow_nan=False)) > 100_000:
            raise ValueError('Draft input budget exceeded')
        cases = [{'case_id': str(i), 'payload': value} for i, value in enumerate(inputs)]
        return self._execute(code, cases, 'python_draft', {'entrypoint': 'execute', 'parameters': ['payload'], 'defaults': {}})

    def _execute(self, code, cases, task, invocation):
        start = time.perf_counter()
        container = "factory-" + uuid.uuid4().hex
        if self.mode == "local":
            command = [sys.executable, "-I", str(Path(__file__).with_name("worker.py"))]
        else:
            command = ["docker", "run", "--rm", "--name", container, "--network=none",
                       "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                       "--pids-limit=32", "--memory=2g", "--memory-swap=2g", "--cpus=1",
                       "--user=65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                       "-i", self.image]
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                       "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                       "MKL_NUM_THREADS": "1", "PYTHONHASHSEED": "0"}
        # Permit Docker client connection configuration; never forward API keys to workers.
        for key in ["DOCKER_HOST", "DOCKER_CONTEXT"]:
            if key in os.environ:
                environment[key] = os.environ[key]
        with tempfile.TemporaryDirectory(prefix="factory-worker-") as temporary:
            with open(Path(temporary) / "stdout", "w+b") as stdout, open(Path(temporary) / "stderr", "w+b") as stderr:
                process = None
                try:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout,
                                               stderr=stderr, cwd=temporary, env=environment,
                                               start_new_session=True)
                    try:
                        process.communicate(json.dumps({"code": code, "cases": cases, "task": task, "invocation": invocation},
                                                      ensure_ascii=False).encode(), timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate()
                        return {"error": "runner timeout", "wall_seconds": time.perf_counter() - start,
                                "runner": self.mode}
                    stdout.seek(0)
                    raw = stdout.read(2 * 1024 * 1024 + 1)
                    stderr.seek(0)
                    diagnostic = stderr.read(4000).decode(errors="replace")
                    if len(raw) > 2 * 1024 * 1024:
                        return {"error": "worker output exceeds limit", "runner": self.mode}
                    try:
                        result = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        result = {"error": f"worker exited {process.returncode}: {diagnostic}"}
                    if process.returncode and "error" not in result:
                        result = {"error": f"worker exited {process.returncode}: {diagnostic}"}
                    result.update(wall_seconds=time.perf_counter() - start, runner=self.mode)
                    return result
                except OSError as exc:
                    return {"error": f"runner unavailable: {exc}", "runner": self.mode}
                finally:
                    if self.mode == "docker" and process is not None:
                        subprocess.run(["docker", "rm", "-f", container], env=environment,
                                       capture_output=True, timeout=10, check=False)

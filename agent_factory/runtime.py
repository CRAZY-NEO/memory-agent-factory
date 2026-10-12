"""Reuse an approved retrieval artifact from a future personal-memory application."""

import json
from pathlib import Path

from .knowledge import digest
from .models import Memory
from .runner import Runner
from .validation import check_result


class MemoryRetriever:
    """Plugin adapter loads the frozen artifact, verifies its hash and runs bounded retrieval."""

    def __init__(self, manifest, runner=None):
        manifest = Path(manifest)
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        if self.manifest["task"] != "memory_retrieval" or self.manifest["interface_version"] != "1":
            raise ValueError("Unsupported plugin interface")
        self.code = (manifest.parent / "selected_retrieval.py").read_text(encoding="utf-8")
        if digest(self.code) != self.manifest["code_hash"]:
            raise ValueError("Plugin hash mismatch: generated code was changed")
        self.runner = Runner(runner or self.manifest.get("runner", "docker"))

    def retrieve(self, query, memories, top_k=5):
        if not isinstance(query, str) or not isinstance(top_k, int) or isinstance(top_k, bool):
            raise ValueError("query must be str and top_k must be int")
        records = [Memory.model_validate(memory).model_dump() for memory in memories]
        if len({row["id"] for row in records}) != len(records):
            raise ValueError("Memory IDs must be unique")
        case = {"case_id": "query", "query": query, "memories": records, "top_k": top_k}
        result = self.runner.execute(self.code, [case])
        if "error" in result:
            raise RuntimeError(result["error"])
        return check_result(result["observations"][0], case)

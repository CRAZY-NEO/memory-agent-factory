"""Authenticated asynchronous API. The gateway enqueues jobs and never executes generated code."""

import json
import os
import re
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from .jobs import JobStore, QueueFull
from .models import Contract, Memory

RUN_PATTERN = r"^\d{8}T\d{6}_[0-9a-f]{8}$"


class FactorySubmission(Contract):
    description: str = Field(min_length=5, max_length=6000)


class APIMemory(Memory):
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)
    source: str = Field(min_length=1, max_length=2048)


class SearchSubmission(Contract):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    run_id: str | None = Field(default=None, pattern=RUN_PATTERN)
    memories: list[APIMemory] | None = Field(default=None, min_length=1, max_length=500)


class BodyLimit:
    """Bound raw request size, including chunked requests, before JSON/model parsing."""
    def __init__(self, app, limit=1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        parts, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            parts.append(message)
            size += len(message.get("body", b""))
            if size > self.limit:
                response = JSONResponse({"detail": "Request body exceeds 1 MiB"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        index = 0

        async def replay():
            nonlocal index
            if index < len(parts):
                message = parts[index]
                index += 1
                return message
            return await receive()
        return await self.app(scope, replay, send)


def create_app(workspace=None, token=None):
    """Uvicorn factory entrypoint; require an explicit private bearer token at startup."""
    workspace = Path(workspace or os.getenv("FACTORY_WORKSPACE", ".")).resolve()
    token = token or os.getenv("FACTORY_API_TOKEN", "")
    if len(token) < 24 or token.startswith("CHANGE_ME"):
        raise RuntimeError("Set FACTORY_API_TOKEN to a private random token of at least 24 characters")
    store = JobStore(workspace)
    app = FastAPI(title="Technical Memory Algorithm Factory", version="0.1.0")
    app.add_middleware(BodyLimit)

    def authenticate(authorization: str = Header(default="")):
        scheme, separator, value = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not secrets.compare_digest(value.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="Invalid bearer token",
                                headers={"WWW-Authenticate": "Bearer"})

    def submit(kind, payload):
        try:
            uid = store.enqueue(kind, payload)
        except QueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"job_id": uid, "status": "queued", "status_url": f"/v1/jobs/{uid}"}

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "agent-factory-gateway"}

    @app.post("/v1/factory/runs", status_code=202, dependencies=[Depends(authenticate)])
    def generate(body: FactorySubmission):
        return submit("factory", body.model_dump())

    @app.post("/v1/knowledge/ingest", status_code=202, dependencies=[Depends(authenticate)])
    def ingest():
        return submit("ingest", {})

    @app.post("/v1/memory/search", status_code=202, dependencies=[Depends(authenticate)])
    def search(body: SearchSubmission):
        return submit("search", body.model_dump())

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(authenticate)])
    def job(job_id: str):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        result = store.get(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return result

    @app.get("/v1/runs/{run_id}/report", dependencies=[Depends(authenticate)])
    def report(run_id: str):
        if not re.fullmatch(RUN_PATTERN, run_id):
            raise HTTPException(status_code=404, detail="Run not found")
        path = workspace / "runs" / run_id / "report.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Report not ready")
        # Remove host workspace paths from user-facing API responses.
        content = path.read_text(encoding="utf-8")
        content = re.sub(r'"code_path":\s*"[^"]*"', '"code_path": "artifact"', content)
        return json.loads(content)

    return app

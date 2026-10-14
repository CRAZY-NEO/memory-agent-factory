"""Authenticated asynchronous API. The gateway enqueues jobs and never executes generated code."""

import json
import os
import re
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .demo_session import DemoSession
from .jobs import JobStore, QueueFull
from .models import Contract, Memory

RUN_PATTERN = r"^\d{8}T\d{6}_[0-9a-f]{8}$"


class FactorySubmission(Contract):
    description: str = Field(min_length=5, max_length=6000)
    mode: Literal['auto', 'reuse', 'discover'] = 'auto'


class BrowserOpening(Contract):
    ticket: str = Field(min_length=24, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')


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


def create_app(workspace=None, token=None, bootstrap_ticket=None):
    """Uvicorn factory entrypoint; require an explicit private bearer token at startup."""
    workspace = Path(workspace or os.getenv("FACTORY_WORKSPACE", ".")).resolve()
    token = token or os.getenv("FACTORY_API_TOKEN", "")
    if len(token) < 24 or token.startswith("CHANGE_ME"):
        raise RuntimeError("Set FACTORY_API_TOKEN to a private random token of at least 24 characters")
    store = JobStore(workspace)
    app = FastAPI(title="Technical Memory Algorithm Factory", version="0.1.0")
    opening = bootstrap_ticket if bootstrap_ticket is not None else os.getenv('FACTORY_BOOTSTRAP_TICKET', '')
    if opening and not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', opening):
        raise RuntimeError('Invalid browser opening ticket')
    session = DemoSession(opening) if opening else None
    app.add_middleware(BodyLimit)
    web = Path(__file__).with_name('web')
    app.mount('/assets', StaticFiles(directory=web), name='assets')

    @app.middleware('http')
    async def browser_headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        if request.url.path == '/' or request.url.path.startswith('/assets/'):
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(web / 'index.html', media_type='text/html')

    def same_origin(request):
        expected = str(request.base_url).rstrip('/')
        if request.headers.get('origin') != expected:
            raise HTTPException(status_code=403, detail='Browser session requires a same-origin request')

    def authenticate(request: Request, authorization: str = Header(default="")):
        scheme, separator, value = authorization.partition(" ")
        if authorization:
            if separator and scheme.lower() == 'bearer' and secrets.compare_digest(value.encode(), token.encode()):
                return
        elif session and session.valid(request.cookies.get(session.cookie_name)):
            if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
                same_origin(request)
            return
        raise HTTPException(status_code=401, detail="Invalid bearer token or browser session",
                            headers={"WWW-Authenticate": "Bearer"})

    @app.post('/v1/session/bootstrap', include_in_schema=False)
    def open_browser(body: BrowserOpening, request: Request):
        if session is None:
            raise HTTPException(status_code=404, detail='Automatic browser login is disabled')
        same_origin(request)
        cookie = session.redeem(body.ticket)
        if cookie is None:
            raise HTTPException(status_code=401, detail='Browser opening ticket expired or already used')
        response = JSONResponse({'status': 'connected'})
        response.set_cookie(session.cookie_name, cookie, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https', path='/v1/')
        return response

    @app.get('/v1/session', include_in_schema=False)
    def browser_session(request: Request):
        if session is None or not session.valid(request.cookies.get(session.cookie_name)):
            raise HTTPException(status_code=401, detail='No active browser session')
        return {'status': 'connected'}

    @app.delete('/v1/session', include_in_schema=False)
    def close_browser(request: Request):
        same_origin(request)
        if session and session.valid(request.cookies.get(session.cookie_name)):
            session.revoke()
        response = JSONResponse({'status': 'disconnected'})
        response.delete_cookie(DemoSession.cookie_name, path='/v1/')
        return response

    def submit(kind, payload):
        try:
            uid = store.enqueue(kind, payload)
        except QueueFull as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"job_id": uid, "status": "queued", "status_url": f"/v1/jobs/{uid}"}

    def run_directory(run_id):
        if not re.fullmatch(RUN_PATTERN, run_id):
            raise HTTPException(status_code=404, detail='Run not found')
        root = (workspace / 'runs').resolve()
        directory = (root / run_id).resolve()
        if not directory.is_relative_to(root) or not directory.is_dir():
            raise HTTPException(status_code=404, detail='Run not found')
        return directory

    def artifact(run_id, relative, filename):
        directory = run_directory(run_id)
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(status_code=404, detail='Artifact not ready')
        return FileResponse(path, filename=filename, media_type='text/plain')

    def progress(record):
        record['progress'] = {'stage': record['status'], 'events': []}
        if not record.get('run_id'):
            return record
        try:
            directory = run_directory(record['run_id'])
            path = (directory / 'workflow.jsonl').resolve()
            events = []
            if path.is_relative_to(directory) and path.exists():
                for line in path.read_text(encoding='utf-8').splitlines()[-200:]:
                    try:
                        event = json.loads(line)
                        events.append({key: event.get(key) for key in ['node', 'time', 'candidate_index', 'revision']})
                    except json.JSONDecodeError:
                        # A poll can observe a partially written last event.
                        continue
            stage = events[-1]['node'] if events else 'prepare'
            record['progress'] = {'stage': stage, 'events': events}
        except HTTPException:
            pass
        return record

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "agent-factory-gateway"}

    @app.get('/v1/settings', dependencies=[Depends(authenticate)])
    def settings():
        config = json.loads((workspace / 'config.json').read_text(encoding='utf-8'))
        metadata = workspace / 'data' / 'dataset.json'
        dataset = json.loads(metadata.read_text(encoding='utf-8')) if metadata.exists() else None
        return {'algorithms': config.get('algorithms', []), 'metrics': config.get('metrics', {}),
                'dataset': dataset, 'discovery_available': os.getenv('FACTORY_PROVIDER', 'mock') == 'ollama'}

    @app.post('/v1/algorithms/discover', status_code=202, dependencies=[Depends(authenticate)])
    def discover_algorithm(body: FactorySubmission):
        if os.getenv('FACTORY_PROVIDER', 'mock') != 'ollama':
            raise HTTPException(status_code=400, detail='GitHub algorithm discovery requires Ollama mode')
        return submit('discovery', {'description': body.description})

    @app.get('/v1/jobs', dependencies=[Depends(authenticate)])
    def recent_jobs(limit: int = Query(default=20, ge=1, le=100)):
        return {'jobs': store.recent(limit)}

    @app.get('/v1/runs', dependencies=[Depends(authenticate)])
    def recent_runs(limit: int = Query(default=20, ge=1, le=100)):
        records = []
        root = workspace / 'runs'
        for directory in sorted(root.glob('*'), reverse=True):
            if not re.fullmatch(RUN_PATTERN, directory.name):
                continue
            try:
                run = run_directory(directory.name)
                path = (run / 'report.json').resolve()
                if not path.is_relative_to(run) or not path.exists():
                    continue
                content = json.loads(path.read_text(encoding='utf-8'))
            except (HTTPException, json.JSONDecodeError):
                continue
            selected = content.get('selected') or {}
            records.append({'run_id': directory.name, 'status': content['status'],
                            'provider': content['provider'], 'model': content['model'],
                            'algorithm': selected.get('algorithm')})
            if len(records) >= limit:
                break
        return {'runs': records}

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
        return progress(result)

    @app.get("/v1/runs/{run_id}/report", dependencies=[Depends(authenticate)])
    def report(run_id: str):
        path = run_directory(run_id) / 'report.json'
        if not path.resolve().is_relative_to(run_directory(run_id)):
            raise HTTPException(status_code=404, detail='Report not found')
        if not path.exists():
            raise HTTPException(status_code=404, detail="Report not ready")
        # Remove host workspace paths from user-facing API responses.
        content = path.read_text(encoding="utf-8")
        content = re.sub(r'"code_path":\s*"[^"]*"', '"code_path": "artifact"', content)
        return json.loads(content)

    @app.get('/v1/runs/{run_id}/artifacts/{filename}', dependencies=[Depends(authenticate)])
    def download(run_id: str, filename: str):
        if filename not in {'selected_retrieval.py', 'plugin.json', 'report.json', 'report.md',
                            'routing.json', 'search_results.json', 'proposal.json', 'source_context.json', 'source_contract.json', 'extracted_repository.json'}:
            raise HTTPException(status_code=404, detail='Artifact not found')
        if filename == 'report.json':
            return JSONResponse(report(run_id), headers={'Content-Disposition': 'attachment; filename="report.json"'})
        return artifact(run_id, filename, filename)

    @app.get('/v1/runs/{run_id}/code/{algorithm}/{revision}', dependencies=[Depends(authenticate)])
    def candidate_code(run_id: str, algorithm: str, revision: int):
        if not re.fullmatch(r'[a-z][a-z0-9_]*', algorithm) or not 0 <= revision <= 3:
            raise HTTPException(status_code=404, detail='Candidate not found')
        return artifact(run_id, f'{algorithm}/r{revision}/retrieval.py', f'{algorithm}-r{revision}.py')

    return app

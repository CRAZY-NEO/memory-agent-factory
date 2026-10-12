import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore, QueueFull

TOKEN = "test-token-used-only-in-tests-0123456789"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def test_token_required_at_startup(workspace):
    with pytest.raises(RuntimeError, match="FACTORY_API_TOKEN"):
        create_app(workspace, token="short")


def test_unauthorized_bad_input_body_limit_and_docs(workspace):
    client = TestClient(create_app(workspace, TOKEN))
    assert client.get("/health").status_code == 200
    assert client.post("/v1/factory/runs", json={"description": "检索个人技术记忆"}).status_code == 401
    assert client.post("/v1/memory/search", headers=HEADERS,
                       json={"query": "x", "top_k": 0}).status_code == 422
    assert client.post("/v1/memory/search", headers=HEADERS,
                       json={"query": "x", "run_id": "../../invalid"}).status_code == 422
    assert client.post("/v1/factory/runs", headers=HEADERS,
                       content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert "/v1/factory/runs" in client.get("/openapi.json").json()["paths"]


def test_api_queue_worker_factory_and_search_end_to_end(workspace):
    client = TestClient(create_app(workspace, TOKEN))
    response = client.post("/v1/factory/runs", headers=HEADERS,
                           json={"description": "为个人技术记忆生成检索算法，返回前 5 条"})
    assert response.status_code == 202
    uid = response.json()["job_id"]
    job = client.get(f"/v1/jobs/{uid}", headers=HEADERS).json()
    assert job["status"] == "queued" and "payload" not in job
    store = JobStore(workspace)
    assert process_one(store, workspace)
    job = client.get(f"/v1/jobs/{uid}", headers=HEADERS).json()
    assert job["status"] == "succeeded" and job["result"]["status"] == "passed"
    report = client.get(job["result"]["report_url"], headers=HEADERS)
    assert report.status_code == 200 and str(workspace) not in report.text
    response = client.post("/v1/memory/search", headers=HEADERS,
                           json={"query": "MySQL 连接池耗尽", "top_k": 1})
    assert process_one(store, workspace)
    query_job = client.get(response.json()["status_url"], headers=HEADERS).json()
    assert query_job["status"] == "succeeded"
    assert query_job["result"]["hits"][0]["id"] == "m01"


def test_worker_failed_job_and_missing_job(workspace):
    store = JobStore(workspace)
    uid = store.enqueue("search", {"query": "数据库", "top_k": 5, "run_id": None})
    assert process_one(store, workspace)
    record = store.get(uid)
    assert record["status"] == "failed" and str(workspace) not in record["error"]
    client = TestClient(create_app(workspace, TOKEN))
    assert client.get("/v1/jobs/" + "0" * 32, headers=HEADERS).status_code == 404


def test_queue_budget_single_worker_and_expired_lease(workspace):
    store = JobStore(workspace)
    first = store.enqueue("ingest", {}, max_pending=2)
    second = store.enqueue("ingest", {}, max_pending=2)
    with pytest.raises(QueueFull):
        store.enqueue("ingest", {}, max_pending=2)
    assert store.claim()["id"] == first
    assert JobStore(workspace).claim() is None
    store.heartbeat(first)
    with store.connect() as db:
        db.execute("UPDATE jobs SET updated_at=0 WHERE id=?", (first,))
    assert store.claim()["id"] == second
    assert store.get(first)["status"] == "failed"
    store.finish(second, result={"status": "passed"})
    with pytest.raises(RuntimeError, match="lease"):
        store.finish(second, result={})


def test_overfull_api_queue_returns_429(workspace):
    store = JobStore(workspace)
    for _ in range(20):
        store.enqueue("ingest", {})
    client = TestClient(create_app(workspace, TOKEN))
    assert client.post("/v1/knowledge/ingest", headers=HEADERS).status_code == 429

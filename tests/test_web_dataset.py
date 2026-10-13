import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.models import ValidationConfig
from agent_factory.validation import analyze_query, load_dataset, validate
from scripts.create_demo_data import build_dataset

ROOT = Path(__file__).parents[1]
TOKEN = 'local-web-test-key-01234567890123456789'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


def test_dataset_rebuild_references_and_new_holdout(workspace):
    for name, records in build_dataset().items():
        assert records == json.loads((ROOT / 'data' / name).read_text())
    memories, validation = load_dataset(workspace / 'data', 'validation')
    _, test = load_dataset(workspace / 'data', 'test')
    assert len(memories) > len(json.loads((ROOT / 'examples/datasets/v1/memories.json').read_text()))
    assert {'paraphrase', 'hard_negative', 'multi_relevance'} <= {q['category'] for q in validation}
    assert {'paraphrase', 'hard_negative', 'multi_relevance'} <= {q['category'] for q in test}
    assert not {q['query'] for q in validation} & {q['query'] for q in test}
    legacy = json.loads((ROOT / 'examples/datasets/v1/test_queries.json').read_text())
    assert not {q['query'] for q in legacy} & {q['query'] for q in test}
    assert any(len(q['relevant_ids']) > 1 for q in validation)
    assert all(q['annotation'] for q in validation + test)


@pytest.mark.parametrize('negative', [['missing'], ['m01', 'm01'], ['m01']])
def test_bad_negative_labels_fail_before_execution(workspace, negative):
    path = workspace / 'data' / 'validation_queries.json'
    rows = json.loads(path.read_text())
    rows[0]['hard_negative_ids'] = negative
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='negative'):
        load_dataset(workspace / 'data', 'validation')


def test_group_metrics_and_failures_are_hand_checkable():
    memories = [{'id': uid, 'text': uid, 'source': 'test://'} for uid in ['a', 'b', 'c']]
    queries = [{'id': 'q1', 'query': 'one', 'relevant_ids': ['a'], 'hard_negative_ids': ['b'], 'category': 'hard_negative'},
               {'id': 'q2', 'query': 'two', 'relevant_ids': ['a', 'c'], 'category': 'multi_relevance'}]

    class FixedRunner:
        def execute(self, code, cases):
            rows = []
            for case in cases:
                query = case['query']
                if not query.strip() or not case['memories'] or case['top_k'] <= 0:
                    hits = []
                elif query == 'one':
                    hits = [{'id': 'b', 'score': 2}, {'id': 'a', 'score': 1}]
                elif query == 'two':
                    hits = [{'id': 'a', 'score': 2}, {'id': 'b', 'score': 1}]
                else:
                    hits = [{'id': 'a', 'score': 1}]
                rows.append({'case_id': case['case_id'], 'result': hits[:case['top_k']], 'mutated': False, 'duration_ms': 1})
            return {'observations': rows}

    config = ValidationConfig(metrics={'recall_at_k': {}, 'mrr_at_k': {}, 'precision_at_k': {}})
    report = validate('unused', FixedRunner(), memories, queries, 2, config)
    first, second = report['per_query']
    assert first['failure_types'] == ['ranking_error', 'hard_negative_confusion']
    assert second['missed_relevant_ids'] == ['c']
    # Gates apply globally; category measurements remain diagnostic.
    assert report['metrics'] == {'recall_at_k': .75, 'mrr_at_k': .75, 'precision_at_k': .5}
    assert report['by_category']['hard_negative']['metrics']['mrr_at_k'] == .5
    assert report['by_category']['multi_relevance']['metrics']['recall_at_k'] == .5
    assert report['by_category']['hard_negative']['hard_negative_confusion_count'] == 1
    assert analyze_query(queries[0], [])['failure_types'] == ['missed_relevance']


def test_existing_queue_migration_preserves_jobs(workspace):
    path = workspace / 'state/jobs/jobs.sqlite'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,kind TEXT,payload TEXT,status TEXT,created_at REAL,updated_at REAL,result TEXT,error TEXT)')
        db.execute("INSERT INTO jobs VALUES ('old','ingest','{}','queued',1,1,NULL,NULL)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(JobStore, [workspace, workspace]))
    store = stores[0]
    assert store.get('old')['status'] == 'queued' and store.get('old')['run_id'] is None
    uid = store.enqueue('factory', {'description': '检索技术记忆'})
    assert store.get(uid)['run_id'] is None
    with pytest.raises(RuntimeError, match='unclaimed'):
        store.attach_run(uid, 'invalid')


def test_web_assets_authentication_progress_and_downloads(workspace):
    client = TestClient(create_app(workspace, TOKEN))
    page = client.get('/')
    assert page.status_code == 200 and '算法能力工厂' in page.text
    assert "script-src 'self'" in page.headers['Content-Security-Policy']
    assert client.get('/assets/app.js').status_code == 200
    for path in ['/v1/settings', '/v1/jobs', '/v1/runs']:
        assert client.get(path).status_code == 401
    settings = client.get('/v1/settings', headers=HEADERS).json()
    assert settings['dataset']['memory_count'] == len(json.loads((workspace / 'data/memories.json').read_text()))
    assert 'test_queries' not in json.dumps(settings) and TOKEN not in json.dumps(settings)
    queued = client.post('/v1/factory/runs', json={'description': '生成技术记忆检索算法'}, headers=HEADERS).json()
    assert process_one(JobStore(workspace), workspace)
    job = client.get(queued['status_url'], headers=HEADERS).json()
    assert job['status'] == 'succeeded' and job['run_id']
    assert job['progress']['events'][-1]['node'] == 'persist'
    assert str(workspace) not in json.dumps(job)
    assert client.get('/v1/jobs', headers=HEADERS).json()['jobs'][0]['id'] == queued['job_id']
    report = client.get(job['result']['report_url'], headers=HEADERS).json()
    uid = job['run_id']
    assert client.get('/v1/runs', headers=HEADERS).json()['runs'][0]['run_id'] == uid
    for name in ['selected_retrieval.py', 'report.json', 'report.md', 'plugin.json']:
        url = f'/v1/runs/{uid}/artifacts/{name}'
        assert client.get(url).status_code == 401
        download = client.get(url, headers=HEADERS)
        assert download.status_code == 200 and 'attachment;' in download.headers['Content-Disposition']
        assert download.headers['X-Content-Type-Options'] == 'nosniff'
        if name == 'report.json':
            assert str(workspace) not in download.text
    attempt = report['attempts'][0]
    assert client.get(f"/v1/runs/{uid}/code/{attempt['algorithm']}/{attempt['revision']}", headers=HEADERS).status_code == 200
    assert client.get(f'/v1/runs/{uid}/artifacts/llm.json', headers=HEADERS).status_code == 404
    assert client.get('/v1/runs?limit=1000', headers=HEADERS).status_code == 422


def test_live_progress_and_partial_event_read(workspace):
    store = JobStore(workspace)
    uid = store.enqueue('factory', {})
    store.claim()
    run_id = '20261014T000000_01234567'
    directory = workspace / 'runs' / run_id
    directory.mkdir(parents=True)
    (directory / 'workflow.jsonl').write_text('{"node":"validate","candidate_index":1,"revision":0}\n{"node":')
    store.attach_run(uid, run_id)
    client = TestClient(create_app(workspace, TOKEN))
    job = client.get(f'/v1/jobs/{uid}', headers=HEADERS).json()
    assert job['status'] == 'running' and job['progress']['stage'] == 'validate'
    assert len(job['progress']['events']) == 1


def test_symlink_artifacts_cannot_escape_run(workspace, tmp_path):
    directory = workspace / 'runs' / '20261014T000000_01234567'
    directory.mkdir(parents=True)
    outside = tmp_path / 'private.txt'
    outside.write_text('private information')
    (directory / 'report.json').symlink_to(outside)
    (directory / 'selected_retrieval.py').symlink_to(outside)
    client = TestClient(create_app(workspace, TOKEN))
    for suffix in ['report', 'artifacts/selected_retrieval.py']:
        assert client.get(f'/v1/runs/{directory.name}/{suffix}', headers=HEADERS).status_code == 404

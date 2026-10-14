"""Task-aware interfaces preserve authentication and export only frozen releases."""

import io
import json
import shutil
import time
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from agent_factory.api import create_app
from agent_factory.cli import main
from agent_factory.job_worker import process_one
from agent_factory.jobs import JobStore
from agent_factory.models import ValidationConfig
from agent_factory.tasks import load_task_config
from agent_factory.workflow import Factory

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'task-tests-private-bearer-01234567890123'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


@pytest.fixture(scope='module')
def completed_tasks(tmp_path_factory):
    workspace = tmp_path_factory.mktemp('task-api')
    for directory in ['data', 'knowledge']:
        shutil.copytree(ROOT / directory, workspace / directory)
    shutil.copyfile(ROOT / 'config.json', workspace / 'config.json')
    retrieval = Factory(workspace, config=ValidationConfig(algorithms=['bm25'], max_candidates=1))
    assert retrieval.run('生成技术记忆检索算法')['status'] == 'passed'
    # IDs contain second-resolution time plus a random suffix; establish a later
    # timestamp so the latest-search check exercises a newer classification run.
    time.sleep(1)
    client = TestClient(create_app(workspace, TOKEN))
    submitted = client.post('/v1/factory/runs', headers=HEADERS,
                            json={'description': '根据训练样本生成文本记忆分类算法，对技术笔记预测类别'})
    assert submitted.status_code == 202
    assert process_one(JobStore(workspace), workspace)
    job = client.get(submitted.json()['status_url'], headers=HEADERS).json()
    assert job['status'] == 'succeeded', job
    return workspace, retrieval.run_id, job['run_id']


def test_classification_api_routes_validates_and_downloads(completed_tasks):
    workspace, _, run_id = completed_tasks
    client = TestClient(create_app(workspace, TOKEN))
    report = client.get(f'/v1/runs/{run_id}/report', headers=HEADERS).json()
    assert report['request']['task'] == 'memory_classification'
    assert report['status'] == 'passed' and report['test']['passed']
    assert {'accuracy', 'macro_f1'} <= report['test']['metrics'].keys()
    records = client.get('/v1/runs', headers=HEADERS).json()['runs']
    assert next(row for row in records if row['run_id'] == run_id)['task'] == 'memory_classification'
    settings = client.get('/v1/settings', headers=HEADERS).json()
    assert settings['classification_dataset']
    assert not any(row['text'] in json.dumps(settings, ensure_ascii=False)
                   for row in json.loads((workspace / 'data/classification/test.json').read_text()))
    selected = report['selected']
    prefix = f'/v1/runs/{run_id}'
    code = client.get(prefix + f"/code/{selected['algorithm']}/{selected['revision']}", headers=HEADERS)
    assert code.status_code == 200 and 'def classify' in code.text
    assert client.get(prefix + '/artifacts/selected_classification.py', headers=HEADERS).status_code == 200
    assert client.get(prefix + '/artifacts/training_examples.json', headers=HEADERS).status_code == 404
    assert client.get(prefix + '/artifacts/selected_retrieval.py', headers=HEADERS).status_code == 404


def test_zip_endpoint_authentication_and_training_disclosure(completed_tasks):
    workspace, _, run_id = completed_tasks
    client = TestClient(create_app(workspace, TOKEN))
    url = f'/v1/runs/{run_id}/export'
    assert client.post(url).status_code == 401
    response = client.post(url, headers=HEADERS)
    assert response.status_code == 200 and response.headers['content-type'] == 'application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    with ZipFile(io.BytesIO(response.content)) as bundle:
        manifest = json.loads(bundle.read('plugin.json'))
        assert manifest['task'] == 'memory_classification' and manifest['includes_training_text']
        assert 'training_examples.json' in bundle.namelist()
        assert not {'test.json', 'validation.json'} & set(bundle.namelist())
        assert '分类包包含' in bundle.read('README.md').decode()
    # Export is a state-changing same-origin request for browser-cookie sessions.
    ticket = 'test-opening-ticket-01234567890123456789'
    browser = TestClient(create_app(workspace, TOKEN, bootstrap_ticket=ticket))
    assert browser.post('/v1/session/bootstrap', json={'ticket': ticket},
                        headers={'Origin': 'http://testserver'}).status_code == 200
    assert browser.post(url).status_code == 403
    assert browser.post(url, headers={'Origin': 'http://testserver'}).status_code == 200


def test_latest_search_ignores_classification_run(completed_tasks):
    workspace, retrieval_id, classification_id = completed_tasks
    assert classification_id > retrieval_id
    client = TestClient(create_app(workspace, TOKEN))
    submitted = client.post('/v1/memory/search', headers=HEADERS,
                            json={'query': 'MySQL 连接池耗尽', 'top_k': 1})
    assert process_one(JobStore(workspace), workspace)
    job = client.get(submitted.json()['status_url'], headers=HEADERS).json()
    assert job['status'] == 'succeeded'
    assert job['result']['run_id'] == retrieval_id
    assert job['result']['hits'][0]['id'] == 'm01'


def test_cli_export_and_task_validation(completed_tasks, tmp_path, capsys):
    workspace, _, classification_id = completed_tasks
    output = tmp_path / 'classification.zip'
    assert main(['--workspace', str(workspace), 'export', '--run-id', classification_id,
                 '--output', str(output)]) == 0
    assert output.is_file() and str(output) in capsys.readouterr().out
    code = workspace / 'runs' / classification_id / 'selected_classification.py'
    assert main(['--workspace', str(workspace), 'validate', '--task', 'memory_classification',
                 '--code', str(code), '--runner', 'local']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['passed'] and 'macro_f1' in result['metrics']


def test_export_refuses_tampered_code_and_unknown_runs(completed_tasks, tmp_path):
    source, _, run_id = completed_tasks
    shutil.copytree(source / 'runs' / run_id, tmp_path / 'runs' / run_id)
    code = tmp_path / 'runs' / run_id / 'selected_classification.py'
    code.write_text(code.read_text() + '\n# changed after validation\n')
    client = TestClient(create_app(tmp_path, TOKEN))
    rejected = client.post(f'/v1/runs/{run_id}/export', headers=HEADERS)
    assert rejected.status_code == 409 and str(tmp_path) not in rejected.text
    assert client.post('/v1/runs/20261015T000000_00000000/export', headers=HEADERS).status_code == 404


@pytest.fixture
def failed_classification_run(workspace):
    config = load_task_config(workspace, 'memory_classification').model_copy(update={
        'algorithms': ['tfidf_nb'], 'max_candidates': 1, 'max_repairs': 0})
    factory = Factory(workspace, config=config, inject_fault=True)
    result = factory.run('生成技术笔记文本分类算法')
    assert result['status'] == 'failed_validation'
    report = json.loads((factory.directory / 'report.json').read_text())
    assert not report['attempts'][0]['validation']['passed']
    return factory.directory


def test_cli_replays_classification_failure_with_correct_profile(workspace, failed_classification_run, capsys):
    # No --config: the workspace default is retrieval, but historical evidence is classification.
    assert main(['--workspace', str(workspace), 'run', '--provider', 'mock', '--runner', 'local',
                 '--description', '修复并验证技术笔记文本分类算法',
                 '--repair-from', str(failed_classification_run)]) == 0
    result = json.loads(capsys.readouterr().out)
    report = json.loads(Path(result['report']).with_suffix('.json').read_text())
    assert report['request']['task'] == 'memory_classification' and report['test']['passed']
    assert report['validation_config']['task'] == 'memory_classification'
    attempts = [row for row in report['attempts'] if row['algorithm'] == 'tfidf_nb']
    assert attempts[0]['generation']['strategy'] == 'historical_replay'
    assert attempts[0]['generation']['source_run'] == failed_classification_run.name
    assert not attempts[0]['validation']['passed']
    assert attempts[1]['validation']['passed']
    assert not report['mock_fault_injection']


def test_cli_replay_rejects_explicit_task_conflict(workspace, failed_classification_run, capsys):
    before = set((workspace / 'runs').iterdir())
    assert main(['--workspace', str(workspace), '--config', str(workspace / 'config.json'),
                 'run', '--description', '修复技术笔记文本分类算法',
                 '--repair-from', str(failed_classification_run)]) == 1
    assert 'different tasks' in capsys.readouterr().err
    assert set((workspace / 'runs').iterdir()) == before


def test_cli_replay_refuses_router_switching_to_retrieval(workspace, failed_classification_run, capsys):
    before = set((workspace / 'runs').iterdir())
    assert main(['--workspace', str(workspace), 'run', '--provider', 'mock',
                 '--description', '生成技术笔记检索算法', '--repair-from', str(failed_classification_run)]) == 1
    assert capsys.readouterr().err
    new_runs = set((workspace / 'runs').iterdir()) - before
    assert len(new_runs) == 1
    rejected = new_runs.pop()
    assert (rejected / 'error.json').exists()
    assert not (rejected / 'report.json').exists()
    assert not (rejected / 'selected_retrieval.py').exists()


def test_cli_replay_rejects_cross_task_historical_candidate(workspace, failed_classification_run, capsys):
    path = failed_classification_run / 'report.json'
    report = json.loads(path.read_text())
    report['attempts'][0]['algorithm'] = 'bm25'
    path.write_text(json.dumps(report))
    assert main(['--workspace', str(workspace), 'run', '--description', '修复技术笔记文本分类算法',
                 '--repair-from', str(failed_classification_run)]) == 1
    assert 'incompatible with its task' in capsys.readouterr().err

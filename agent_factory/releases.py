"""Load only hash-verified algorithm manifests; generated registration code is never imported."""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from .safety import inspect_code


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_releases(registry, workspace):
    from .registry import AlgorithmSpec

    root = (Path(workspace) / 'state' / 'algorithm_registry').resolve()
    index = root / 'active.json'
    if not index.exists():
        return
    if index.is_symlink() or index.stat().st_size > 65536:
        raise ValueError('Unsafe algorithm registry index')
    active = json.loads(index.read_text(encoding='utf-8'))
    if not isinstance(active, dict) or len(active) > 32:
        raise ValueError('Invalid algorithm registry index')
    for identity, version in active.items():
        if not re.fullmatch(r'github_[a-z0-9_]+', identity) or not re.fullmatch(r'[0-9A-Za-z_-]{1,64}', version):
            raise ValueError('Invalid external algorithm identity/version')
        directory = root / identity / version
        manifest = directory / 'manifest.json'
        if manifest.is_symlink() or not manifest.resolve().is_relative_to(root) or manifest.stat().st_size > 65536:
            raise ValueError('Unsafe external algorithm manifest')
        record = json.loads(manifest.read_text(encoding='utf-8'))
        if record['status'] != 'approved' or record['id'] != identity or record['version'] != version:
            raise ValueError('External algorithm is not approved')
        if not record.get('license_hashes'):
            raise ValueError('External algorithm source license is missing')
        for name, expected in record['license_hashes'].items():
            if Path(name).name != name or name in {'.', '..'}:
                raise ValueError('Invalid source license path')
            path = directory / 'source-licenses' / name
            if path.is_symlink() or not path.resolve().is_relative_to(root) or file_hash(path) != expected:
                raise ValueError('External algorithm source license hash mismatch')
        for name in ['reference.py', 'material.md', 'verification.json']:
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(root) or file_hash(path) != record['hashes'][name]:
                raise ValueError('External algorithm artifact hash mismatch')
        proof = json.loads((directory / 'verification.json').read_text(encoding='utf-8'))
        if (not proof['validation']['passed'] or not proof['test']['passed']
                or proof['test']['split'] != 'test' or proof['code_hash'] != record['hashes']['reference.py']
                or proof['runner'] != 'docker'):
            raise ValueError('External algorithm lacks passing isolated validation and final test')
        code = (directory / 'reference.py').read_text(encoding='utf-8')
        if inspect_code(code):
            raise ValueError('External algorithm no longer satisfies source safety policy')
        registry.add_algorithm(AlgorithmSpec(
            id=identity, name=record['name'], version=version, task='memory_retrieval',
            material=directory / 'material.md', reference=directory / 'reference.py',
            guidance=record['guidance'], dependencies=tuple(record['dependencies']),
            public_cases=tuple(record.get('public_cases', [])),
            provenance=record['source']))


def approve_algorithm(workspace, spec, code, validation, test, source, run_id, provider, license_documents):
    """Stage an immutable version, then atomically expose it to registry readers."""
    if not validation['passed'] or not test['passed'] or test['split'] != 'test':
        raise ValueError('Cannot approve an algorithm before passing both evaluation stages')
    if any(report.get('execution', {}).get('runner') != 'docker' for report in [validation, test]):
        raise ValueError('Automatic approval requires Docker execution evidence')
    if inspect_code(code):
        raise ValueError('Cannot approve unsafe algorithm source')
    root = Path(workspace) / 'state' / 'algorithm_registry'
    version = run_id
    directory = root / spec.id / version
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'reference.py').write_text(code, encoding='utf-8')
    (directory / 'material.md').write_text(spec.material.read_text(encoding='utf-8'), encoding='utf-8')
    if not license_documents:
        raise ValueError('External algorithm release must preserve source license documents')
    license_hashes = {}
    for name, text in license_documents.items():
        if Path(name).name != name or name in {'.', '..'}:
            raise ValueError('Invalid source license filename')
        path = directory / 'source-licenses' / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding='utf-8')
        license_hashes[name] = file_hash(path)
    proof = {'validation': validation, 'test': test, 'code_hash': file_hash(directory / 'reference.py'),
             'run_id': run_id, 'provider': provider, 'runner': 'docker',
             'method_fidelity': 'source-grounded model adaptation; no independent mathematical equivalence proof'}
    atomic_json(directory / 'verification.json', proof)
    record = {'id': spec.id, 'name': spec.name, 'version': version, 'status': 'approved',
              'task': 'memory_retrieval', 'guidance': spec.guidance, 'dependencies': list(spec.dependencies),
              'public_cases': list(spec.public_cases),
              'source': source, 'run_id': run_id, 'license_hashes': license_hashes,
              'hashes': {name: file_hash(directory / name) for name in ['reference.py', 'material.md', 'verification.json']}}
    atomic_json(directory / 'manifest.json', record)
    # A single workspace's job queue serializes normal promotions. The lock also
    # serializes concurrent CLI promotions and prevents lost index/config updates.
    import fcntl

    with (root / '.promotion.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = root / 'active.json'
        active = json.loads(index.read_text()) if index.exists() else {}
        active[spec.id] = version
        if len(active) > 32:
            raise ValueError('External algorithm registry exceeds 32 algorithm budget')
        atomic_json(index, active)
        config_path = Path(workspace) / 'config.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        enabled = config.get('algorithms', [])
        if spec.id not in enabled and len(enabled) < 8:
            config['algorithms'] = enabled + [spec.id]
            config['max_candidates'] = max(config.get('max_candidates', 2), len(config['algorithms']))
            atomic_json(config_path, config)
        record['enabled'] = spec.id in config['algorithms']
    return record

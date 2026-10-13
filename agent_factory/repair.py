"""Trusted failure localization, compact feedback and historical repair replay."""

import ast
import hashlib
import json
from pathlib import Path


def code_fingerprint(code):
    """Ignore comments and docstrings; never execute code while checking progress."""
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.body and isinstance(node.body[0], ast.Expr):
                    value = node.body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        node.body.pop(0)
        text = ast.dump(tree, include_attributes=False)
    except SyntaxError:
        text = code
    return hashlib.sha256(text.encode()).hexdigest()


def static_findings(code):
    """Conservative local evidence for set indexing, without claiming general type inference."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    findings = []

    def local_nodes(node):
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                yield from local_nodes(child)

    scopes = [tree, *(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    for scope in scopes:
        nodes = list(local_nodes(scope))
        assignments = {}
        for node in nodes:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node.value)
        known_sets = {name for name, values in assignments.items() if all(
            isinstance(value, ast.Set) or (isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name) and value.func.id == 'set') for value in values)}
        findings.extend({'kind': 'set_subscript', 'line': node.lineno,
                         'source': ast.get_source_segment(code, node),
                         'detail': '局部静态检查发现集合赋值与下标访问并存；结合错误行确认。相邻双字应从原文字符串切片。'}
                        for node in nodes if isinstance(node, ast.Subscript)
                        and isinstance(node.value, ast.Name) and node.value.id in known_sets)
    return findings


def validation_feedback(report):
    """Preserve distinct errors and execution evidence, excluding gold labels and repeated cases."""
    return {'passed': report['passed'], 'split': report['split'],
            'errors': list(dict.fromkeys(error.split(': ', 1)[1] if error.startswith(('v', 'contract:'))
                                        and ': ' in error else error for error in report['errors']))[:12],
            'diagnostics': report.get('diagnostics', [])[:6], 'metrics': report['metrics']}


def focused_context(state, registry):
    candidate = state['plan']['candidates'][state['candidate_index']]
    plugin = registry.algorithm(candidate['algorithm'])
    knowledge = state['knowledge']
    capabilities = [cap for cap in knowledge['capabilities'] if cap['id'] == candidate['capability_id']]
    source_ids = {cap['source_id'] for cap in capabilities}
    reference = plugin.reference.read_text(encoding='utf-8')
    return {'request': state['request'], 'candidate': candidate,
            'algorithm_plugin': plugin.metadata(), 'capabilities': capabilities,
            'sources': [source for source in knowledge['sources'] if source['source_id'] in source_ids],
            # Unverified historic advice is available to the planner, not treated as code instructions.
            'validated_lessons': [lesson for lesson in knowledge.get('lessons', [])
                                 if lesson.get('algorithm') == plugin.id and lesson.get('validated_fix')][:3],
            'reference_example': {'algorithm': plugin.id, 'source': f'plugin:{plugin.id}@{plugin.version}',
                                  'sha256': hashlib.sha256(reference.encode()).hexdigest(), 'code': reference}}


def load_failed_codes(directory, enabled_algorithms):
    """Replay only hash-verified failed revisions from a user-selected local report."""
    directory = Path(directory).resolve()
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    latest = {}
    for attempt in report['attempts']:
        name, revision = attempt['algorithm'], attempt['revision']
        if name not in enabled_algorithms:
            continue
        if not isinstance(revision, int) or isinstance(revision, bool) or not 0 <= revision <= 3:
            raise ValueError('Invalid historical revision')
        if name not in latest or revision > latest[name]['revision']:
            latest[name] = attempt
    seeds = {}
    for name, attempt in latest.items():
        if attempt['validation']['passed']:
            continue
        source = (directory / name / f"r{attempt['revision']}" / 'retrieval.py').resolve()
        if not source.is_relative_to(directory):
            raise ValueError('Historical code path escapes the selected run')
        code = source.read_text(encoding='utf-8')
        checksum = hashlib.sha256(code.encode()).hexdigest()
        if checksum != attempt['code_hash']:
            raise ValueError('Historical code hash mismatch: ' + name)
        seeds[name] = {'code': code, 'source_run': report['run_id'], 'source_revision': attempt['revision'],
                       'source_hash': checksum}
    if not seeds:
        raise ValueError('No failed enabled candidates in the selected report')
    return seeds

"""Trusted failure localization, compact feedback and historical repair replay."""

import ast
import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path

REPAIR_RULE_EXTRACTOR_VERSION = 'observed-diff-v3'


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


def import_findings(code, task='memory_retrieval'):
    """Explain the same AST import policy as the safety gate without executing code."""
    from .safety import CLASSIFICATION_IMPORTS, allowed_imports
    ALLOWED_IMPORTS = allowed_imports(task)

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    findings = []
    allowed = sorted(ALLOWED_IMPORTS | (set(CLASSIFICATION_IMPORTS) if task == 'memory_classification' else set()))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            invalid = [alias.name for alias in node.names if alias.name not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed:
                invalid = [node.module or '(relative import)']
            elif task == 'memory_classification' and node.module in CLASSIFICATION_IMPORTS:
                invalid = [node.module + '.' + alias.name for alias in node.names
                           if alias.name not in CLASSIFICATION_IMPORTS[node.module]]
            else:
                invalid = [node.module + '.' + alias.name for alias in node.names if alias.name.startswith('_')]
        else:
            continue
        if invalid:
            findings.append({'kind': 'forbidden_import', 'line': node.lineno,
                             'source': ast.get_source_segment(code, node),
                             'detail': 'Remove unsupported import(s): ' + ', '.join(invalid)
                                       + '. Allowed modules: ' + ', '.join(allowed)
                                       + '. Classifier modules use from-import of LogisticRegression or MultinomialNB only. '
                                       'A classifier random_state parameter does not require importing random.'})
    return findings


def _feedback_error(error):
    """Keep an observed sklearn exception without echoing its training label."""
    return re.sub(r'(but the data contains only one class:).*', r'\1 <training label omitted>',
                  error, flags=re.DOTALL)


def validation_feedback(report):
    """Retain every distinct failure with its public contract/case identifier.

    Removing ``contract:blank`` erased which input condition needed repair, and
    a first-12 slice silently dropped later business requirements. The validator
    already bounds case counts; deduplicate exact messages, not their source IDs.
    Only diagnostics and aggregate metrics accompany errors, never probe inputs,
    individual predictions or expected labels.
    """
    diagnostics = [{**item, 'error': _feedback_error(item['error'])}
                   for item in report.get('diagnostics', [])]
    return {'passed': report['passed'], 'split': report['split'],
            'errors': list(dict.fromkeys(_feedback_error(error) for error in report['errors'])),
            'diagnostics': diagnostics, 'metrics': report['metrics']}


def repair_feedback_report(state):
    """Recover current-run execution evidence after an unchanged response rejection.

    The returned report still records that the current artifact was not executed.
    Prior failures are reusable only for a verified identical implementation in
    the same candidate directory. AST-only equality does not preserve source line
    locations, so its traceback frames are deliberately omitted.
    """
    current = state.get('validation', {})
    generation = state.get('generation', {})
    if (current.get('executed') is not False or current.get('split') != 'validation'
            or 'transport' in {current.get('failure_kind'), generation.get('failure_kind')}
            or generation.get('failure_kind', current.get('failure_kind')) != 'response_contract'):
        return deepcopy(current)
    code = state.get('code', '')
    if not isinstance(code, str) or not code.strip():
        return deepcopy(current)
    try:
        ast.parse(code)
        candidate = state['plan']['candidates'][state['candidate_index']]['algorithm']
    except (SyntaxError, KeyError, IndexError, TypeError):
        return deepcopy(current)
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    semantic_hash = code_fingerprint(code)
    attempts = state.get('attempts', [])
    # Anchor reads to the current artifact's factory-recorded candidate directory,
    # never to a path supplied by model output or a previous unrelated run.
    anchor = next((row for row in reversed(attempts)
                   if row.get('algorithm') == candidate and row.get('code_hash') == code_hash
                   and row.get('validation') == current and row.get('code_path')), None)
    if anchor is None:
        return deepcopy(current)
    try:
        anchor_path = Path(anchor['code_path']).resolve()
        candidate_directory = anchor_path.parent.parent
    except (OSError, TypeError, ValueError):
        return deepcopy(current)
    for attempt in reversed(attempts):
        report = attempt.get('validation', {})
        if (attempt.get('algorithm') != candidate or report.get('split') != 'validation'
                or report.get('passed') is not False or report.get('executed') is False
                or attempt.get('revision', anchor['revision']) >= anchor['revision']):
            continue
        try:
            path = Path(attempt['code_path']).resolve()
            if path.parent.parent != candidate_directory or path.name != anchor_path.name:
                continue
            previous = path.read_text(encoding='utf-8')
            previous_hash = hashlib.sha256(previous.encode()).hexdigest()
            if previous_hash != attempt['code_hash']:
                continue
            ast.parse(previous)
        except (KeyError, OSError, TypeError, ValueError, SyntaxError):
            continue
        if code_fingerprint(previous) != semantic_hash:
            continue
        exact = previous_hash == code_hash
        diagnostics = [{key: deepcopy(item[key]) for key in ('error', 'frames', 'count') if key in item}
                       for item in report.get('diagnostics', [])]
        for diagnostic in diagnostics:
            if 'frames' in diagnostic:
                diagnostic['frames'] = [{key: frame[key] for key in ('line', 'function', 'source') if key in frame}
                                        for frame in diagnostic['frames']]
        if not exact:
            for diagnostic in diagnostics:
                diagnostic.pop('frames', None)
        result = {key: deepcopy(current[key]) for key in ('task', 'passed', 'split', 'executed',
                  'errors', 'metrics', 'diagnostics', 'gates', 'failure_kind') if key in current}
        result['errors'] = list(dict.fromkeys([*report.get('errors', []), *current.get('errors', [])]))
        result['diagnostics'] = diagnostics
        if 'requirement_checks' in report:
            checks = report['requirement_checks']
            result['requirement_checks'] = {key: deepcopy(checks[key]) for key in
                ('passed', 'split', 'requirements', 'case_count', 'errors') if key in checks}
            if 'details' in checks:
                result['requirement_checks']['details'] = [
                    {key: row[key] for key in ('requirement', 'check', 'passed') if key in row}
                    for row in checks['details']]
        result['feedback_origin'] = {
            'implementation_id': attempt.get('implementation_id'),
            'revision': attempt['revision'], 'code_hash': previous_hash,
            'semantic_hash': semantic_hash, 'match': 'exact_source' if exact else 'same_ast',
            'frames_retained': exact,
        }
        return result
    return deepcopy(current)


def no_observable_progress(state, max_repairs):
    """Return audited evidence of a stalled final repair, otherwise ``None``.

    This is deliberately weaker than behavioral equivalence: two verified
    executions showed the same failures and no quality gain. Empty metrics on
    both sides count only as unavailable observations, never as equal quality.
    Runtime measurements and reviewer advice do not influence this decision.
    """
    from .safety import inspect_code

    quality_ids = {
        'memory_classification': {'accuracy', 'macro_f1'},
        'memory_retrieval': {'recall_at_k', 'mrr_at_k', 'precision_at_k'},
    }
    runtime_ids = {'p95_ms', 'classification_p95_ms'}
    task = state.get('request', {}).get('task')
    revision = state.get('revision')
    if (task not in quality_ids or type(max_repairs) is not int or max_repairs < 2
            or type(revision) is not int or revision != max_repairs):
        return None
    try:
        algorithm = state['plan']['candidates'][state['candidate_index']]['algorithm']
        rows = [row for row in state['attempts'] if row.get('algorithm') == algorithm]
        latest = rows[-1]
    except (KeyError, IndexError, TypeError):
        return None

    def executed(row):
        report = row.get('validation', {})
        execution = report.get('execution', {})
        return (report.get('split') == 'validation' and report.get('executed') is not False
                and not report.get('failure_kind') and not row.get('generation', {}).get('failure_kind')
                and row.get('generation', {}).get('artifact_accepted') is not False
                and isinstance(execution, dict) and execution.get('runner') in {'local', 'docker'}
                and 'error' not in execution and isinstance(execution.get('wall_seconds'), (int, float))
                and not isinstance(execution['wall_seconds'], bool)
                and math.isfinite(execution['wall_seconds']) and execution['wall_seconds'] > 0)

    if (not executed(latest) or latest.get('revision') != revision - 1
            or latest.get('validation') != state.get('validation')
            or state.get('generation', {}).get('failure_kind')):
        return None
    previous = next((row for row in reversed(rows[:-1]) if executed(row)), None)
    if previous is None:
        return None
    pair = [previous, latest]
    reports = [row['validation'] for row in pair]
    if any(report.get('passed') is not False for report in reports):
        return None
    gates = reports[0].get('gates')
    if (not isinstance(gates, dict) or gates != reports[1].get('gates')
            or gates.get('task') != task or not isinstance(gates.get('metrics'), dict)):
        return None
    configured = set(gates['metrics'])
    quality = configured & quality_ids[task]
    if not quality or configured - quality_ids[task] - runtime_ids:
        return None  # New plugins need an explicit quality/runtime classification.

    metadata = reports[0].get('metric_plugins')
    if not isinstance(metadata, list) or metadata != reports[1].get('metric_plugins'):
        return None
    declarations = {item.get('id'): item for item in metadata if isinstance(item, dict)}
    if any(declarations.get(name, {}).get('direction') != 'higher'
           or declarations.get(name, {}).get('task') != task for name in quality):
        return None

    verified = []
    directory = None
    try:
        for row in pair:
            number = row['revision']
            if type(number) is not int or not 0 <= number < revision:
                return None
            path = Path(row['code_path']).resolve(strict=True)
            candidate_dir = path.parent.parent
            expected_name = 'classification.py' if task == 'memory_classification' else 'retrieval.py'
            if (path.name != expected_name or path.parent.name != f'r{number}'
                    or candidate_dir.name != algorithm or directory is not None and candidate_dir != directory):
                return None
            directory = candidate_dir
            expected_id = f'implementation_{candidate_dir.parent.name}_{algorithm}_r{number}'
            source = path.read_text(encoding='utf-8')
            checksum = hashlib.sha256(source.encode()).hexdigest()
            if (row['implementation_id'] != expected_id or checksum != row['code_hash']
                    or inspect_code(source, task=task)):
                return None
            verified.append({'implementation_id': expected_id, 'revision': number, 'code_hash': checksum})
        if previous['revision'] >= latest['revision'] or source != state.get('code'):
            return None
    except (KeyError, OSError, TypeError, ValueError):
        return None

    signatures = []
    for report in reports:
        errors = report.get('errors')
        checks = report.get('requirement_checks', {})
        if (not isinstance(errors, list) or any(not isinstance(error, str) for error in errors)
                or not isinstance(checks, dict)):
            return None
        configured_requirements = gates.get('requirements', [])
        if configured_requirements and (checks.get('requirements') != configured_requirements
                                        or 'details' not in checks):
            return None
        details = checks.get('details', [])
        if not isinstance(details, list):
            return None
        outcomes = {}
        for check in details:
            if (not isinstance(check, dict) or type(check.get('passed')) is not bool
                    or not isinstance(check.get('requirement'), str) or not isinstance(check.get('check'), str)):
                return None
            key = (check['requirement'], check['check'])
            if key in outcomes and outcomes[key] != check['passed']:
                return None
            outcomes[key] = check['passed']
        normalized = sorted({' '.join(_feedback_error(error).split()) for error in errors})
        failed = sorted(key for key, passed in outcomes.items() if not passed)
        if not normalized and not failed:
            return None
        signatures.append((normalized, failed))
    if signatures[0] != signatures[1]:
        return None

    values = [report.get('metrics') for report in reports]
    if any(not isinstance(value, dict) for value in values):
        return None
    if values == [{}, {}]:
        quality_values = [{}, {}]
        metric_status = 'unavailable_in_both_executions'
    else:
        quality_values = []
        for value in values:
            if set(value) != configured:
                return None
            selected = {name: value[name] for name in sorted(quality)}
            if any(type(number) not in {int, float} or not math.isfinite(number) for number in selected.values()):
                return None
            quality_values.append(selected)
        # Existing task quality metrics all have direction=higher. Do not guess
        # how to compare unknown plugins or resolve improvements/degradations.
        if any(quality_values[1][name] > quality_values[0][name] for name in quality):
            return None
        metric_status = 'no_quality_metric_improved'
    return {'reason': 'no_observable_progress',
            'interpretation': 'The last two executed validations showed identical failures and no observed quality improvement; this does not prove semantic equivalence.',
            'attempts': verified, 'normalized_errors': signatures[0][0],
            'failed_requirement_checks': [{'requirement': requirement, 'check': check}
                                          for requirement, check in signatures[0][1]],
            'quality_metrics': {'before': quality_values[0], 'after': quality_values[1], 'status': metric_status}}


# Public behavior specifications, not examples or implementations. These mirror
# the existing interface contracts; the evaluator remains the source of failure.
CLASSIFICATION_BASE_CONTRACTS = {
    'contract:empty': 'Empty prediction inputs must return an empty list, including when no effective training remains.',
    'contract:empty_all': 'Empty prediction inputs and empty training must return an empty list before fitting or selecting a fallback.',
    'contract:no_training': 'Nonempty prediction inputs with no effective training after required filtering/deduplication must raise ValueError.',
    'contract:blank': 'Keep every prediction input position, including whitespace-only strings. Return the effective training majority label at blank positions, with lexical label tie-breaking.',
    'contract:single_label': 'When effective training has one distinct label, return that label for every prediction input without fitting an estimator that requires multiple classes.',
    'contract:empty_vocabulary': 'If effective training contains no usable vocabulary, return its majority label for every prediction input; break label-count ties lexically.',
    'contract:repeat': 'Repeated calls with identical inputs must return identical predictions without reusing mutable global fitted state.',
    'contract:third': 'Repeated calls with identical inputs must return identical predictions without reusing mutable global fitted state.',
    'contract:reverse': 'Reordering prediction inputs must reorder the same predictions; fit only on training data.',
    'contract:duplicate': 'Repeated occurrences of the same prediction text must retain their positions and match the prediction in the original batch.',
    'contract:unicode': 'Support mixed Chinese/English and Unicode prediction strings while returning one training label per input.',
}
CLASSIFICATION_INTERFACE_CONTRACTS = {
    'output_cardinality': ('classify must return a list with one label per input text',
                           'Return a list with exactly one label per original prediction input, preserving order and duplicate/blank positions. Do not drop prediction texts.'),
    'output_labels': ('classify returned a non-string or unknown training label',
                     'Every returned value must be a string label drawn from the effective training examples; do not invent labels or return class indices.'),
    'input_immutability': ('classification input mutated or mutation flag missing',
                          'Do not mutate the supplied prediction list, training list or their rows; required preprocessing must use new containers.'),
}

CLASSIFICATION_FUNCTIONAL_FAILURES = {
    'functional: blank text fallback must be majority training label, ties sorted by label': 'contract:blank',
    'functional: single-label training must support arbitrary labels': 'contract:single_label',
    'functional: empty vocabulary must use the deterministic majority-label fallback': 'contract:empty_vocabulary',
}


def classification_failure_contracts(errors):
    """Attach code-free public behavior to observed base/interface failure IDs."""
    selected = {}

    def record(uid, description, source):
        item = selected.setdefault(uid, {'id': uid, 'description': description, 'failure_sources': []})
        if source not in item['failure_sources']:
            item['failure_sources'].append(source)

    for error in errors:
        source, separator, _ = error.partition(': ')
        if source in CLASSIFICATION_BASE_CONTRACTS:
            record(source, CLASSIFICATION_BASE_CONTRACTS[source], source)
        if error in CLASSIFICATION_FUNCTIONAL_FAILURES:
            uid = CLASSIFICATION_FUNCTIONAL_FAILURES[error]
            record(uid, CLASSIFICATION_BASE_CONTRACTS[uid], error)
        for name, (message, description) in CLASSIFICATION_INTERFACE_CONTRACTS.items():
            if message in error:
                record('interface:' + name, description, source if separator else 'interface')
    return list(selected.values())


def _prose(value, limit=600):
    """Reject code-bearing prose rather than trying to redact full implementations."""
    if not isinstance(value, str):
        return ''
    forbidden = ('```', 'def ', 'class ', 'import ', 'return ', 'lambda ', '\n    ')
    return '' if any(token in value for token in forbidden) else value[:limit]


def compact_lessons(knowledge, algorithm=None, task=None):
    """Use observed failed-to-passed outcomes, never unverified reviewer advice."""
    selected = []
    for lesson in knowledge.get('lessons', []):
        if not lesson.get('validated_fix') or (algorithm and lesson.get('algorithm') != algorithm):
            continue
        if task and lesson.get('scope') != task:
            continue
        evidence = lesson.get('evidence', {})
        rules = [text for item in lesson.get('repair_rules', []) if (text := _prose(item))]
        if not rules or not evidence.get('validation_id') or not evidence.get('prior_validation_id'):
            continue
        selected.append({'lesson_id': lesson.get('node_id'), 'algorithm': lesson['algorithm'],
                         'scope': lesson['scope'], 'rules': rules[:4],
                         'evidence': {key: evidence[key] for key in (
                             'validation_id', 'prior_validation_id', 'code_hash', 'prior_code_hash')
                             if key in evidence},
                         'interpretation': 'Observed revision passed validation; advice is not a causal guarantee.'})
    return selected[-6:]


def algorithm_specification(plugin):
    """Only prose/metadata is a specification; reject accidental implementation payloads."""
    metadata = plugin.metadata()
    selected = {key: metadata[key] for key in ('id', 'name', 'version', 'task', 'guidance', 'dependencies')
                if key in metadata}
    if re.search(r'\b(?:async\s+)?def\s+[A-Za-z_]\w*\s*\([^)]*\)\s*:', '\n'.join(str(value) for value in selected.values())):
        raise ValueError('Algorithm specification contains an implementation; use explicit reference mode')
    return selected


def planner_knowledge(knowledge, registry, config):
    """Prevent source text from entering plans and their free-form rationale."""
    if config.generation_mode == 'reference':
        context = dict(knowledge)
        context['repository_candidates'] = [
            {key: value for key, value in row.items() if key not in {'code', 'comments'}}
            for row in knowledge.get('repository_candidates', [])]
    else:
        context = {'capabilities': [{
            'id': cap['id'], 'algorithm': cap['algorithm'], 'task': cap['task'],
            'source_id': cap['source_id'],
            'evidence': _prose('；'.join(cap.get('applicable_conditions', []))) or 'Registered algorithm specification',
            'guidance': algorithm_specification(registry.algorithm(cap['algorithm']))['guidance'],
        } for cap in knowledge['capabilities']]}
    context['lessons'] = (compact_lessons(knowledge, task=config.task)
                          if config.history_mode == 'on' else [])
    context['historical_validations'] = ([
        {key: item[key] for key in ('node_id', 'passed', 'metrics') if key in item}
        for item in knowledge.get('historical_validations', [])]
        if config.history_mode == 'on' else [])
    return context


def focused_context(state, registry, config=None):
    from .models import ValidationConfig
    from .requirements import requirement_contracts

    config = config or ValidationConfig()
    candidate = state['plan']['candidates'][state['candidate_index']]
    plugin = registry.algorithm(candidate['algorithm'])
    knowledge = state['knowledge']
    selected_candidate = ({key: candidate[key] for key in ('algorithm', 'capability_id')}
                          if config.generation_mode == 'spec' else candidate)
    metadata = (algorithm_specification(plugin) if config.generation_mode == 'spec' else plugin.metadata())
    context = {'request': state['request'], 'candidate': selected_candidate,
               'algorithm_plugin': metadata, 'generation_mode': config.generation_mode,
               'history_mode': config.history_mode, 'model_seed': config.model_seed,
               'required_behaviors': requirement_contracts(config.task, config.requirements),
               'validated_lessons': (compact_lessons(knowledge, plugin.id, config.task)
                                     if config.history_mode == 'on' else [])}
    from .tasks import task_spec
    context['capability_contract'] = task_spec(config.task, config.contract_version).contract.model_dump()
    if config.generation_mode == 'reference':
        capabilities = [cap for cap in knowledge['capabilities'] if cap['id'] == candidate['capability_id']]
        source_ids = {cap['source_id'] for cap in capabilities}
        if plugin.reference is None:
            raise ValueError('This capability is specification-only and has no complete reference implementation')
        reference = plugin.reference.read_text(encoding='utf-8')
        context.update(capabilities=capabilities,
                       sources=[source for source in knowledge['sources'] if source['source_id'] in source_ids],
                       reference_example={'algorithm': plugin.id, 'source': f'plugin:{plugin.id}@{plugin.version}',
                                          'sha256': hashlib.sha256(reference.encode()).hexdigest(), 'code': reference})
        repository_candidates = [row for row in knowledge.get('repository_candidates', [])
                                 if row.get('algorithm') == plugin.id][:1]
        if repository_candidates:
            context['repository_candidates'] = repository_candidates
    return context


def repair_diagnosis(state):
    """Build repair targets from execution evidence and public contracts only.

    The reviewer's free-form diagnosis stays in ``diagnosis.json`` for inspection.
    Even without a static finding it is not a trusted source of repair instructions:
    a reviewer can invent missing operations or recommend removing correct ones.
    Requirement probe inputs and expected results never enter this context.
    """
    from .requirements import requirement_contracts

    code = state.get('code', '')
    task = state.get('request', {}).get('task', 'memory_retrieval')
    localized = static_findings(code) + import_findings(code, task)
    report = state.get('validation', {})
    if report.get('split', 'validation') != 'validation':
        raise ValueError('Repair feedback must come from validation, never the final test')
    feedback = validation_feedback({'passed': report.get('passed', False),
                                    'split': 'validation', 'errors': report.get('errors', []),
                                    'diagnostics': report.get('diagnostics', []),
                                    'metrics': report.get('metrics', {})})
    checks = report.get('requirement_checks', {})
    contracts = requirement_contracts(task, checks.get('requirements', []))
    failed_requirements = {row['requirement'] for row in checks.get('details', [])
                           if row.get('passed') is False}
    failed_requirements.update(contract['id'] for contract in contracts
                               if any(error.startswith(contract['id'] + ':')
                                      for error in checks.get('errors', [])))
    required_targets = [contract for contract in contracts if contract['id'] in failed_requirements]
    base_targets = classification_failure_contracts(feedback['errors']) if task == 'memory_classification' else []

    instructions = []
    if any(item['kind'] == 'set_subscript' for item in localized):
        instructions.extend([
            'Sets cannot be subscripted. Slice the original contiguous text fragments before adding tokens to sets.',
            'Preserve English whole-word tokens and valid set union/intersection operations; they are not the diagnosed failure.',
        ])
    instructions.extend(item['detail'] for item in localized if item['kind'] == 'forbidden_import')
    if feedback['diagnostics']:
        instructions.append('Correct the reported runtime exception at its cited expression. '
                            'The error does not establish that unrelated code is wrong.')
    if required_targets:
        instructions.append('Implement the failed public behavior contracts below. Apply their preprocessing '
                            'before the specified counting/fitting/scoring steps; preserve the algorithm itself.')
    if base_targets:
        instructions.append('Fix every failed basic contract below as well as the business requirements. '
                            'A repaired runtime expression alone does not resolve blank-input, single-label '
                            'or empty-vocabulary behavior; preserve all prediction input positions.')
    if feedback['errors']:
        instructions.append('Resolve the trusted validation failures without changing the interface, '
                            'algorithm, datasets, tests or metric gates. Preserve behavior that already passes.')
    if not instructions:
        instructions.append('No localized trusted failure is available. Check the implementation against '
                            'the supplied specification and required behaviors; do not assume the reviewer identified a bug.')
    return {
        'advice_policy': 'trusted_static_localization_only' if localized else 'trusted_validation_only',
        'summary': 'Repair targets derived from trusted static inspection, validation and public behavior contracts.',
        'trusted_findings': localized,
        'validation_errors': feedback['errors'],
        'execution_diagnostics': feedback['diagnostics'],
        'failed_required_behaviors': required_targets,
        'failed_base_behaviors': base_targets,
        'repair_instructions': instructions,
        'reviewer_advice_withheld': True,
        'reason': 'Free-form reviewer advice is unverified and remains in diagnosis.json only.',
    }


def observed_repair_rules(previous_code, code, previous_report, requirements=()):
    """Describe a verified passing revision without inferring why it passed.

    Callers must verify the revised implementation passed validation. A previously
    failing requirement identifies a revalidation outcome, not an implementation
    change: a runtime exception can mask behavior that was already implemented.
    """
    if code_fingerprint(previous_code) == code_fingerprint(code):
        return []
    errors = ' '.join(previous_report.get('errors', []))
    rules = []
    checks = previous_report.get('requirement_checks', {})
    failed_requirements = {row['requirement'] for row in checks.get('details', [])
                           if row.get('passed') is False and 'requirement' in row}
    requirement_errors = [*previous_report.get('errors', []), *checks.get('errors', [])]
    revalidated = list(dict.fromkeys(requirement for requirement in requirements
                                    if requirement in failed_requirements or any(
                                        error.startswith(requirement + ':') for error in requirement_errors)))
    outcomes = (['Previously failing requirement checks passed revalidation: ' + ', '.join(revalidated)
                 + '. This records the validation outcome only; it does not establish that requirement-specific '
                 'logic was added or identify the cause of the improvement.'] if revalidated else [])
    try:
        new = ast.parse(code)
    except SyntaxError:
        return []  # Invalid source cannot support a claim about a passing revision.
    try:
        old = ast.parse(previous_code)
    except SyntaxError:
        return ['A syntactically valid implementation replaced the malformed prior revision.', *outcomes]
    def imports(tree):
        return {node.module if isinstance(node, ast.ImportFrom) else alias.name
                for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    def bindings(tree):
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found.update({alias.asname or alias.name: f'{node.module}.{alias.name}'
                              for alias in node.names if alias.name != '*'})
            elif isinstance(node, ast.Import):
                found.update({alias.asname or alias.name.split('.')[0]: alias.name for alias in node.names})
        return found

    # Use only trusted runtime diagnostics, never the reviewer's free-form guess.
    diagnostic_text = errors + ' ' + ' '.join(row.get('error', '')
                                             for row in previous_report.get('diagnostics', []))
    missing = set(re.findall(r'''\bNameError:\s*name ['"]([A-Za-z_]\w*)['"] is not defined''', diagnostic_text))
    old_references = {node.id for node in ast.walk(old) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    new_references = {node.id for node in ast.walk(new) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    for name in sorted(missing & (old_references - new_references)):
        rules.append(f'A trusted NameError identified the undefined symbol {name}; AST comparison shows references '
                     f'to {name} were removed in the passing revision. This is an observed source change, '
                     'not proof that other implementation logic was missing or changed.')
    old_bindings, new_bindings = bindings(old), bindings(new)
    added_bindings = {name: origin for name, origin in new_bindings.items() if name not in old_bindings}
    fixed_names = sorted(missing & old_references & new_references & set(added_bindings))
    for name in fixed_names:
        rules.append(f'A trusted NameError identified the undefined symbol {name}; the passing revision added '
                     f'its dependency binding from {added_bindings[name]}. Bind referenced dependencies before use.')
    if fixed_names:
        accompanying = sorted((set(added_bindings) & old_references) - missing)
        if accompanying:
            details = ', '.join(f'{name} ({added_bindings[name]})' for name in accompanying)
            rules.append('The same passing revision also added previously referenced dependency bindings: '
                         + details + '. These changes accompanied the fix; only '
                         + ', '.join(fixed_names) + ' was identified by the original NameError.')
    from .safety import ALLOWED_IMPORTS, CLASSIFICATION_IMPORTS
    removed_imports = (imports(old) - imports(new)) - ALLOWED_IMPORTS - set(CLASSIFICATION_IMPORTS)
    if 'safety:' in errors and removed_imports:
        rules.append('Previously rejected imports were removed: ' + ', '.join(sorted(removed_imports))
                     + '. Use the configured allowlist and estimator parameters for deterministic behavior.')
    old_calls = {ast.unparse(node.func) for node in ast.walk(old) if isinstance(node, ast.Call)}
    new_calls = {ast.unparse(node.func) for node in ast.walk(new) if isinstance(node, ast.Call)}
    observations = {
        'normalize': 'Unicode normalization was introduced before matching or vectorization.',
        'casefold': 'Unicode case folding was introduced for case-insensitive text handling.',
        'strip': 'Leading and trailing whitespace handling was added before processing.',
        'sorted': 'Explicit deterministic ordering was added before selecting output.',
        'fit_transform': 'The revised implementation explicitly fits a feature representation.',
        'transform': 'The revised implementation explicitly transforms inputs with the fitted representation.',
    }
    for suffix, advice in observations.items():
        if any(call.split('.')[-1] == suffix for call in new_calls - old_calls):
            rules.append(advice)
    old_returns = [ast.dump(node.value) for node in ast.walk(old) if isinstance(node, ast.Return)]
    new_returns = [ast.dump(node.value) for node in ast.walk(new) if isinstance(node, ast.Return)]
    if old_returns != new_returns:
        rules.append('Output expressions or control paths changed; check output shape and boundary cases against the contract.')
    if any(item.get('kind') == 'set_subscript' for item in static_findings(previous_code)) and not static_findings(code):
        rules.append('Set indexing was removed; form adjacent text fragments from strings or sequences before converting to sets.')
    return rules[:6 - len(outcomes)] + outcomes


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
        from .tasks import task_spec
        filename = task_spec(report.get('request', {}).get('task', 'memory_retrieval')).filename
        source = (directory / name / f"r{attempt['revision']}" / filename).resolve()
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

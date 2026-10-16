"""Bounded role-agent workflow with trusted candidate selection and persistent provenance."""

import json
import logging
import platform
import shutil
import sys
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .knowledge import KnowledgeStore, digest, ingest_sources
from .llm import LLMClient, LLMError
from .models import CodeArtifact, Diagnosis, Plan, Request, ValidationConfig
from .providers import REAL_PROVIDERS, load_project_env, resolve_settings
from .registry import load_registry
from .repair import (
    algorithm_specification,
    code_fingerprint,
    focused_context,
    import_findings,
    no_observable_progress,
    observed_repair_rules,
    planner_knowledge,
    repair_diagnosis,
    repair_feedback_report,
    static_findings,
    validation_feedback,
)
from .reporting import write_report
from .requirements import requirement_contracts, validate_requirements
from .retrieval_scaffold import adapter_from_reference, compose_retrieval
from .runner import Runner
from .tasks import load_task_config, task_spec

LOGGER = logging.getLogger(__name__)


class FactoryState(TypedDict, total=False):
    description: str
    request: dict
    knowledge: dict
    plan: dict
    candidate_index: int
    revision: int
    code: str
    code_explanation: str
    generation_error: str | None
    generation: dict
    initial_codes: dict
    validation: dict
    diagnosis: dict
    attempts: list[dict]
    selected: dict | None
    test: dict | None
    status: str
    events: list[str]


class Factory:
    def __init__(self, workspace, provider="mock", model=None,
                 base_url=None, runner=None, inject_fault=False,
                 allow_local=False, config=None, registry=None):
        self.workspace = Path(workspace).resolve()
        load_project_env(self.workspace)
        model, base_url = resolve_settings(provider, model, base_url)
        self.config = config or ValidationConfig.model_validate(
            json.loads((self.workspace / "config.json").read_text(encoding="utf-8")))
        self.registry = registry or load_registry(self.config.plugin_packages, workspace=self.workspace)
        self.registry.validate_config(self.config)
        self.runner_mode = runner or ("docker" if provider in REAL_PROVIDERS else "local")
        if provider == "deepseek" and self.runner_mode != "docker":
            raise ValueError("DeepSeek 生成的代码必须使用 Docker 执行")
        if provider in REAL_PROVIDERS and self.runner_mode == "local" and not allow_local:
            raise ValueError("真实 LLM 代码请使用 Docker；若明确接受本地执行边界，添加 --allow-local")
        if inject_fault and provider != "mock":
            raise ValueError("故障注入只用于明确标注的 mock 演示")
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.directory = self.workspace / "runs" / self.run_id
        self.directory.mkdir(parents=True)
        self.client = LLMClient(provider, model, base_url, self.directory / "llm", inject_fault,
                                registry=self.registry, model_seed=self.config.model_seed,
                                fault_algorithm=self.config.algorithms[min(1, len(self.config.algorithms) - 1)])
        self.store = KnowledgeStore(self.workspace / "state" / "knowledge.sqlite")
        self.runner = Runner(self.runner_mode, self.config.timeout_seconds)
        self.environment = {"python": sys.version, "platform": platform.platform(),
                            "dependencies": {name: version(name) for name in
                                             ["langgraph", "pydantic", "scikit-learn", "networkx"]},
                            "runner": self.runner_mode,
                            "worker_sha256": digest(Path(__file__).with_name("worker.py").read_text()),
                            "docker_image": self.runner.image if self.runner_mode == "docker" else None}
        self.environment_id = "environment_" + digest(json.dumps(self.environment, sort_keys=True))

    @property
    def task(self):
        return task_spec(self.config.task)

    def activate_task(self, task):
        """Switch the validation adapter before generation, preserving the run and traces."""
        if task != self.config.task:
            policies = {key: getattr(self.config, key) for key in (
                'generation_mode', 'history_mode', 'model_seed')}
            self.config = load_task_config(self.workspace, task).model_copy(update=policies)
            requirement_contracts(task, self.config.requirements)
            self.registry = load_registry(self.config.plugin_packages, workspace=self.workspace)
            self.registry.validate_config(self.config)
            self.client.registry = self.registry
            self.runner.timeout = self.config.timeout_seconds
            self.client.fault_algorithm = self.config.algorithms[min(1, len(self.config.algorithms) - 1)]

    def _dataset(self, split):
        current = {name: digest((self.workspace / 'data' / name).read_text(encoding='utf-8'))
                   for name in self.task.data_files}
        if current != self.dataset_hashes:
            raise ValueError('Dataset changed during the run')
        inputs, examples = self.task.load(self.workspace / 'data', split, self.config)
        return self.task.frozen_inputs(self, inputs), examples

    def _prepare_data(self):
        requirement_contracts(self.config.task, self.config.requirements)
        if self.config.generation_mode == 'reference' and any(self.registry.algorithm(name).reference is None for name in self.config.algorithms):
            raise ValueError('This capability is specification-only and has no complete reference implementation')
        self.task.preflight(self.workspace / 'data', self.config)
        self.dataset_hashes = {name: digest((self.workspace / 'data' / name).read_text(encoding='utf-8'))
                               for name in self.task.data_files}
        self.task.prepare(self)
        from .contracts import contract_hash
        self.contract_snapshot = self.task.contract.model_dump()
        self.contract_hash = contract_hash(self.contract_snapshot)
        (self.directory / 'capability_contract.json').write_text(
            json.dumps(self.contract_snapshot, ensure_ascii=False, indent=2), encoding='utf-8')

    def _evaluate(self, code, request, algorithm, split):
        from .contracts import contract_hash
        saved = json.loads((self.directory / 'capability_contract.json').read_text(encoding='utf-8'))
        if (contract_hash(self.task.contract.model_dump()) != self.contract_hash
                or contract_hash(saved) != self.contract_hash):
            raise ValueError('Capability contract changed during the run')
        inputs, examples = self._dataset(split)
        result = self.task.evaluate(code, self.runner, inputs, examples, request, self.config,
                                    self.registry, algorithm, split)
        if self.config.requirements:
            checks = validate_requirements(code, self.runner, self.config.task,
                                           self.config.requirements, split=split)
            result['requirement_checks'] = checks
            result['errors'].extend(checks['errors'])
            result.setdefault('diagnostics', []).extend(checks['diagnostics'])
            result['passed'] = bool(result['passed'] and checks['passed'])
        return result

    def _wrap(self, name, function):
        def node(state):
            LOGGER.info("%s: %s", self.run_id, name)
            event = {"node": name, "time": datetime.now(timezone.utc).isoformat(),
                     "candidate_index": state.get("candidate_index"), "revision": state.get("revision")}
            with (self.directory / "workflow.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            update = function(state)
            return {**update, "events": state.get("events", []) + [name]}
        return node

    def _understand(self, state):
        request = self.client.ask("understander", {"description": state["description"],
                                                   "task": self.config.task,
                                                   "capability_contract": self.task.contract.model_dump()}, Request)
        if request.task != self.config.task:
            raise ValueError('Understood task differs from the selected validation profile')
        request.description = state["description"]
        return {"request": request.model_dump()}

    def _retrieve(self, state):
        knowledge = self.store.retrieve(state["description"], limit=max(4, len(self.config.algorithms)),
                                        algorithms=self.config.algorithms, include_history=self.config.history_mode == "on",
                                        task=self.config.task)
        (self.directory / "retrieved_knowledge.json").write_text(
            json.dumps(knowledge, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"knowledge": knowledge}

    def _plan(self, state):
        knowledge = planner_knowledge(state['knowledge'], self.registry, self.config)
        plan = self.client.ask("planner", {"request": state["request"], **knowledge,
                                          "generation_mode": self.config.generation_mode,
                                          "history_mode": self.config.history_mode,
                                          "required_behaviors": requirement_contracts(self.config.task, self.config.requirements),
                                          "algorithm_catalog": [algorithm_specification(self.registry.algorithm(name))
                                              if self.config.generation_mode == 'spec' else self.registry.algorithm(name).metadata()
                                              for name in self.config.algorithms],
                                          "capability_contract": self.task.contract.model_dump(),
                                          "max_candidates": self.config.max_candidates,
                                          "required_algorithms": self.config.algorithms
                                          if self.config.compare_all_algorithms else []}, Plan)
        if len(plan.candidates) > self.config.max_candidates:
            raise ValueError("Plan exceeds configured candidate budget")
        available = {cap["id"]: cap for cap in state["knowledge"]["capabilities"]}
        algorithms = []
        for candidate in plan.candidates:
            self.registry.algorithm(candidate.algorithm)
            if candidate.algorithm not in self.config.algorithms:
                raise ValueError("Plan references disabled algorithm")
            if candidate.capability_id not in available or available[candidate.capability_id]["algorithm"] != candidate.algorithm:
                raise ValueError("Plan references unsupported capability/algorithm")
            if candidate.algorithm in algorithms:
                raise ValueError("Plan must compare distinct algorithms")
            algorithms.append(candidate.algorithm)
        if self.config.compare_all_algorithms and set(algorithms) != set(self.config.algorithms):
            raise ValueError("Plan must include all enabled algorithms in comparison mode")
        (self.directory / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        return {"plan": plan.model_dump(), "candidate_index": 0, "revision": 0}

    def _generate(self, state):
        candidate = state["plan"]["candidates"][state["candidate_index"]]
        seed = state.get('initial_codes', {}).get(candidate['algorithm'])
        if state['revision'] == 0 and seed:
            return {'code': seed['code'], 'code_explanation': '重放已校验哈希的历史失败代码，等待真实模型修复',
                    'generation_error': None, 'generation': {'strategy': 'historical_replay',
                    'source_run': seed['source_run'], 'source_revision': seed['source_revision'],
                    'source_hash': seed['source_hash'], 'unchanged': False,
                    'backend': 'historical_replay', 'llm_code_generated': False}}
        context = focused_context(state, self.registry, self.config)
        plugin = self.registry.algorithm(candidate['algorithm'])
        adapter = (adapter_from_reference(plugin.reference.read_text(encoding='utf-8'))
                   if plugin.provenance and plugin.public_cases and not self.config.requirements else None)
        repository_ids = [row['id'] for row in context.get('repository_candidates', [])]
        role = 'coder'
        strategy = 'generate_from_spec' if self.config.generation_mode == 'spec' else 'reproduce_reference'
        if state['revision']:
            role = 'repairer'
            # Keep partial progress unless execution evidence shows a stall.
            stalled = state.get('generation', {}).get('unchanged', False)
            progress = no_observable_progress(state, self.config.max_repairs)
            rebuild = not state.get('code', '').strip() or (state['revision'] >= 2 and stalled) or bool(progress)
            strategy = ('rebuild_from_' + self.config.generation_mode) if rebuild else 'targeted_repair'
            feedback = repair_feedback_report(state)
            context.update(repair_strategy=strategy,
                           diagnosis=repair_diagnosis({**state, 'validation': feedback}),
                           validation=validation_feedback(feedback))
            if feedback.get('feedback_origin'):
                context['feedback_origin'] = feedback['feedback_origin']
            if progress:
                context['repair_progress'] = progress
            if strategy == 'targeted_repair':
                context['previous_code'] = state['code']
            else:
                # Retain evidence even when a stalled/empty implementation is discarded.
                context['failed_code_hash'] = digest(state['code'])
        context['repair_strategy'] = strategy
        input_evidence = {'reference_provided': 'reference_example' in context,
                          'history_lesson_ids': [lesson['lesson_id'] for lesson in context.get('validated_lessons', [])]}
        if context.get('repair_progress'):
            input_evidence['repair_progress'] = context['repair_progress']
        if adapter:
            # This wrapper is already complete. Reuse it directly instead of
            # counting an LLM echo of the same source as independent code generation.
            code = compose_retrieval(adapter['source_adapter_hint'], adapter['mode'], adapter['kernel_helpers'])
            return {'code': code, 'code_explanation': '确定性复用已验证来源内核与适配器，未调用模型生成代码',
                    'generation_error': None,
                    'generation': {'strategy': 'deterministic_source_reuse', 'backend': 'deterministic_source_adapter',
                                   'llm_code_generated': False, 'generation_mode': self.config.generation_mode,
                                   'unchanged': bool(state['revision'] and code_fingerprint(code) == code_fingerprint(state['code'])),
                                   'semantic_hash': code_fingerprint(code), 'source': plugin.provenance,
                                   'repository_candidate_ids': repository_ids}}
        try:
            artifact = self.client.ask(role, context, CodeArtifact)
            code = artifact.code
            unchanged = bool(state['revision'] and code_fingerprint(code) == code_fingerprint(state['code']))
            return {'code': code, 'code_explanation': artifact.explanation, 'generation_error': None,
                    'generation': {'strategy': strategy, 'unchanged': unchanged,
                                   'backend': 'mock_template_baseline' if self.client.provider == 'mock' else 'llm',
                                   'llm_code_generated': self.client.provider in REAL_PROVIDERS,
                                   **input_evidence,
                                   'generation_mode': self.config.generation_mode,
                                   'semantic_hash': code_fingerprint(code),
                                   'repository_candidate_ids': repository_ids}}
        except (LLMError, ValueError) as exc:
            code = getattr(exc, 'generated_code', '') or ''
            unchanged = bool(code and state['revision'] and code_fingerprint(code) == code_fingerprint(state['code']))
            return {'code': code, 'code_explanation': '', 'generation_error': str(exc),
                    'generation': {'strategy': strategy, 'unchanged': unchanged,
                                   'backend': 'mock_template_baseline' if self.client.provider == 'mock' else 'llm',
                                   'llm_code_generated': bool(code) and self.client.provider in REAL_PROVIDERS,
                                   'generation_mode': self.config.generation_mode,
                                   'artifact_accepted': False,
                                   'failure_kind': ('transport' if getattr(exc, 'transport_failure', False)
                                                    else 'response_contract'),
                                   'rejected_source_trace': getattr(exc, 'generated_code_trace', None),
                                   'response_protocol': getattr(exc, 'response_protocol', None),
                                   'semantic_hash': code_fingerprint(code),
                                   **input_evidence,
                                   'repository_candidate_ids': repository_ids}}

    def _validate(self, state):
        if state["generation_error"]:
            # Retain rejected/truncated source for diagnosis, but never execute
            # an artifact that failed the response contract or transport step.
            report = {'passed': False, 'split': 'validation', 'executed': False,
                      'errors': [state['generation_error']], 'metrics': {}, 'diagnostics': [],
                      'gates': self.config.model_dump(),
                      'failure_kind': state.get('generation', {}).get('failure_kind', 'response_contract')}
        else:
            report = self._evaluate(state['code'], state['request'],
                                    state['plan']['candidates'][state['candidate_index']]['algorithm'], 'validation')
        if state.get('generation', {}).get('unchanged'):
            report['errors'].append('repair: implementation unchanged apart from comments or docstrings')
            report['passed'] = False
        candidate = state["plan"]["candidates"][state["candidate_index"]]
        algorithm, revision = candidate["algorithm"], state["revision"]
        directory = self.directory / algorithm / f"r{revision}"
        directory.mkdir(parents=True)
        (directory / self.task.filename).write_text(state["code"], encoding="utf-8")
        (directory / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        impl_id = f"implementation_{self.run_id}_{algorithm}_r{revision}"
        val_id = f"validation_{self.run_id}_{algorithm}_r{revision}"
        parent = f"implementation_{self.run_id}_{algorithm}_r{revision - 1}" if revision else None
        attempt = {"algorithm": algorithm, "revision": revision, "implementation_id": impl_id,
                   "capability_id": candidate["capability_id"], "parent": parent,
                   "code_hash": digest(state["code"]), "code_path": str(directory / self.task.filename),
                   "explanation": state["code_explanation"], "generation": state.get("generation", {}), "validation": report}
        self.store.add_node(impl_id, "Implementation", {key: value for key, value in attempt.items() if key != "validation"})
        self.store.add_node(val_id, "ValidationRun", report)
        self.store.add_edge(candidate["capability_id"], impl_id, "realized_by")
        self.store.add_edge(impl_id, val_id, "validated_by")
        self.store.add_edge(impl_id, self.environment_id, "requires")
        for evidence_id in attempt['generation'].get('repository_candidate_ids', []):
            self.store.add_edge(impl_id, evidence_id, "context_from")
        if parent:
            self.store.add_edge(impl_id, parent, "revises")
        if not report["passed"] or revision:
            lesson_id = f"outcome_{self.run_id}_{algorithm}_r{revision}"
            lesson = {"scope": self.config.task, "algorithm": algorithm,
                      "summary": "; ".join(report["errors"]) if not report["passed"] else "修复后的版本通过可信验证集检查",
                      "validated_fix": bool(report["passed"] and revision),
                      "implementation_id": impl_id, "parent": parent,
                      "evidence": {"code_hash": attempt["code_hash"], "validation_id": val_id},
                      "diagnosis": state.get("diagnosis") if revision else None}
            if report['passed'] and revision:
                prior = next((row for row in reversed(state['attempts']) if row['algorithm'] == algorithm), None)
                if prior and not prior['validation']['passed']:
                    previous_code = Path(prior['code_path']).read_text(encoding='utf-8')
                    lesson['repair_rules'] = observed_repair_rules(previous_code, state['code'],
                                                                   prior['validation'], self.config.requirements)
                    lesson['evidence'].update(prior_validation_id=f"validation_{self.run_id}_{algorithm}_r{revision - 1}",
                                               prior_code_hash=prior['code_hash'])
                    lesson['observation'] = 'Revision changed code and passed validation; causal effect on other tasks is untested.'
                else:
                    lesson['validated_fix'] = False
            self.store.add_node(lesson_id, "Lesson", lesson)
            self.store.add_edge(val_id, lesson_id, "produced")
        return {"validation": report, "attempts": state["attempts"] + [attempt]}

    def _review(self, state):
        candidate = state["plan"]["candidates"][state["candidate_index"]]
        context = focused_context(state, self.registry, self.config)
        feedback = repair_feedback_report(state)
        context.update(code=state['code'], validation=validation_feedback(feedback),
                       trusted_findings=static_findings(state['code']) + import_findings(state['code'], self.config.task))
        if feedback.get('feedback_origin'):
            context['feedback_origin'] = feedback['feedback_origin']
        try:
            diagnosis = self.client.ask("reviewer", context, Diagnosis).model_dump()
        except LLMError as exc:
            diagnosis = {"summary": "Reviewer unavailable: " + str(exc),
                         "repair_instructions": state["validation"]["errors"]}
        diagnosis['trusted_findings'] = context['trusted_findings']
        diagnosis['execution_diagnostics'] = context['validation']['diagnostics']
        diagnosis['unchanged_previous_repair'] = state.get('generation', {}).get('unchanged', False)
        lesson_id = f"lesson_{self.run_id}_{candidate['algorithm']}_r{state['revision']}"
        self.store.add_node(lesson_id, "Lesson", {**diagnosis, "scope": self.config.task,
                                                 "algorithm": candidate["algorithm"],
                                                 "validated_fix": False,
                                                 "origin": "failed validation; advice is not a verified fix"})
        self.store.add_edge(f"validation_{self.run_id}_{candidate['algorithm']}_r{state['revision']}",
                            lesson_id, "produced")
        (Path(state["attempts"][-1]["code_path"]).parent / "diagnosis.json").write_text(
            json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"diagnosis": diagnosis, "revision": state["revision"] + 1}

    def _after_validation(self, state):
        # A disconnected model service cannot be repaired by modifying Python.
        # Preserve the failed attempt and stop without spending more role calls.
        if state.get('generation', {}).get('failure_kind') == 'transport':
            return 'select'
        # Pinned source adapters have no generative repair step. Repeating an
        # unchanged wrapper cannot remedy a failed contract or quality gate.
        deterministic = state.get('generation', {}).get('backend') == 'deterministic_source_adapter'
        if not deterministic and not state["validation"]["passed"] and state["revision"] < self.config.max_repairs:
            return "review"
        return "advance" if state["candidate_index"] + 1 < len(state["plan"]["candidates"]) else "select"

    def _advance(self, state):
        return {"candidate_index": state["candidate_index"] + 1, "revision": 0, "diagnosis": {}}

    def _select(self, state):
        if state.get('generation', {}).get('failure_kind') == 'transport':
            return {'selected': None, 'test': None, 'status': 'failed_generation'}
        passed = [attempt for attempt in state["attempts"] if attempt["validation"]["passed"]]
        if not passed:
            failed_generation = all(row['validation'].get('executed') is False for row in state['attempts'])
            return {"selected": None, "test": None,
                    "status": "failed_generation" if failed_generation else "failed_validation"}
        def ranking(row):
            values = tuple(row["validation"]["metrics"][name]
                           * (1 if self.registry.metric(name).direction == "higher" else -1)
                           for name in self.config.selection_order)
            plugin = self.registry.algorithm(row["algorithm"])
            # Equal metrics: fewer dependencies, configured algorithm order, earliest revision.
            return (*values, -len(plugin.dependencies),
                    -self.config.algorithms.index(row["algorithm"]), -row["revision"])
        selected = max(passed, key=ranking)
        return {"selected": selected}

    def _test(self, state):
        # The selection is frozen BEFORE this node reads held-out query text or labels.
        code = Path(state["selected"]["code_path"]).read_text(encoding="utf-8")
        if digest(code) != state["selected"]["code_hash"]:
            raise ValueError("Selected code changed before final test")
        report = self._evaluate(code, state['request'], state['selected']['algorithm'], 'test')
        uid = "test_" + self.run_id
        self.store.add_node(uid, "ValidationRun", report)
        self.store.add_edge(state["selected"]["implementation_id"], uid, "tested_by")
        # No repair or re-selection is permitted after a final-test failure.
        if report["passed"]:
            shutil.copyfile(state["selected"]["code_path"], self.directory / self.task.selected_filename)
        return {"test": report, "status": "passed" if report["passed"] else "failed_test"}

    def _persist(self, state):
        from .contracts import contract_hash
        saved = json.loads((self.directory / 'capability_contract.json').read_text(encoding='utf-8'))
        if contract_hash(saved) != self.contract_hash or self.task.contract.model_dump() != self.contract_snapshot:
            raise ValueError('Capability contract changed before publication')
        files = {name: digest((self.workspace / "data" / name).read_text(encoding="utf-8"))
                 for name in self.task.data_files}
        if files != self.dataset_hashes:
            raise ValueError('Dataset changed during the run; refusing to publish')
        backends = {row.get('generation', {}).get('backend', 'unknown') for row in state['attempts']}
        generation_backend = (state.get('selected') or {}).get('generation', {}).get('backend')
        if generation_backend is None:
            generation_backend = next(iter(backends)) if len(backends) == 1 else 'mixed' if backends else 'none'
        summary = {"run_id": self.run_id, "description": state["description"],
                   "provider": self.client.provider, "model": self.client.model,
                   "mock_fault_injection": self.client.inject_fault, "runner": self.runner_mode,
                   "request": state["request"], "plan": state["plan"], "attempts": state["attempts"],
                   "selected": state["selected"], "test": state["test"], "status": state["status"],
                   "environment": self.environment, "dataset_hashes": files,
                   "validation_config": self.config.model_dump(), "llm_calls": self.client.calls,
                   "plugin_catalog": self.registry.catalog(self.config),
                   "generation_policy": "spec-or-reference-v10",
                   "generation_mode": self.config.generation_mode,
                   "history_mode": self.config.history_mode,
                   "model_seed": None if self.client.provider == "deepseek" else self.config.model_seed,
                   "requested_model_seed": self.config.model_seed, "seed_supported": self.client.provider == "ollama",
                   "requirements": self.config.requirements,
                   "generation_backend": generation_backend,
                   "history_lesson_ids": [item['lesson_id'] for item in
                       planner_knowledge(state['knowledge'], self.registry, self.config)['lessons']],
                   "mock_fault_algorithm": self.client.fault_algorithm if self.client.inject_fault else None,
                   "workflow_events": state["events"] + ["persist"]}
        summary.update(self.report_metadata(state))
        summary.update(self.task.summary_metadata(self))
        summary.update(capability_contract=self.contract_snapshot, contract_hash=self.contract_hash,
                       artifacts={'selected_code': self.task.selected_filename, 'contract': 'capability_contract.json'})
        if getattr(self, 'routing', None):
            summary['routing'] = self.routing
        self.store.add_node("run_" + self.run_id, "FactoryRun", summary)
        for attempt in state["attempts"]:
            self.store.add_edge("run_" + self.run_id, attempt["implementation_id"], "generated")
        if state["status"] == "passed":
            plugin = {"task": self.config.task, "entrypoint": self.task.entrypoint, "interface_version": "1",
                      "runner": self.runner_mode,
                      "run_id": self.run_id, "code_hash": state["selected"]["code_hash"],
                      "code_path": str(self.directory / self.task.selected_filename),
                      "algorithm_plugin": self.registry.algorithm(state["selected"]["algorithm"]).metadata(),
                      "dependencies": list(self.registry.algorithm(state["selected"]["algorithm"]).dependencies),
                      "validation_report": str(self.directory / "report.json"),
                      "generation_mode": self.config.generation_mode,
                      "history_mode": self.config.history_mode,
                      "model_seed": None if self.client.provider == "deepseek" else self.config.model_seed,
                      "requested_model_seed": self.config.model_seed,
                      "seed_supported": self.client.provider == "ollama",
                      "requirements": self.config.requirements,
                      "generation_backend": state['selected'].get('generation', {}).get('backend', 'unknown')}
            plugin.update(self.task.publication_metadata(self))
            plugin.update(capability_contract=self.contract_snapshot, contract_hash=self.contract_hash)
            (self.directory / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False, indent=2), encoding="utf-8")
        write_report(self.directory, summary)
        self.store.export(self.directory / "knowledge")
        return {}

    def report_metadata(self, state):
        return {}

    def graph(self):
        builder = StateGraph(FactoryState)
        for name, function in [("understand", self._understand), ("retrieve", self._retrieve),
                               ("plan", self._plan), ("generate", self._generate),
                               ("validate", self._validate), ("review", self._review),
                               ("advance", self._advance), ("select", self._select),
                               ("test", self._test), ("persist", self._persist)]:
            builder.add_node(name, self._wrap(name, function))
        builder.add_edge(START, "understand")
        for source, target in [("understand", "retrieve"), ("retrieve", "plan"),
                               ("plan", "generate"), ("generate", "validate"),
                               ("review", "generate"), ("advance", "generate"),
                               ("test", "persist")]:
            builder.add_edge(source, target)
        builder.add_conditional_edges("validate", self._after_validation,
                                      {name: name for name in ["review", "advance", "select"]})
        builder.add_conditional_edges("select", lambda state: "test" if state["selected"] else "persist",
                                      {"test": "test", "persist": "persist"})
        builder.add_edge("persist", END)
        return builder.compile()

    def run(self, description, initial_codes=None):
        try:
            if not description.strip():
                raise ValueError("Empty capability description")
            # Gold data integrity is a trusted precondition, not a task for generated code.
            self._prepare_data()
            self.store.add_node(self.environment_id, "Environment", self.environment)
            known = {cap["algorithm"] for cap in self.store.nodes("Capability")}
            missing = [name for name in self.config.algorithms if name not in known]
            if missing:
                ingest_sources(self.store, self.client, self.workspace / "knowledge" / "sources",
                               self.directory / "extracted_capabilities.json", registry=self.registry,
                               algorithm_ids=missing)
            graph = self.graph()
            (self.directory / "workflow.mmd").write_text(graph.get_graph().draw_mermaid(), encoding="utf-8")
            state = graph.invoke({"description": description, "attempts": [], "events": getattr(self, 'initial_events', []),
                                  "initial_codes": initial_codes or {}},
                                 config={"recursion_limit": 10 + self.config.max_candidates *
                                         (3 + 3 * self.config.max_repairs)})
            return {"status": state["status"], "run_id": self.run_id,
                    "directory": str(self.directory), "selected": state["selected"]}
        except Exception as exc:
            failure = {"status": "error", "run_id": self.run_id, "error": f"{type(exc).__name__}: {exc}"}
            (self.directory / "error.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            raise

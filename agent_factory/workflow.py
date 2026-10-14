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
from .registry import load_registry
from .repair import code_fingerprint, focused_context, static_findings, validation_feedback
from .reporting import write_report
from .runner import Runner
from .validation import load_dataset, validate

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
    def __init__(self, workspace, provider="mock", model="mock-template-v1",
                 base_url="http://localhost:11434", runner=None, inject_fault=False,
                 allow_local=False, config=None, registry=None):
        self.workspace = Path(workspace).resolve()
        self.config = config or ValidationConfig.model_validate(
            json.loads((self.workspace / "config.json").read_text(encoding="utf-8")))
        self.registry = registry or load_registry(self.config.plugin_packages)
        self.registry.validate_config(self.config)
        self.runner_mode = runner or ("docker" if provider == "ollama" else "local")
        if provider == "ollama" and self.runner_mode == "local" and not allow_local:
            raise ValueError("真实 LLM 代码请使用 Docker；若明确接受本地执行边界，添加 --allow-local")
        if inject_fault and provider != "mock":
            raise ValueError("故障注入只用于明确标注的 mock 演示")
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        self.directory = self.workspace / "runs" / self.run_id
        self.directory.mkdir(parents=True)
        self.client = LLMClient(provider, model, base_url, self.directory / "llm", inject_fault,
                                registry=self.registry,
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
        request = self.client.ask("understander", {"description": state["description"]}, Request)
        request.description = state["description"]
        return {"request": request.model_dump()}

    def _retrieve(self, state):
        knowledge = self.store.retrieve(state["description"], limit=max(4, len(self.config.algorithms)),
                                        algorithms=self.config.algorithms)
        (self.directory / "retrieved_knowledge.json").write_text(
            json.dumps(knowledge, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"knowledge": knowledge}

    def _plan(self, state):
        knowledge = dict(state["knowledge"])
        if knowledge.get("repository_candidates"):
            # Planning needs provenance and descriptions; only the current coder
            # receives the bounded code excerpt, preserving the small-model budget.
            knowledge["repository_candidates"] = [
                {key: value for key, value in row.items() if key != "code"}
                for row in knowledge["repository_candidates"]]
        plan = self.client.ask("planner", {"request": state["request"], **knowledge,
                                          "algorithm_catalog": self.registry.catalog(self.config)["algorithms"],
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
                    'source_hash': seed['source_hash'], 'unchanged': False}}
        context = focused_context(state, self.registry)
        repository_ids = [row['id'] for row in context.get('repository_candidates', [])]
        role = 'coder'
        strategy = 'reproduce_reference'
        if state['revision']:
            role = 'repairer'
            strategy = 'rebuild_from_reference' if state['revision'] >= 2 else 'targeted_repair'
            context.update(repair_strategy=strategy, diagnosis=state['diagnosis'],
                           validation=validation_feedback(state['validation']))
            if strategy == 'targeted_repair':
                context['previous_code'] = state['code']
            else:
                # Do not anchor the final repair on the repeatedly failing implementation.
                context['failed_code_hash'] = digest(state['code'])
        context['repair_strategy'] = strategy
        try:
            artifact = self.client.ask(role, context, CodeArtifact)
            unchanged = bool(state['revision'] and code_fingerprint(artifact.code) == code_fingerprint(state['code']))
            return {'code': artifact.code, 'code_explanation': artifact.explanation, 'generation_error': None,
                    'generation': {'strategy': strategy, 'unchanged': unchanged,
                                   'semantic_hash': code_fingerprint(artifact.code),
                                   'repository_candidate_ids': repository_ids}}
        except LLMError as exc:
            return {'code': '', 'code_explanation': '', 'generation_error': str(exc),
                    'generation': {'strategy': strategy, 'unchanged': False,
                                   'repository_candidate_ids': repository_ids}}

    def _validate(self, state):
        memories, queries = load_dataset(self.workspace / "data", "validation")
        report = validate(state["code"], self.runner, memories, queries,
                          state["request"]["top_k"], self.config, registry=self.registry,
                          algorithm=state["plan"]["candidates"][state["candidate_index"]]["algorithm"])
        if state["generation_error"]:
            report["errors"].append(state["generation_error"])
            report["passed"] = False
        if state.get('generation', {}).get('unchanged'):
            report['errors'].append('repair: implementation unchanged apart from comments or docstrings')
            report['passed'] = False
        candidate = state["plan"]["candidates"][state["candidate_index"]]
        algorithm, revision = candidate["algorithm"], state["revision"]
        directory = self.directory / algorithm / f"r{revision}"
        directory.mkdir(parents=True)
        (directory / "retrieval.py").write_text(state["code"], encoding="utf-8")
        (directory / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        impl_id = f"implementation_{self.run_id}_{algorithm}_r{revision}"
        val_id = f"validation_{self.run_id}_{algorithm}_r{revision}"
        parent = f"implementation_{self.run_id}_{algorithm}_r{revision - 1}" if revision else None
        attempt = {"algorithm": algorithm, "revision": revision, "implementation_id": impl_id,
                   "capability_id": candidate["capability_id"], "parent": parent,
                   "code_hash": digest(state["code"]), "code_path": str(directory / "retrieval.py"),
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
            lesson = {"scope": "memory_retrieval", "algorithm": algorithm,
                      "summary": "; ".join(report["errors"]) if not report["passed"] else "修复后的版本通过可信验证集检查",
                      "validated_fix": bool(report["passed"] and revision),
                      "implementation_id": impl_id, "parent": parent,
                      "evidence": {"code_hash": attempt["code_hash"], "validation_id": val_id},
                      "diagnosis": state.get("diagnosis") if revision else None}
            self.store.add_node(lesson_id, "Lesson", lesson)
            self.store.add_edge(val_id, lesson_id, "produced")
        return {"validation": report, "attempts": state["attempts"] + [attempt]}

    def _review(self, state):
        candidate = state["plan"]["candidates"][state["candidate_index"]]
        context = focused_context(state, self.registry)
        context.update(code=state['code'], validation=validation_feedback(state['validation']),
                       trusted_findings=static_findings(state['code']))
        try:
            diagnosis = self.client.ask("reviewer", context, Diagnosis).model_dump()
        except LLMError as exc:
            diagnosis = {"summary": "Reviewer unavailable: " + str(exc),
                         "repair_instructions": state["validation"]["errors"]}
        diagnosis['trusted_findings'] = context['trusted_findings']
        diagnosis['execution_diagnostics'] = context['validation']['diagnostics']
        diagnosis['unchanged_previous_repair'] = state.get('generation', {}).get('unchanged', False)
        lesson_id = f"lesson_{self.run_id}_{candidate['algorithm']}_r{state['revision']}"
        self.store.add_node(lesson_id, "Lesson", {**diagnosis, "scope": "memory_retrieval",
                                                 "algorithm": candidate["algorithm"],
                                                 "validated_fix": False,
                                                 "origin": "failed validation; advice is not a verified fix"})
        self.store.add_edge(f"validation_{self.run_id}_{candidate['algorithm']}_r{state['revision']}",
                            lesson_id, "produced")
        (Path(state["attempts"][-1]["code_path"]).parent / "diagnosis.json").write_text(
            json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"diagnosis": diagnosis, "revision": state["revision"] + 1}

    def _after_validation(self, state):
        if not state["validation"]["passed"] and state["revision"] < self.config.max_repairs:
            return "review"
        return "advance" if state["candidate_index"] + 1 < len(state["plan"]["candidates"]) else "select"

    def _advance(self, state):
        return {"candidate_index": state["candidate_index"] + 1, "revision": 0, "diagnosis": {}}

    def _select(self, state):
        passed = [attempt for attempt in state["attempts"] if attempt["validation"]["passed"]]
        if not passed:
            return {"selected": None, "test": None, "status": "failed_validation"}
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
        memories, queries = load_dataset(self.workspace / "data", "test")
        report = validate(code, self.runner, memories, queries, state["request"]["top_k"],
                          self.config, "test", registry=self.registry,
                          algorithm=state["selected"]["algorithm"])
        uid = "test_" + self.run_id
        self.store.add_node(uid, "ValidationRun", report)
        self.store.add_edge(state["selected"]["implementation_id"], uid, "tested_by")
        # No repair or re-selection is permitted after a final-test failure.
        if report["passed"]:
            shutil.copyfile(state["selected"]["code_path"], self.directory / "selected_retrieval.py")
        return {"test": report, "status": "passed" if report["passed"] else "failed_test"}

    def _persist(self, state):
        files = {name: digest((self.workspace / "data" / name).read_text(encoding="utf-8"))
                 for name in ["memories.json", "validation_queries.json", "test_queries.json"]}
        summary = {"run_id": self.run_id, "description": state["description"],
                   "provider": self.client.provider, "model": self.client.model,
                   "mock_fault_injection": self.client.inject_fault, "runner": self.runner_mode,
                   "request": state["request"], "plan": state["plan"], "attempts": state["attempts"],
                   "selected": state["selected"], "test": state["test"], "status": state["status"],
                   "environment": self.environment, "dataset_hashes": files,
                   "validation_config": self.config.model_dump(), "llm_calls": self.client.calls,
                   "plugin_catalog": self.registry.catalog(self.config),
                   "generation_policy": "focused-repair-v2",
                   "mock_fault_algorithm": self.client.fault_algorithm if self.client.inject_fault else None,
                   "workflow_events": state["events"] + ["persist"]}
        self.store.add_node("run_" + self.run_id, "FactoryRun", summary)
        for attempt in state["attempts"]:
            self.store.add_edge("run_" + self.run_id, attempt["implementation_id"], "generated")
        if state["status"] == "passed":
            plugin = {"task": "memory_retrieval", "entrypoint": "retrieve", "interface_version": "1",
                      "runner": self.runner_mode,
                      "run_id": self.run_id, "code_hash": state["selected"]["code_hash"],
                      "code_path": str(self.directory / "selected_retrieval.py"),
                      "algorithm_plugin": self.registry.algorithm(state["selected"]["algorithm"]).metadata(),
                      "dependencies": list(self.registry.algorithm(state["selected"]["algorithm"]).dependencies),
                      "validation_report": str(self.directory / "report.json")}
            (self.directory / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False, indent=2), encoding="utf-8")
        write_report(self.directory, summary)
        self.store.export(self.directory / "knowledge")
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
            _, validation_queries = load_dataset(self.workspace / "data", "validation")
            _, test_queries = load_dataset(self.workspace / "data", "test")
            if {q["query"] for q in validation_queries} & {q["query"] for q in test_queries}:
                raise ValueError("Validation and final-test queries must be disjoint")
            self.store.add_node(self.environment_id, "Environment", self.environment)
            known = {cap["algorithm"] for cap in self.store.nodes("Capability")}
            missing = [name for name in self.config.algorithms if name not in known]
            if missing:
                ingest_sources(self.store, self.client, self.workspace / "knowledge" / "sources",
                               self.directory / "extracted_capabilities.json", registry=self.registry,
                               algorithm_ids=missing)
            graph = self.graph()
            (self.directory / "workflow.mmd").write_text(graph.get_graph().draw_mermaid(), encoding="utf-8")
            state = graph.invoke({"description": description, "attempts": [], "events": [],
                                  "initial_codes": initial_codes or {}},
                                 config={"recursion_limit": 10 + self.config.max_candidates *
                                         (3 + 3 * self.config.max_repairs)})
            return {"status": state["status"], "run_id": self.run_id,
                    "directory": str(self.directory), "selected": state["selected"]}
        except Exception as exc:
            failure = {"status": "error", "run_id": self.run_id, "error": f"{type(exc).__name__}: {exc}"}
            (self.directory / "error.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            raise

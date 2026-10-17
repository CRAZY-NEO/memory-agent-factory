"""Reuse an approved retrieval artifact from a future personal-memory application."""

import json
from pathlib import Path

from .classification import effective_training_examples
from .knowledge import digest
from .models import Memory
from .runner import Runner
from .validation import check_result


class MemoryRetriever:
    """Plugin adapter loads the frozen artifact, verifies its hash and runs bounded retrieval."""

    def __init__(self, manifest, runner=None):
        manifest = Path(manifest)
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        if self.manifest["task"] != "memory_retrieval" or self.manifest["interface_version"] != "1":
            raise ValueError("Unsupported plugin interface")
        self.code = (manifest.parent / "selected_retrieval.py").read_text(encoding="utf-8")
        if digest(self.code) != self.manifest["code_hash"]:
            raise ValueError("Plugin hash mismatch: generated code was changed")
        self.runner = Runner(runner or self.manifest.get("runner", "docker"))

    def retrieve(self, query, memories, top_k=5):
        if not isinstance(query, str) or not isinstance(top_k, int) or isinstance(top_k, bool):
            raise ValueError("query must be str and top_k must be int")
        records = [Memory.model_validate(memory).model_dump() for memory in memories]
        if len({row["id"] for row in records}) != len(records):
            raise ValueError("Memory IDs must be unique")
        case = {"case_id": "query", "query": query, "memories": records, "top_k": top_k}
        result = self.runner.execute(self.code, [case])
        if "error" in result:
            raise RuntimeError(result["error"])
        return check_result(result["observations"][0], case)


class MemoryClassifier:
    """Reuse a frozen classifier and its training examples through the trusted runner.

    Training data is read only from the run directory and verified before use.
    Applications provide new unlabelled texts; they cannot replace the training
    set through this adapter or pass evaluation labels to the generated code.
    """

    def __init__(self, manifest, runner=None):
        manifest = Path(manifest)
        if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
            raise ValueError("Unsafe classifier manifest")
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        if (self.manifest.get("task") != "memory_classification"
                or self.manifest.get("entrypoint") != "classify"
                or self.manifest.get("interface_version") != "1"):
            raise ValueError("Unsupported classifier interface")
        if self.manifest.get("training_data") != "training_examples.json":
            raise ValueError("Classifier training data must be the frozen local training_examples.json")
        directory = manifest.parent.resolve()

        def verified(name, expected_hash, size_limit):
            path = directory / name
            if (path.is_symlink() or not path.resolve().is_relative_to(directory)
                    or not path.is_file() or path.stat().st_size > size_limit):
                raise ValueError("Unsafe classifier artifact: " + name)
            content = path.read_bytes().decode("utf-8")
            if digest(content) != expected_hash:
                raise ValueError("Classifier artifact hash mismatch: " + name)
            return content

        self.code = verified("selected_classification.py", self.manifest.get("code_hash"), 512 * 1024)
        training = json.loads(verified("training_examples.json", self.manifest.get("training_data_hash"),
                                      8 * 1024 * 1024))
        effective_training_examples(training, nonempty=True, nonblank=True)
        requirements = self.manifest.get("requirements", [])
        from .requirements import requirement_contracts

        requirement_contracts("memory_classification", requirements)
        if (any(row.get("enabled") is False for row in training)
                and "classification_ignore_disabled" not in requirements):
            raise ValueError("Disabled training examples require the ignore-disabled business contract")
        # Keep a canonical private snapshot. Each call receives a new parsed copy,
        # so an application or custom transport cannot change subsequent inputs.
        self._training_json = json.dumps(training, ensure_ascii=False)
        self.runner = Runner(runner or self.manifest.get("runner", "docker"))

    def classify(self, texts):
        from .classification import check_result as check_classification_result

        if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
            raise ValueError("texts must be a list of strings")
        case = {"case_id": "classify", "texts": list(texts),
                "training_examples": json.loads(self._training_json)}
        result = self.runner.execute(self.code, [case], task="memory_classification")
        if "error" in result:
            raise RuntimeError(result["error"])
        observations = result.get("observations", [])
        if (not isinstance(observations, list) or len(observations) != 1
                or not isinstance(observations[0], dict) or observations[0].get("case_id") != "classify"):
            raise ValueError("Classifier worker returned missing or unexpected observations")
        return check_classification_result(observations[0], case)


class _DocumentReviewer:
    """Pure structured-input document capability, executed in the same sandbox."""

    def __init__(self, manifest, runner=None):
        from .tasks import task_spec
        self.path = Path(manifest)
        self.manifest = json.loads(self.path.read_text(encoding='utf-8'))
        self.task = task_spec(self.manifest['task'], self.manifest.get('capability_contract', {}).get('version', '1'))
        if self.task.contract.domain != 'information_design':
            raise ValueError('DocumentReviewer requires a document-review capability')
        self.code = (self.path.parent / self.task.selected_filename).read_text(encoding='utf-8')
        if digest(self.code) != self.manifest['code_hash']:
            raise ValueError('Capability code hash mismatch')
        self.runner = Runner(runner or self.manifest.get('runner', 'docker'))

    def invoke(self, payload):
        from .document_contracts import check_output, parse_input
        normalized = parse_input(self.task.id, payload, self.task.contract.version)
        case = {'case_id': 'invoke', 'payload': normalized}
        version_args = {'contract_version': self.task.contract.version} if self.task.contract.version != '1' else {}
        result = self.runner.execute(self.code, [case], task=self.task.id, **version_args)
        if 'error' in result:
            raise RuntimeError(result['error'])
        rows = result.get('observations', [])
        if len(rows) != 1 or rows[0].get('case_id') != 'invoke':
            raise ValueError('Missing or unexpected capability observation')
        row = rows[0]
        if 'error' in row:
            raise RuntimeError(row['error'])
        if row.get('mutated'):
            raise ValueError('Capability mutated inputs')
        return check_output(self.task.id, row.get('result'), normalized, self.task.contract.version)


class CapabilityPlugin:
    """Uniform application-facing SDK: load approved artifact, then invoke JSON.

    Only trusted adapters dispatch calls. Generated code never imports into this
    process. New manifests bind the contract, selected code and passing evidence.
    """

    def __init__(self, manifest, runner=None):
        from .contracts import contract_hash
        from .tasks import task_spec
        path = Path(manifest)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError('Unsafe capability manifest')
        self.manifest = json.loads(path.read_text(encoding='utf-8'))
        self.adapter = task_spec(self.manifest['task'], self.manifest.get('capability_contract', {}).get('version', '1'))
        contract = self.manifest.get('capability_contract')
        if contract is None:
            raise ValueError('Unified SDK requires a versioned capability contract')
        saved_path = path.parent / 'capability_contract.json'
        if saved_path.is_symlink() or saved_path.stat().st_size > 1024 * 1024:
            raise ValueError('Unsafe capability contract')
        saved = json.loads(saved_path.read_text(encoding='utf-8'))
        expected = contract_hash(self.adapter.contract.model_dump())
        if saved != contract:
            raise ValueError('Frozen capability contract mismatch')
        if contract_hash(contract) != expected or self.manifest.get('contract_hash') != expected:
            raise ValueError('Capability contract mismatch')
        report_path = path.parent / 'report.json'
        if report_path.is_symlink() or report_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Unsafe capability report')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        selected = report.get('selected') or {}
        if (self.manifest.get('interface_version') != '1'
                or self.manifest.get('entrypoint') != self.adapter.entrypoint
                or report.get('run_id') != self.manifest.get('run_id')
                or report.get('request', {}).get('task') != self.adapter.id
                or report.get('status') != 'passed' or report.get('test', {}).get('passed') is not True
                or report.get('test', {}).get('split') != 'test'
                or selected.get('validation', {}).get('passed') is not True
                or report.get('contract_hash') != expected
                or report.get('capability_contract') != contract):
            raise ValueError('Capability lacks matching passing publication evidence')
        code_path = path.parent / self.adapter.selected_filename
        if code_path.is_symlink() or not code_path.is_file() or code_path.stat().st_size > 512 * 1024:
            raise ValueError('Unsafe capability code')
        code = code_path.read_text(encoding='utf-8')
        if digest(code) != self.manifest.get('code_hash') or digest(code) != selected.get('code_hash'):
            raise ValueError('Capability code hash mismatch')
        self.runtime = self.adapter.create_runtime(path, runner)

    def invoke(self, payload):
        return self.adapter.invoke_runtime(self.runtime, payload)

"""Business-independent adapter interface; importing it never registers tasks."""

from .models import CapabilityContract


class TaskAdapter:
    """Extension boundary for data, invocation, validation and frozen artifacts."""

    id: str
    filename: str
    data_files: tuple[str, ...]
    contract: CapabilityContract
    config_file: str
    default_config: dict

    @property
    def entrypoint(self):
        return self.contract.entrypoint

    @property
    def selected_filename(self):
        return 'selected_' + self.filename

    def load(self, directory, split, config=None):
        raise NotImplementedError

    def preflight(self, directory, config=None):
        raise NotImplementedError

    def evaluate(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        from .safety import dependency_errors
        result = self.evaluate_task(code, runner, inputs, examples, request, config, registry, algorithm, split)
        declared = registry.algorithm(algorithm).dependencies if algorithm else self.contract.allowed_dependencies
        issues = dependency_errors(code, declared)
        result['errors'].extend(issues)
        result['passed'] = result['passed'] and not issues
        return result

    def evaluate_task(self, code, runner, inputs, examples, request, config, registry, algorithm, split):
        raise NotImplementedError

    def validate_cases(self, cases):
        raise NotImplementedError

    def prepare(self, factory):
        """Optional trusted freezing, e.g. classifier training data."""

    def frozen_inputs(self, factory, inputs):
        return inputs

    def publication_metadata(self, factory):
        return {}

    def summary_metadata(self, factory):
        return {}

    def create_runtime(self, manifest, runner=None):
        raise NotImplementedError

    def invoke_runtime(self, runtime, payload):
        raise NotImplementedError

    def portable_validation(self):
        return None

    def example_input(self):
        raise NotImplementedError

    def code_rules(self):
        raise NotImplementedError

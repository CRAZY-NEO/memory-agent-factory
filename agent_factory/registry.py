"""Discover developer-authored plugins and validate their contracts before execution."""

import importlib
import math
import pkgutil
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Literal

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
BUILTIN_PACKAGES = ("agent_factory.plugins.algorithms", "agent_factory.plugins.metrics")


@dataclass(frozen=True)
class AlgorithmSpec:
    id: str
    name: str
    version: str
    task: str
    material: Path
    reference: Path
    guidance: str
    dependencies: tuple[str, ...] = ()
    environment: str = "retrieval-v1"
    public_cases: tuple[dict, ...] = ()
    provenance: dict | None = None

    def metadata(self):
        result = {"id": self.id, "name": self.name, "version": self.version, "task": self.task,
                "dependencies": list(self.dependencies), "environment": self.environment,
                "guidance": self.guidance, "public_cases": list(self.public_cases)}
        if self.provenance:
            result['provenance'] = self.provenance
        return result


@dataclass(frozen=True)
class MetricContext:
    predictions: dict
    queries: list[dict]
    top_k: int
    durations_ms: tuple[float, ...] = ()


@dataclass(frozen=True)
class MetricSpec:
    id: str
    name: str
    version: str
    task: str
    direction: Literal["higher", "lower"]
    compute: Callable[[MetricContext], float]
    minimum: float = 0
    maximum: float | None = 1

    def metadata(self):
        return {"id": self.id, "name": self.name, "version": self.version, "task": self.task,
                "direction": self.direction, "minimum": self.minimum, "maximum": self.maximum}

    def evaluate(self, context):
        value = self.compute(context)
        if isinstance(value, bool):
            raise ValueError(f"Metric {self.id} returned bool instead of a numeric value")
        value = float(value)
        if not math.isfinite(value) or value < self.minimum or (self.maximum is not None and value > self.maximum):
            raise ValueError(f"Metric {self.id} returned a nonfinite or out-of-range value")
        return value


class PluginRegistry:
    def __init__(self):
        self.algorithms: dict[str, AlgorithmSpec] = {}
        self.metrics: dict[str, MetricSpec] = {}
        self.modules: list[str] = []
        self._packages: set[str] = set()

    @staticmethod
    def _identity(spec):
        if not IDENTIFIER.fullmatch(spec.id) or not spec.name or not spec.version:
            raise ValueError("Plugin needs a valid id, name and version")
        if spec.task not in {"memory_retrieval", "memory_classification"}:
            raise ValueError(f"Unsupported plugin task: {spec.task}")

    def add_algorithm(self, spec: AlgorithmSpec):
        self._identity(spec)
        if spec.id in self.algorithms:
            raise ValueError(f"Duplicate algorithm plugin: {spec.id}")
        expected = {"memory_retrieval": "retrieval-v1", "memory_classification": "classification-v1"}
        if spec.environment != expected[spec.task] or not set(spec.dependencies) <= {"scikit-learn"}:
            raise ValueError(f"Algorithm {spec.id} requires an unsupported execution environment")
        if not spec.material.is_file() or not spec.reference.is_file() or not spec.guidance:
            raise ValueError(f"Algorithm {spec.id} has missing material, reference or guidance")
        self.algorithms[spec.id] = spec

    def add_metric(self, spec: MetricSpec):
        self._identity(spec)
        if spec.id in self.metrics:
            raise ValueError(f"Duplicate metric plugin: {spec.id}")
        if spec.direction not in {"higher", "lower"} or not callable(spec.compute):
            raise ValueError(f"Invalid metric contract: {spec.id}")
        if not math.isfinite(spec.minimum) or (spec.maximum is not None and
                (not math.isfinite(spec.maximum) or spec.maximum < spec.minimum)):
            raise ValueError(f"Invalid metric range: {spec.id}")
        self.metrics[spec.id] = spec

    def algorithm(self, name):
        if name not in self.algorithms:
            raise ValueError(f"Unknown algorithm plugin: {name}")
        return self.algorithms[name]

    def metric(self, name):
        if name not in self.metrics:
            raise ValueError(f"Unknown metric plugin: {name}")
        return self.metrics[name]

    def discover(self, package_name):
        """Import only explicitly selected trusted packages, never generated run folders."""
        if package_name in self._packages:
            return
        # A failed package must not leave half of its registrations active.
        previous = (self.algorithms.copy(), self.metrics.copy(), self.modules.copy())
        try:
            package = importlib.import_module(package_name)
            if not hasattr(package, "__path__"):
                raise ValueError(f"Plugin package is not a package: {package_name}")
            for item in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
                if item.name.startswith("_") or item.ispkg:
                    continue
                module_name = package_name + "." + item.name
                module = importlib.import_module(module_name)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise ValueError(f"Plugin module has no register(registry): {module_name}")
                register(self)
                self.modules.append(module_name)
        except Exception as exc:
            self.algorithms, self.metrics, self.modules = previous
            raise ValueError(f"Plugin registration failed in {package_name}: {exc}") from exc
        self._packages.add(package_name)

    def validate_config(self, config):
        for name in config.algorithms:
            spec = self.algorithm(name)
            if spec.task != config.task:
                raise ValueError(f"Algorithm {name} is incompatible with {config.task}")
            for dependency in spec.dependencies:
                try:
                    version(dependency)
                except PackageNotFoundError as exc:
                    raise ValueError(f"Algorithm {name} requires missing dependency {dependency}") from exc
        for name, rule in config.metrics.items():
            spec = self.metric(name)
            if spec.task != config.task:
                raise ValueError(f"Metric {name} is incompatible with {config.task}")
            for threshold in [rule.min, rule.max]:
                if threshold is not None and (threshold < spec.minimum or
                        (spec.maximum is not None and threshold > spec.maximum)):
                    raise ValueError(f"Metric {name} threshold is outside its declared range")
        for name in config.selection_order:
            self.metric(name)
            if name not in config.metrics:
                raise ValueError(f"Selection metric is not enabled: {name}")

    def catalog(self, config=None):
        algorithms = config.algorithms if config else sorted(self.algorithms)
        metrics = config.metrics if config else sorted(self.metrics)
        return {"algorithms": [self.algorithm(name).metadata() for name in algorithms],
                "metrics": [self.metric(name).metadata() for name in metrics],
                "modules": self.modules.copy()}


def load_registry(extra_packages=(), workspace=None):
    registry = PluginRegistry()
    for package in [*BUILTIN_PACKAGES, *extra_packages]:
        registry.discover(package)
    if workspace is not None:
        from .releases import load_releases
        load_releases(registry, workspace)
    return registry

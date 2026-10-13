"""Validated contracts shared by role agents, knowledge storage and trusted evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Memory(Contract):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Query(Contract):
    id: str
    query: str = Field(min_length=1)
    relevant_ids: list[str] = Field(min_length=1)
    category: Literal["direct", "paraphrase", "hard_negative", "multi_relevance"] = "direct"
    hard_negative_ids: list[str] = Field(default_factory=list)
    annotation: str = ""


class Capability(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    name: str
    task: Literal["memory_retrieval"]
    algorithm: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    inputs: list[str]
    outputs: list[str]
    applicable_conditions: list[str]
    metrics: list[str]
    dependencies: list[str]
    limitations: list[str]
    source_id: str
    evidence: str = Field(min_length=10)


class Extraction(Contract):
    capabilities: list[Capability] = Field(min_length=1, max_length=4)


class Request(Contract):
    task: Literal["memory_retrieval"]
    description: str = Field(min_length=1)
    language: Literal["zh", "mixed"]
    top_k: int = Field(ge=1, le=20)


class Candidate(Contract):
    algorithm: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    capability_id: str
    rationale: str = Field(min_length=1)


class Plan(Contract):
    candidates: list[Candidate] = Field(min_length=1, max_length=8)
    design_explanation: str


class CodeArtifact(Contract):
    code: str = Field(min_length=30, max_length=40000)
    explanation: str


class Diagnosis(Contract):
    summary: str
    repair_instructions: list[str] = Field(min_length=1, max_length=6)


class Hit(Contract):
    id: str
    score: float = Field(allow_inf_nan=False, ge=0)


class MetricRule(Contract):
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Metric min cannot exceed max")
        return self


class ValidationConfig(Contract):
    task: Literal["memory_retrieval"] = "memory_retrieval"
    algorithms: list[str] = Field(default_factory=lambda: ["tfidf", "bm25"], min_length=1)
    plugin_packages: list[str] = Field(default_factory=list)
    max_candidates: int = Field(default=2, ge=1, le=8)
    compare_all_algorithms: bool = False
    metrics: dict[str, MetricRule] = Field(default_factory=lambda: {
        "recall_at_k": MetricRule(min=0.75), "mrr_at_k": MetricRule(min=0.65),
        "p95_ms": MetricRule(max=1000)})
    selection_order: list[str] = Field(default_factory=lambda: ["recall_at_k", "mrr_at_k"], min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_repairs: int = Field(default=2, ge=0, le=3)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_thresholds(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        legacy = {"min_recall": ("recall_at_k", "min"), "min_mrr": ("mrr_at_k", "min"),
                  "max_p95_ms": ("p95_ms", "max")}
        if any(name in value for name in legacy):
            if "metrics" in value:
                raise ValueError("Use metrics or legacy thresholds, not both")
            rules = {"recall_at_k": {"min": 0.75}, "mrr_at_k": {"min": 0.65},
                     "p95_ms": {"max": 1000}}
            for name, (metric, bound) in legacy.items():
                if name in value:
                    rules[metric][bound] = value.pop(name)
            value["metrics"] = rules
        return value

    @model_validator(mode="after")
    def unique_configuration(self):
        if len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("Duplicate enabled algorithms")
        if len(set(self.selection_order)) != len(self.selection_order):
            raise ValueError("Duplicate selection metrics")
        if self.compare_all_algorithms and len(self.algorithms) > self.max_candidates:
            raise ValueError("Comparing all algorithms requires a sufficient candidate budget")
        if not self.metrics:
            raise ValueError("At least one metric must be enabled")
        return self

"""Validated contracts shared by role agents, knowledge storage and trusted evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class Capability(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]+$")
    name: str
    task: Literal["memory_retrieval"]
    algorithm: Literal["tfidf", "bm25"]
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
    algorithm: Literal["tfidf", "bm25"]
    capability_id: str
    rationale: str = Field(min_length=1)


class Plan(Contract):
    candidates: list[Candidate] = Field(min_length=1, max_length=2)
    design_explanation: str


class CodeArtifact(Contract):
    code: str = Field(min_length=30, max_length=40000)
    explanation: str


class Diagnosis(Contract):
    summary: str
    repair_instructions: list[str] = Field(min_length=1)


class Hit(Contract):
    id: str
    score: float = Field(allow_inf_nan=False, ge=0)


class ValidationConfig(Contract):
    task: Literal["memory_retrieval"] = "memory_retrieval"
    min_recall: float = Field(default=0.75, ge=0, le=1)
    min_mrr: float = Field(default=0.65, ge=0, le=1)
    max_p95_ms: float = Field(default=1000, gt=0)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_repairs: int = Field(default=2, ge=0, le=3)

"""Trusted case-level correctness and latency metrics for 字段完整性检查."""

from agent_factory.document_review import case_accuracy, latency_p95
from agent_factory.registry import MetricSpec


def register(registry):
    registry.add_metric(MetricSpec('field_case_accuracy', '整例精确通过率', '1.0.0', 'document_field_completeness', 'higher', case_accuracy))
    registry.add_metric(MetricSpec('field_p95_ms', '单例 P95 毫秒', '1.0.0', 'document_field_completeness', 'lower', latency_p95, maximum=None))

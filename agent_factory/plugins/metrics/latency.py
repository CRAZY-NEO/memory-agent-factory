"""Trusted P95 ms evaluation plugin."""

from agent_factory.registry import MetricSpec


def compute(context):
    import numpy as np
    return float(np.percentile(context.durations_ms, 95))


def register(registry):
    registry.add_metric(MetricSpec(
        id='p95_ms', name='P95 ms', version="1.0.0", task="memory_retrieval",
        direction='lower', compute=compute, maximum=None))

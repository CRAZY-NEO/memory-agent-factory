"""Classification P95 includes fitting and predicting an entire input batch."""

from agent_factory.registry import MetricSpec


def compute(context):
    import numpy as np
    return float(np.percentile(context.durations_ms, 95))


def register(registry):
    registry.add_metric(MetricSpec(id="classification_p95_ms", name="Classification batch P95 ms",
        version="1.0.0", task="memory_classification", direction="lower", compute=compute, maximum=None))

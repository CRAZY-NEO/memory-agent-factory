"""Trusted classification Accuracy metric; gold labels stay in the parent evaluator."""

from agent_factory.registry import MetricSpec


def compute(context):
    return sum(context.predictions[row["id"]] == row["label"] for row in context.queries) / len(context.queries)


def register(registry):
    registry.add_metric(MetricSpec(id="accuracy", name="Accuracy", version="1.0.0",
        task="memory_classification", direction="higher", compute=compute))

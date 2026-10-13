"""Trusted Precision@k evaluation plugin."""

from agent_factory.registry import MetricSpec


def compute(context):
    # Fixed denominator k: missing returned slots count as nonrelevant.
    values = []
    for query in context.queries:
        relevant = set(query["relevant_ids"])
        ranked = {hit["id"] for hit in context.predictions[query["id"]][:context.top_k]}
        values.append(len(ranked & relevant) / context.top_k)
    return sum(values) / len(values)


def register(registry):
    registry.add_metric(MetricSpec(
        id='precision_at_k', name='Precision@k', version="1.0.0", task="memory_retrieval",
        direction='higher', compute=compute, maximum=1))

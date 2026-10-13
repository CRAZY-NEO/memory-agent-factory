"""Trusted Recall@k evaluation plugin."""

from agent_factory.registry import MetricSpec


def compute(context):
    values = []
    for query in context.queries:
        relevant = set(query["relevant_ids"])
        ranked = {hit["id"] for hit in context.predictions[query["id"]][:context.top_k]}
        values.append(len(ranked & relevant) / len(relevant))
    return sum(values) / len(values)


def register(registry):
    registry.add_metric(MetricSpec(
        id='recall_at_k', name='Recall@k', version="1.0.0", task="memory_retrieval",
        direction='higher', compute=compute, maximum=1))

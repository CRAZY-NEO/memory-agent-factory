"""Trusted MRR@k evaluation plugin."""

from agent_factory.registry import MetricSpec


def compute(context):
    values = []
    for query in context.queries:
        relevant = set(query["relevant_ids"])
        ranked = context.predictions[query["id"]][:context.top_k]
        rank = next((i + 1 for i, hit in enumerate(ranked) if hit["id"] in relevant), None)
        values.append(1 / rank if rank else 0)
    return sum(values) / len(values)


def register(registry):
    registry.add_metric(MetricSpec(
        id='mrr_at_k', name='MRR@k', version="1.0.0", task="memory_retrieval",
        direction='higher', compute=compute, maximum=1))

"""Trusted Macro-F1, with equal weight for every gold/predicted label."""

from agent_factory.registry import MetricSpec


def compute(context):
    pairs = [(row["label"], context.predictions[row["id"]]) for row in context.queries]
    labels = {label for pair in pairs for label in pair}
    scores = []
    for label in sorted(labels):
        tp = sum(gold == label and predicted == label for gold, predicted in pairs)
        fp = sum(gold != label and predicted == label for gold, predicted in pairs)
        fn = sum(gold == label and predicted != label for gold, predicted in pairs)
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0.0)
    return sum(scores) / len(scores)


def register(registry):
    registry.add_metric(MetricSpec(id="macro_f1", name="Macro-F1", version="1.0.0",
        task="memory_classification", direction="higher", compute=compute))

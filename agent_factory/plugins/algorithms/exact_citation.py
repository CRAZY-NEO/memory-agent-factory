"""Register a specification-only document capability: no complete code reference."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec
from agent_factory.tasks import task_spec


def register(registry):
    adapter = task_spec('document_source_references')
    registry.add_algorithm(AlgorithmSpec(
        id='exact_citation', name='来源引用检查', version='1.0.0', task=adapter.id,
        environment=adapter.contract.environment,
        material=Path(__file__).parent / 'materials' / 'exact_citation.md', reference=None,
        guidance=' '.join(adapter.contract.behaviors)))

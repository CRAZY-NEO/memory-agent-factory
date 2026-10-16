"""Register a specification-only document capability: no complete code reference."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec
from agent_factory.tasks import task_spec


def register(registry):
    adapter = task_spec('document_field_completeness')
    registry.add_algorithm(AlgorithmSpec(
        id='field_presence', name='字段完整性检查', version='1.0.0', task=adapter.id,
        environment=adapter.contract.environment,
        material=Path(__file__).parent / 'materials' / 'field_presence.md', reference=None,
        guidance=' '.join(adapter.contract.behaviors)))

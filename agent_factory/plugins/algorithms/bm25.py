"""字符 BM25 plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='bm25', name='字符 BM25', version="1.0.0", task="memory_retrieval",
        material=base / "materials" / "bm25.md",
        reference=base.parents[1] / "templates" / "bm25.py",
        guidance='使用原文中的非负 IDF、k1=1.5 和 b=0.75。中文单字/双字及英文词分词；将去重的查询词排序后累加，保持跨进程浮点结果一致。', dependencies=()))

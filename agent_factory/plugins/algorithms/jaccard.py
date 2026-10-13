"""集合 Jaccard plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='jaccard', name='集合 Jaccard', version="1.0.0", task="memory_retrieval",
        material=base / "materials" / "jaccard.md",
        reference=base.parents[1] / "templates" / "jaccard.py",
        guidance='使用中文单字/双字及英文词的集合交并比 len(Q & D)/len(Q | D)。中文双字必须从原文连续中文片段按相邻位置切分，不能从无序集合取字符再拼接。不使用词频、TF-IDF 或 BM25；只返回正分结果，处理空集合。', dependencies=()))

"""字符 TF-IDF plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='tfidf', name='字符 TF-IDF', version="1.0.0", task="memory_retrieval",
        material=base / "materials" / "tfidf.md",
        reference=base.parents[1] / "templates" / "tfidf.py",
        guidance='使用 sklearn 字符 1 至 3 元组 TF-IDF 与余弦相似度。拟合语料仅为记忆文本，transform([query]) 后与整个语料计算相似度。', dependencies=('scikit-learn',)))

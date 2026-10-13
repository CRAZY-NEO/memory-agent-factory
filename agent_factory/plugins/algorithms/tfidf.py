"""字符 TF-IDF plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='tfidf', name='字符 TF-IDF', version="1.0.1", task="memory_retrieval",
        material=base / "materials" / "tfidf.md",
        reference=base.parents[1] / "templates" / "tfidf.py",
        guidance='使用 sklearn 字符 1 至 3 元组 TF-IDF 与余弦相似度。必须对 texts=[memory["text"] for memory in memories] 拟合，每条完整记忆对应矩阵一行，行数必须等于 len(memories)。禁止把拆分去重的词当成多篇文档，禁止移除中文或过滤行后仍用原列表索引；transform([query]) 后与整个语料计算相似度。', dependencies=('scikit-learn',),
        public_cases=({'query': '甲乙 alpha beta', 'memories': [{'id': 'a', 'text': '甲乙 alpha beta', 'source': 'public://a'}, {'id': 'b', 'text': '乙丙 alpha gamma', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'a', 'score': 1.0000000000000016}, {'id': 'b', 'score': 0.46518071501565006}]},)))

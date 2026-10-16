"""字符 TF-IDF plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='tfidf', name='字符 TF-IDF', version="1.0.1", task="memory_retrieval",
        material=base / "materials" / "tfidf.md",
        reference=base.parents[1] / "templates" / "tfidf.py",
        guidance='使用 sklearn 字符 1 至 3 元组 TF-IDF（默认线性词频，sublinear_tf=False）与余弦相似度。先完成扩展要求中的筛选和去重，再对有效记忆的完整text列表拟合，每条有效记忆对应矩阵一行。禁止把拆分的词当成多篇文档、移除中文或过滤后仍用原列表索引；查询只transform，与同一有效语料矩阵计算相似度。只返回正分结果。', dependencies=('scikit-learn',),
        public_cases=({'query': '甲乙 alpha beta', 'memories': [{'id': 'a', 'text': '甲乙 alpha beta', 'source': 'public://a'}, {'id': 'b', 'text': '乙丙 alpha gamma', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'a', 'score': 1.0000000000000016}, {'id': 'b', 'score': 0.46518071501565006}]},)))

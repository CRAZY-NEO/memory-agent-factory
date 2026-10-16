"""集合 Jaccard plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='jaccard', name='集合 Jaccard', version="1.0.1", task="memory_retrieval",
        material=base / "materials" / "jaccard.md",
        reference=base.parents[1] / "templates" / "jaccard.py",
        guidance='使用中文单字/双字及英文词的集合交并比 len(Q & D)/len(Q | D)。中文双字必须从每个原文连续中文片段内按相邻位置切分；空格、标点、英文都切断片段。禁止先去掉分隔符或抽出全文所有中文字符后再连接，禁止跨片段拼接，也不能从无序集合拼接。英文标识符先统一转小写，再整词加入集合，例如 tokens.add(fragment)，禁止 tokens.update(fragment) 将英文拆成字母；alpha 与 beta 的英文词集合没有交集。不使用词频、TF-IDF 或 BM25；只返回正分结果，处理空集合。', dependencies=(),
        public_cases=(
            {'query': '甲乙', 'memories': [{'id': 'a', 'text': '甲乙', 'source': 'public://a'}, {'id': 'b', 'text': '甲 乙', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'a', 'score': 1.0}, {'id': 'b', 'score': 2 / 3}]},
            {'query': '甲乙', 'memories': [{'id': 'b', 'text': '甲Python乙', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'b', 'score': 0.5}]},
            {'query': '甲乙', 'memories': [{'id': 'a', 'text': '甲乙', 'source': 'public://a'}, {'id': 'b', 'text': '乙丙', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'a', 'score': 1.0}, {'id': 'b', 'score': 0.2}]},
            {'query': 'ALPHA', 'memories': [{'id': 'a', 'text': 'alpha', 'source': 'public://a'}, {'id': 'b', 'text': 'beta', 'source': 'public://b'}], 'top_k': 5, 'expected': [{'id': 'a', 'score': 1.0}]},)))

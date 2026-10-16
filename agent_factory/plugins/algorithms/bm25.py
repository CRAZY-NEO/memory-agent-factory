"""字符 BM25 plugin registration."""

from pathlib import Path

from agent_factory.registry import AlgorithmSpec


def register(registry):
    base = Path(__file__).parent
    registry.add_algorithm(AlgorithmSpec(
        id='bm25', name='字符 BM25', version="1.0.0", task="memory_retrieval",
        material=base / "materials" / "bm25.md",
        reference=base.parents[1] / "templates" / "bm25.py",
        guidance='BM25：k1=1.5，b=0.75；IDF(t)=log(1+(N-df(t)+0.5)/(df(t)+0.5))；每个查询词贡献 IDF(t)*tf(t,d)*(k1+1)/(tf(t,d)+k1*(1-b+b*len(d)/avgdl))。N、df、avgdl均从满足扩展筛选/去重要求后的有效语料计算。中文连续片段提取单字和相邻双字，英文及数字下划线按小写完整词；文档保留词频，去重查询词排序后累加，跨进程稳定。只返回正分结果。', dependencies=()))

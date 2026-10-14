# BM25Plus

算法：github_xhluca_bm25s。任务：memory_retrieval。
输入 query、memories、top_k；输出有序 id、score。

Reproduce BM25Plus from the pinned source scoring functions and constructor defaults. Adapt the retrieve interface and tokenizer only; do not mix in unrelated scoring methods or install dependencies.

模型归纳（未独立认证，不作为评分公式）：The BM25Plus algorithm adapts to the repository by leveraging existing scoring functions from bm25s/scoring.py. It uses the _score_idf_bm25plus function for IDF calculation and _score_tfc_bm25plus for TF calculation, ensuring compatibility with the provided interface. The method requires no additional dependencies beyond the existing bm25s library, maintaining stability and performance.

适用：BM25Plus is only applicable when the repository contains the _score_idf_bm25plus and _score_tfc_bm25plus functions in bm25s/scoring.py.；The method is applicable only when the corpus is in JSONL format as specified by the JsonlCorpus class in bm25s/utils/corpus.py.
局限：The method does not support English language input as the current bm25s implementation only supports English.；The method relies on the existing bm25s library and cannot be used independently without the underlying scoring functions.
来源：https://github.com/xhluca/bm25s/tree/03eac5a8fb3aa4958ce37415a4cf2220d35157f7；许可：MIT
证据 bm25s/scoring.py：def _score_idf_bm25plus(df, N):
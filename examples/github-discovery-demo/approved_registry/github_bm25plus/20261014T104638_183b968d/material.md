# BM25Plus

算法：github_bm25plus。任务：memory_retrieval。
输入 query、memories、top_k；输出有序 id、score。

Reproduce BM25Plus from the pinned source scoring functions and constructor defaults. Adapt the retrieve interface and tokenizer only; do not mix in unrelated scoring methods or install dependencies.

模型归纳（未独立认证，不作为评分公式）：The BM25Plus algorithm adapts the standard BM25 scoring by incorporating a delta parameter to adjust document relevance scores. It utilizes a unified tokenization pipeline to convert query and corpus tokens into indices before applying the scoring formula. The implementation requires a custom backend selection mechanism to handle different numerical computation backends efficiently.

适用：k must be less than or equal to the number of available scores in the corpus；delta parameter must be provided to adjust the final relevance score
局限：The algorithm assumes a fixed vocabulary size and does not support dynamic vocabulary expansion；The delta parameter is not automatically calculated and must be explicitly set during initialization
来源：https://github.com/xhluca/bm25s/tree/03eac5a8fb3aa4958ce37415a4cf2220d35157f7；许可：MIT
证据 bm25s/scoring.py：return (num / den) + delta
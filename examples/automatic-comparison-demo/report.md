# 算法能力工厂验证报告

运行：`20261014T161001_92e82969`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
比较 TF-IDF 与 BM25Plus，用于中文与英文混合技术记忆检索。输入 query、memories、top_k=5，返回相关记忆 ID 和分数；保留各方法的评分规则与默认参数，验证接口、稳定性及检索指标，通过后沉淀为可复用插件。

## 方案依据
根据需求，同时启用 tfidf 和 github_xhluca_bm25s 两种算法以覆盖中文与英文混合场景，确保检索精度与稳定性。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | False | — | — | — | — | v01: NameError: name 'cosine_similarity' is not defined; v02: NameError: name 'cosine_similarity' is not defined; v03: NameError: name 'cosine_similarity' is not defined; v04: NameError: name 'cosine_similarity' is not defined; v05: NameError: name 'cosine_similarity' is not defined; v06: NameError: name 'cosine_similarity' is not defined; v07: NameError: name 'cosine_similarity' is not defined; v08: NameError: name 'cosine_similarity' is not defined; v09: NameError: name 'cosine_similarity' is not defined; v10: NameError: name 'cosine_similarity' is not defined; v11: NameError: name 'cosine_similarity' is not defined; v12: NameError: name 'cosine_similarity' is not defined; v13: NameError: name 'cosine_similarity' is not defined; v14: NameError: name 'cosine_similarity' is not defined; v15: NameError: name 'cosine_similarity' is not defined; v16: NameError: name 'cosine_similarity' is not defined; v17: NameError: name 'cosine_similarity' is not defined; v18: NameError: name 'cosine_similarity' is not defined; v19: NameError: name 'cosine_similarity' is not defined; v20: NameError: name 'cosine_similarity' is not defined; v21: NameError: name 'cosine_similarity' is not defined; v22: NameError: name 'cosine_similarity' is not defined; v23: NameError: name 'cosine_similarity' is not defined; v24: NameError: name 'cosine_similarity' is not defined; v25: NameError: name 'cosine_similarity' is not defined; v26: NameError: name 'cosine_similarity' is not defined; v27: NameError: name 'cosine_similarity' is not defined; v28: NameError: name 'cosine_similarity' is not defined; v29: NameError: name 'cosine_similarity' is not defined; v30: NameError: name 'cosine_similarity' is not defined; v31: NameError: name 'cosine_similarity' is not defined; v32: NameError: name 'cosine_similarity' is not defined; v33: NameError: name 'cosine_similarity' is not defined; v34: NameError: name 'cosine_similarity' is not defined; v35: NameError: name 'cosine_similarity' is not defined; v36: NameError: name 'cosine_similarity' is not defined; contract:exact: NameError: name 'cosine_similarity' is not defined; contract:repeat: NameError: name 'cosine_similarity' is not defined; contract:large_k: NameError: name 'cosine_similarity' is not defined; contract:unicode: NameError: name 'cosine_similarity' is not defined; contract:algorithm:0: NameError: name 'cosine_similarity' is not defined |
| tfidf | 1 | False | — | — | — | — | interface: missing top-level retrieve function |
| tfidf | 2 | True | 1.0000 | 0.9537 | 0.2444 | 6.1236 |  |
| github_xhluca_bm25s | 0 | True | 0.9444 | 0.9236 | 0.2333 | 1.7045 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| tfidf | 0 | reproduce_reference | False |  |
| tfidf | 1 | targeted_repair | False |  |
| tfidf | 2 | rebuild_from_reference | False |  |
| github_xhluca_bm25s | 0 | reuse_source_adapter | False |  |

## 冻结后的最终测试

选定：`tfidf` 版本 2
SHA-256：`12c3d600a318031d7b632854c3eb10b69599ca0e6bd1dac819d14abeaf215fe2`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 0.9527777777777777,
  "precision_at_k": 0.25333333333333335,
  "p95_ms": 6.545862345956265
}
```
最终测试通过：True

## 插件与比较规则

算法插件：tfidf@1.0.1, github_xhluca_bm25s@20261014T160901_22b20fbf
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 查询类型分析：tfidf r2 / validation

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 12 | 1.0000 | 0.9444 | 0.2000 | 1 | 0 |
| hard_negative | 8 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| multi_relevance | 8 | 1.0000 | 0.9375 | 0.4000 | 1 | 1 |
| paraphrase | 8 | 1.0000 | 0.9375 | 0.2000 | 1 | 1 |

失败案例（排名错误、漏召回、相近负例混淆）：

- v02：Redis 不存在的 key 一直查数据库；类型 ranking_error；漏召回 []；负例优先 []
- v16：RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm35', 'rank': 1}]
- v32：Git rebase 冲突解决后如何继续，以及共享的错误提交如何撤销；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm32', 'rank': 1}]

## 查询类型分析：github_xhluca_bm25s r0 / validation

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 12 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| hard_negative | 8 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| multi_relevance | 8 | 1.0000 | 1.0000 | 0.4000 | 0 | 0 |
| paraphrase | 8 | 0.7500 | 0.6562 | 0.1500 | 3 | 2 |

失败案例（排名错误、漏召回、相近负例混淆）：

- v16：RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了；类型 missed_relevance, hard_negative_confusion；漏召回 ['m11']；负例优先 [{'id': 'm35', 'rank': 1}]
- v17：Ollama 在 Jetson 自己能用，另一台电脑却连不进去，先查什么；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm37', 'rank': 2}]
- v19：SQLite 两个人同时保存，后一个一直拿不到写机会，怎样缩短等待；类型 missed_relevance；漏召回 ['m19']；负例优先 []

## 查询类型分析：选定版本 / test

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 6 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| hard_negative | 8 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| multi_relevance | 8 | 1.0000 | 1.0000 | 0.4000 | 0 | 0 |
| paraphrase | 8 | 1.0000 | 0.8229 | 0.2000 | 2 | 2 |

失败案例（排名错误、漏召回、相近负例混淆）：

- t08：Redis 许多键约好同一时刻失效，后端瞬间被请求打满怎么办；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm03', 'rank': 2}, {'id': 'm28', 'rank': 3}]
- t12：模型预处理先看了所有数据，测试成绩会不会被提前的信息影响；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm39', 'rank': 1}]

## 可复现性与边界

- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。
- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。
- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。
- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。
- 算法目录来自可信开发者模块或验证后登记清单；真实模型代码默认在禁网 Docker 容器运行，local 不构成可靠安全沙箱。

## 自动工作流选择

提交模式：auto；执行路径：compare_after_discovery
存在未登记的方法，先发现并验证。
模型说明：用户请求比较 TF-IDF 与 BM25Plus 方法，但当前支持的检索方法仅包含字符 BM25、集合 Jaccard 和字符 TF-IDF，未包含 BM25Plus。
未登记的方法：BM25Plus

发现与登记运行：`20261014T160901_22b20fbf`；本报告为随后候选比较。

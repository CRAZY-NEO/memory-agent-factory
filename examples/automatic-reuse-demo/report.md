# 算法能力工厂验证报告

运行：`20261014T161126_0568ab6e`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
为中文与英文混合技术笔记复刻 BM25Plus 检索方法，输入 query、memories、top_k=5，返回相关记忆 ID 和得分，通过验证后交付可复用代码。

## 方案依据
Implement the retrieve interface and tokenizer from bm25s, using the pinned scoring functions to calculate IDF and TF scores for the given query against memories, returning top_k results with ordered IDs and scores.

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| github_xhluca_bm25s | 0 | True | 0.9444 | 0.9236 | 0.2333 | 6.8085 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| github_xhluca_bm25s | 0 | reuse_source_adapter | False |  |

## 冻结后的最终测试

选定：`github_xhluca_bm25s` 版本 0
SHA-256：`cee5c30b688684a1dc315430fc60b323a102d7c56bb1ec09e317c3cbf90b48e0`

```json
{
  "recall_at_k": 0.9666666666666667,
  "mrr_at_k": 0.925,
  "precision_at_k": 0.24666666666666667,
  "p95_ms": 10.084216709947187
}
```
最终测试通过：True

## 插件与比较规则

算法插件：github_xhluca_bm25s@20261014T160901_22b20fbf
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

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
| hard_negative | 8 | 1.0000 | 0.9375 | 0.2000 | 1 | 1 |
| multi_relevance | 8 | 1.0000 | 1.0000 | 0.4000 | 0 | 0 |
| paraphrase | 8 | 0.8750 | 0.7812 | 0.1750 | 2 | 2 |

失败案例（排名错误、漏召回、相近负例混淆）：

- t08：Redis 许多键约好同一时刻失效，后端瞬间被请求打满怎么办；类型 missed_relevance, hard_negative_confusion；漏召回 ['m04']；负例优先 [{'id': 'm28', 'rank': 2}]
- t12：模型预处理先看了所有数据，测试成绩会不会被提前的信息影响；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm39', 'rank': 2}]
- t20：HTTP 429 有 Retry-After，要降低并发还是重新获取认证凭据；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm41', 'rank': 1}]

## 可复现性与边界

- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。
- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。
- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。
- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。
- 算法目录来自可信开发者模块或验证后登记清单；真实模型代码默认在禁网 Docker 容器运行，local 不构成可靠安全沙箱。

## 自动工作流选择

提交模式：auto；执行路径：reuse
已有能力可以作为实现依据，无需联网搜索。
模型说明：用户明确要求复刻 BM25Plus 检索方法，该 ID 在算法目录中已存在，因此 requested_methods 应包含 github_xhluca_bm25s。
未登记的方法：

# 算法能力工厂验证报告

运行：`20261014T182523_907e25f5`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
使用 BM25 为中文与英文技术记忆生成检索算法，返回最相关的前5条记忆

## 方案依据
仅规划 bm25 算法，因其是唯一可用且满足任务要求的算法，无需扩展。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bm25 | 0 | True | 0.9722 | 0.9083 | 0.2389 | 1.6395 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| bm25 | 0 | reproduce_reference | False |  |

## 冻结后的最终测试

选定：`bm25` 版本 0
SHA-256：`a68fae3c50925689492cb3cb78dca5d24c96396b70083db2577e39e4af1c2b4c`

```json
{
  "recall_at_k": 0.9666666666666667,
  "mrr_at_k": 0.9277777777777777,
  "precision_at_k": 0.24666666666666667,
  "p95_ms": 1.546583278104663
}
```
最终测试通过：True

## 插件与比较规则

算法插件：bm25@1.0.0
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 查询类型分析：bm25 r0 / validation

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 12 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| hard_negative | 8 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| multi_relevance | 8 | 1.0000 | 1.0000 | 0.4000 | 0 | 0 |
| paraphrase | 8 | 0.8750 | 0.5875 | 0.1750 | 4 | 4 |

失败案例（排名错误、漏召回、相近负例混淆）：

- v14：Redis 查一个根本没有的数据，每次又去后面的数据库，怎么挡住；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm28', 'rank': 2}, {'id': 'm27', 'rank': 3}]
- v16：RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm35', 'rank': 1}]
- v17：Ollama 在 Jetson 自己能用，另一台电脑却连不进去，先查什么；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm37', 'rank': 2}]
- v19：SQLite 两个人同时保存，后一个一直拿不到写机会，怎样缩短等待；类型 missed_relevance, hard_negative_confusion；漏召回 ['m19']；负例优先 [{'id': 'm43', 'rank': 5}]

## 查询类型分析：选定版本 / test

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 6 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| hard_negative | 8 | 1.0000 | 0.9375 | 0.2000 | 1 | 1 |
| multi_relevance | 8 | 1.0000 | 1.0000 | 0.4000 | 0 | 0 |
| paraphrase | 8 | 0.8750 | 0.7917 | 0.1750 | 2 | 2 |

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
模型说明：用户明确要求使用 BM25 算法进行中文与英文技术记忆的检索，且指定返回前5条结果，该请求符合 memory_retrieval 任务且未提及新算法。
未登记的方法：

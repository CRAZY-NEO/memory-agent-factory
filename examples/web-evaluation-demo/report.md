# 算法能力工厂验证报告

运行：`20261013T195942_1359d9f1`
提供方：**mock**；模型：`mock-template-v1`；执行器：`docker`
状态：**passed**

## 需求
为个人技术记忆库分别复刻 TF-IDF、BM25、Jaccard 三种检索算法并比较。输入 query、memories、top_k，返回前 5 条相关记忆的 ID 与分数。保留中文单字/双字及英文词的算法规则，处理空输入，按配置验证 Recall、MRR、Precision 和稳定性；修复失败版本，选择通过验证的候选并回写知识库。

## 方案依据
比较注册算法 tfidf, bm25, jaccard，按配置的可信验证集指标选择通过检查的方案。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | True | 1.0000 | 0.9537 | 0.2444 | 8.5497 |  |
| bm25 | 0 | True | 0.9722 | 0.9083 | 0.2389 | 1.8335 |  |
| jaccard | 0 | True | 0.9306 | 0.8958 | 0.2278 | 1.0100 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| tfidf | 0 | reproduce_reference | False |  |
| bm25 | 0 | reproduce_reference | False |  |
| jaccard | 0 | reproduce_reference | False |  |

## 冻结后的最终测试

选定：`tfidf` 版本 0
SHA-256：`84fbe993c241919f3a551f0f81a7be700dfa09e16c23ae1a37b746ca7b432d6a`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 0.9527777777777777,
  "precision_at_k": 0.25333333333333335,
  "p95_ms": 7.221256097545844
}
```
最终测试通过：True

## 插件与比较规则

算法插件：tfidf@1.0.1, bm25@1.0.0, jaccard@1.0.1
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 查询类型分析：tfidf r0 / validation

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

## 查询类型分析：jaccard r0 / validation

| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |
| --- | --- | --- | --- | --- | --- | --- |
| direct | 12 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| hard_negative | 8 | 1.0000 | 1.0000 | 0.2000 | 0 | 0 |
| multi_relevance | 8 | 0.9375 | 0.9375 | 0.3750 | 2 | 1 |
| paraphrase | 8 | 0.7500 | 0.5938 | 0.1500 | 4 | 4 |

失败案例（排名错误、漏召回、相近负例混淆）：

- v14：Redis 查一个根本没有的数据，每次又去后面的数据库，怎么挡住；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm28', 'rank': 1}]
- v16：RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm35', 'rank': 1}]
- v17：Ollama 在 Jetson 自己能用，另一台电脑却连不进去，先查什么；类型 missed_relevance, hard_negative_confusion；漏召回 ['m13']；负例优先 [{'id': 'm37', 'rank': 2}]
- v19：SQLite 两个人同时保存，后一个一直拿不到写机会，怎样缩短等待；类型 missed_relevance, hard_negative_confusion；漏召回 ['m19']；负例优先 [{'id': 'm43', 'rank': 3}]
- v32：Git rebase 冲突解决后如何继续，以及共享的错误提交如何撤销；类型 ranking_error, hard_negative_confusion；漏召回 []；负例优先 [{'id': 'm32', 'rank': 1}]
- v33：RAG 的文档怎么分块，之后怎么用 Recall 和 MRR 检查检索；类型 missed_relevance；漏召回 ['m12']；负例优先 []

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
- 插件由开发者编写，真实模型代码默认在禁网 Docker 容器运行；local 不构成可靠安全沙箱。

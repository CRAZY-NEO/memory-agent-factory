# 算法能力工厂验证报告

运行：`20261014T104638_183b968d`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
请为中文与英文混合技术笔记检索发现并复刻一个尚未登记的 BM25Plus 算法。自动在 GitHub 寻找独立算法实现库，依据外部源码保留评分公式和参数，适配 retrieve(query, memories, top_k=5)。通过统一 Docker 验证和最终测试后登记，供后续工厂任务使用。

## 方案依据
从实际 GitHub 搜索结果下载固定提交，依据来源证据复刻新方法：The BM25Plus algorithm adapts the standard BM25 scoring by incorporating a delta parameter to adjust document relevance scores. It utilizes a unified tokenization pipeline to convert query and corpus tokens into indices before applying the scoring formula. The implementation requires a custom backend selection mechanism to handle different numerical computation backends efficiently.

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| github_bm25plus | 0 | False | 0.9444 | 0.9236 | 0.2333 | 1.5675 | algorithm contract: github_bm25plus public example 0 scores or ranking mismatch; algorithm contract: github_bm25plus public example 1 scores or ranking mismatch |
| github_bm25plus | 1 | True | 0.9444 | 0.9236 | 0.2333 | 3.9248 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| github_bm25plus | 0 | external_source_adaptation | False |  |
| github_bm25plus | 1 | external_source_adaptation | False |  |

## 冻结后的最终测试

选定：`github_bm25plus` 版本 1
SHA-256：`a315241e4e438f1eac693ecf13f000b463659cc489c76fc06840c646bb6b0aa7`

```json
{
  "recall_at_k": 0.9666666666666667,
  "mrr_at_k": 0.925,
  "precision_at_k": 0.24666666666666667,
  "p95_ms": 4.034510592464356
}
```
最终测试通过：True

## 插件与比较规则

算法插件：github_bm25plus@20261014T104638_183b968d
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 查询类型分析：github_bm25plus r0 / validation

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

## 查询类型分析：github_bm25plus r1 / validation

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

## GitHub 算法发现与登记

来源：https://github.com/xhluca/bm25s；固定提交：`03eac5a8fb3aa4958ce37415a4cf2220d35157f7`；许可：MIT
搜索词：BM25Plus, BM25
登记结果：True；使用内置算法模板：False

下载仓库未导入或执行，模型根据来源片段复刻代码。验证证明当前数据与接口上的表现，不构成源码数学等价性证明。

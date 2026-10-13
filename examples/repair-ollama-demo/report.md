# 算法能力工厂验证报告

运行：`20261013T183514_adb2a1af`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
请为个人技术记忆库复刻检索算法，分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较三者，不要只选择其中一个来生成。输入 query、memories、top_k，输出相关记忆 ID 与分数，默认前 5 条。每个候选使用自己的评分方法：TF-IDF 使用余弦相似度，BM25 使用词频与逆文档频率，Jaccard 使用词集合交并比；Jaccard 的集合规则仅约束 Jaccard 候选，不能用于排除 TF-IDF 或 BM25。三者都遵循各自的算法插件指导和参考示例。按配置评估 Recall@k、MRR@k、Precision@k 和 P95 查询耗时，同时检查接口、空输入及跨进程稳定性，选择验证通过的方案并将结果沉淀回知识库。


## 方案依据
为个人技术记忆库复刻检索算法，分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较三者，覆盖用户要求比较的所有已启用算法。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | False | — | — | — | — | v01: IndexError: list index out of range; v02: IndexError: list index out of range; v03: IndexError: list index out of range; v04: IndexError: list index out of range; v05: IndexError: list index out of range; v06: IndexError: list index out of range; v07: IndexError: list index out of range; v08: IndexError: list index out of range; v09: IndexError: list index out of range; v10: IndexError: list index out of range; v11: IndexError: list index out of range; v12: IndexError: list index out of range; contract:exact: IndexError: list index out of range; contract:repeat: IndexError: list index out of range; contract:large_k: IndexError: list index out of range; contract:unicode: IndexError: list index out of range; contract:algorithm:0: IndexError: list index out of range |
| tfidf | 1 | False | — | — | — | — | v01: NameError: name 're' is not defined; v02: NameError: name 're' is not defined; v03: NameError: name 're' is not defined; v04: NameError: name 're' is not defined; v05: NameError: name 're' is not defined; v06: NameError: name 're' is not defined; v07: NameError: name 're' is not defined; v08: NameError: name 're' is not defined; v09: NameError: name 're' is not defined; v10: NameError: name 're' is not defined; v11: NameError: name 're' is not defined; v12: NameError: name 're' is not defined; contract:exact: NameError: name 're' is not defined; contract:repeat: NameError: name 're' is not defined; contract:large_k: NameError: name 're' is not defined; contract:unicode: NameError: name 're' is not defined; contract:algorithm:0: NameError: name 're' is not defined |
| tfidf | 2 | True | 1.0000 | 1.0000 | 0.2000 | 4.1009 |  |
| bm25 | 0 | True | 1.0000 | 1.0000 | 0.2000 | 0.6939 |  |
| jaccard | 0 | False | — | — | — | — | v01: TypeError: 'set' object is not subscriptable; v02: TypeError: 'set' object is not subscriptable; v03: TypeError: 'set' object is not subscriptable; v04: TypeError: 'set' object is not subscriptable; v05: TypeError: 'set' object is not subscriptable; v06: TypeError: 'set' object is not subscriptable; v07: TypeError: 'set' object is not subscriptable; v08: TypeError: 'set' object is not subscriptable; v09: TypeError: 'set' object is not subscriptable; v10: TypeError: 'set' object is not subscriptable; v11: TypeError: 'set' object is not subscriptable; v12: TypeError: 'set' object is not subscriptable; contract:exact: TypeError: 'set' object is not subscriptable; contract:repeat: TypeError: 'set' object is not subscriptable; contract:large_k: TypeError: 'set' object is not subscriptable; contract:unicode: TypeError: 'set' object is not subscriptable |
| jaccard | 1 | False | 1.0000 | 1.0000 | 0.2000 | 0.3811 | algorithm contract: jaccard public example 0 scores or ranking mismatch |
| jaccard | 2 | True | 1.0000 | 1.0000 | 0.2000 | 0.6038 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| tfidf | 0 | historical_replay | False | 20261013T172815_22ac726c |
| tfidf | 1 | targeted_repair | False |  |
| tfidf | 2 | rebuild_from_reference | False |  |
| bm25 | 0 | reproduce_reference | False |  |
| jaccard | 0 | historical_replay | False | 20261013T172815_22ac726c |
| jaccard | 1 | targeted_repair | False |  |
| jaccard | 2 | rebuild_from_reference | False |  |

## 冻结后的最终测试

选定：`bm25` 版本 0
SHA-256：`a68fae3c50925689492cb3cb78dca5d24c96396b70083db2577e39e4af1c2b4c`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 1.0,
  "precision_at_k": 0.22000000000000003,
  "p95_ms": 0.7026037987088779
}
```
最终测试通过：True

## 插件与比较规则

算法插件：tfidf@1.0.1, bm25@1.0.0, jaccard@1.0.1
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 可复现性与边界

- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。
- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。
- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。
- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。
- 插件由开发者编写，真实模型代码默认在禁网 Docker 容器运行；local 不构成可靠安全沙箱。

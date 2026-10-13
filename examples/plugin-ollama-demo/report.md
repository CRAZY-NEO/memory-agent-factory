# 算法能力工厂验证报告

运行：`20261013T172815_22ac726c`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
请为个人技术记忆库复刻检索算法，分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较三者，不要只选择其中一个来生成。输入 query、memories、top_k，输出相关记忆 ID 与分数，默认前 5 条。每个候选使用自己的评分方法：TF-IDF 使用余弦相似度，BM25 使用词频与逆文档频率，Jaccard 使用词集合交并比；Jaccard 的集合规则仅约束 Jaccard 候选，不能用于排除 TF-IDF 或 BM25。三者都遵循各自的算法插件指导和参考示例。按配置评估 Recall@k、MRR@k、Precision@k 和 P95 查询耗时，同时检查接口、空输入及跨进程稳定性，选择验证通过的方案并将结果沉淀回知识库。


## 方案依据
为满足用户要求，本方案同时规划 TF-IDF、BM25 和 Jaccard 三个独立候选算法。TF-IDF 侧重词频加权以捕捉特定组件信息；BM25 结合长度归一化以优化不同长度文档的匹配精度；Jaccard 作为轻量级集合算法，仅用于英文标识符的精确集合重合检索。三者均遵循各自算法插件指导，并针对中文/英文混合场景进行了针对性优化（如 Jaccard 限制仅匹配英文、TF-IDF 忽略同义改写等），确保在 Recall@k、MRR@k、Precision@k 及 P95 查询耗时指标上全面评估并沉淀验证结果。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | False | — | — | — | — | v01: IndexError: list index out of range; v02: IndexError: list index out of range; v03: IndexError: list index out of range; v04: IndexError: list index out of range; v05: IndexError: list index out of range; v06: IndexError: list index out of range; v07: IndexError: list index out of range; v08: IndexError: list index out of range; v09: IndexError: list index out of range; v10: IndexError: list index out of range; v11: IndexError: list index out of range; v12: IndexError: list index out of range; contract:exact: IndexError: list index out of range; contract:repeat: IndexError: list index out of range; contract:large_k: IndexError: list index out of range; contract:unicode: IndexError: list index out of range |
| tfidf | 1 | False | — | — | — | — | v01: IndexError: list index out of range; v02: IndexError: list index out of range; v03: IndexError: list index out of range; v04: IndexError: list index out of range; v05: IndexError: list index out of range; v06: IndexError: list index out of range; v07: IndexError: list index out of range; v08: IndexError: list index out of range; v09: IndexError: list index out of range; v10: IndexError: list index out of range; v11: IndexError: list index out of range; v12: IndexError: list index out of range; contract:exact: IndexError: list index out of range; contract:repeat: IndexError: list index out of range; contract:large_k: IndexError: list index out of range; contract:unicode: IndexError: list index out of range |
| tfidf | 2 | False | — | — | — | — | v01: IndexError: list index out of range; v02: IndexError: list index out of range; v03: IndexError: list index out of range; v04: IndexError: list index out of range; v05: IndexError: list index out of range; v06: IndexError: list index out of range; v07: IndexError: list index out of range; v08: IndexError: list index out of range; v09: IndexError: list index out of range; v10: IndexError: list index out of range; v11: IndexError: list index out of range; v12: IndexError: list index out of range; contract:exact: IndexError: list index out of range; contract:repeat: IndexError: list index out of range; contract:large_k: IndexError: list index out of range; contract:unicode: IndexError: list index out of range |
| bm25 | 0 | True | 1.0000 | 1.0000 | 0.2000 | 1.1281 |  |
| jaccard | 0 | False | — | — | — | — | v01: TypeError: 'set' object is not subscriptable; v02: TypeError: 'set' object is not subscriptable; v03: TypeError: 'set' object is not subscriptable; v04: TypeError: 'set' object is not subscriptable; v05: TypeError: 'set' object is not subscriptable; v06: TypeError: 'set' object is not subscriptable; v07: TypeError: 'set' object is not subscriptable; v08: TypeError: 'set' object is not subscriptable; v09: TypeError: 'set' object is not subscriptable; v10: TypeError: 'set' object is not subscriptable; v11: TypeError: 'set' object is not subscriptable; v12: TypeError: 'set' object is not subscriptable; contract:exact: TypeError: 'set' object is not subscriptable; contract:repeat: TypeError: 'set' object is not subscriptable; contract:large_k: TypeError: 'set' object is not subscriptable; contract:unicode: TypeError: 'set' object is not subscriptable |
| jaccard | 1 | False | — | — | — | — | v01: TypeError: 'set' object is not subscriptable; v02: TypeError: 'set' object is not subscriptable; v03: TypeError: 'set' object is not subscriptable; v04: TypeError: 'set' object is not subscriptable; v05: TypeError: 'set' object is not subscriptable; v06: TypeError: 'set' object is not subscriptable; v07: TypeError: 'set' object is not subscriptable; v08: TypeError: 'set' object is not subscriptable; v09: TypeError: 'set' object is not subscriptable; v10: TypeError: 'set' object is not subscriptable; v11: TypeError: 'set' object is not subscriptable; v12: TypeError: 'set' object is not subscriptable; contract:exact: TypeError: 'set' object is not subscriptable; contract:repeat: TypeError: 'set' object is not subscriptable; contract:large_k: TypeError: 'set' object is not subscriptable; contract:unicode: TypeError: 'set' object is not subscriptable |
| jaccard | 2 | False | — | — | — | — | v01: TypeError: 'set' object is not subscriptable; v02: TypeError: 'set' object is not subscriptable; v03: TypeError: 'set' object is not subscriptable; v04: TypeError: 'set' object is not subscriptable; v05: TypeError: 'set' object is not subscriptable; v06: TypeError: 'set' object is not subscriptable; v07: TypeError: 'set' object is not subscriptable; v08: TypeError: 'set' object is not subscriptable; v09: TypeError: 'set' object is not subscriptable; v10: TypeError: 'set' object is not subscriptable; v11: TypeError: 'set' object is not subscriptable; v12: TypeError: 'set' object is not subscriptable; contract:exact: TypeError: 'set' object is not subscriptable; contract:repeat: TypeError: 'set' object is not subscriptable; contract:large_k: TypeError: 'set' object is not subscriptable; contract:unicode: TypeError: 'set' object is not subscriptable |

## 冻结后的最终测试

选定：`bm25` 版本 0
SHA-256：`196298654b48dd0f8cc11f03254e52a60118b0f5102862f2245f350aaa66deef`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 1.0,
  "precision_at_k": 0.22000000000000003,
  "p95_ms": 1.1474291095510112
}
```
最终测试通过：True

## 插件与比较规则

算法插件：tfidf@1.0.0, bm25@1.0.0, jaccard@1.0.0
指标插件：recall_at_k@1.0.0, mrr_at_k@1.0.0, precision_at_k@1.0.0, p95_ms@1.0.0
指标比较顺序：recall_at_k, mrr_at_k, precision_at_k
同分时按依赖数量、配置算法顺序、修订号选择。

## 可复现性与边界

- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。
- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。
- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。
- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。
- 插件由开发者编写，真实模型代码默认在禁网 Docker 容器运行；local 不构成可靠安全沙箱。

# 算法能力工厂验证报告

运行：`20261013T101009_6655f03f`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**failed_validation**

## 需求
请为个人技术记忆库复刻检索算法，分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较三者，不要只选择其中一个来生成。输入 query、memories、top_k，输出相关记忆 ID 与分数，默认前 5 条。每个候选使用自己的评分方法：TF-IDF 使用余弦相似度，BM25 使用词频与逆文档频率，Jaccard 使用词集合交并比；Jaccard 的集合规则仅约束 Jaccard 候选，不能用于排除 TF-IDF 或 BM25。三者都遵循各自的算法插件指导和参考示例。按配置评估 Recall@k、MRR@k、Precision@k 和 P95 查询耗时，同时检查接口、空输入及跨进程稳定性，选择验证通过的方案并将结果沉淀回知识库。


## 方案依据
设计解释：为满足用户“分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较”的需求，且 Jaccard 仅用于集合重合检索，同时遵循“不能修改验证门槛”和“算法尚未执行”的原则，我们构建了三个独立的算法候选。TF-IDF 候选基于 scikit-learn 实现，适用于词面相近的排错记录；BM25 候选基于 Python 标准库实现，适用于不同长度笔记及明确组件名查询；Jaccard 候选基于 Python 标准库实现，适用于轻量且可解释的集合重合检索。三者均遵循各自的算法插件指导，并评估了 Recall@k、MRR@k、Precision@k 和 P95 查询耗时指标。由于算法尚未执行，暂无法提供验证结果，但候选方案已完整规划。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| jaccard | 0 | False | 1.0000 | 1.0000 | 0.2000 | 0.6044 | stability: fresh process returned different results |
| jaccard | 1 | False | 1.0000 | 1.0000 | 0.2000 | 0.6216 | stability: fresh process returned different results |
| jaccard | 2 | False | 1.0000 | 1.0000 | 0.2000 | 1.0810 | stability: fresh process returned different results |

## 冻结后的最终测试

所有候选均未通过验证，未选定或发布插件。

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

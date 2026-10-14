# 算法能力工厂验证报告

运行：`20261014T033355_6999daf6`
提供方：**mock**；模型：`mock-template-v1`；执行器：`docker`
状态：**passed**

## 需求
请为个人技术记忆库复刻检索算法，分别生成 TF-IDF、BM25、Jaccard 三个独立候选并比较三者，不要只选择其中一个来生成。输入 query、memories、top_k，输出相关记忆 ID 与分数，默认前 5 条。每个候选使用自己的评分方法：TF-IDF 使用余弦相似度，BM25 使用词频与逆文档频率，Jaccard 使用词集合交并比；Jaccard 的集合规则仅约束 Jaccard 候选，不能用于排除 TF-IDF 或 BM25。三者都遵循各自的算法插件指导和参考示例。按配置评估 Recall@k、MRR@k、Precision@k 和 P95 查询耗时，同时检查接口、空输入及跨进程稳定性，选择验证通过的方案并将结果沉淀回知识库。


## 方案依据
比较注册算法 tfidf, bm25, jaccard，按配置的可信验证集指标选择通过检查的方案。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | Precision@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | True | 1.0000 | 0.9537 | 0.2444 | 7.4782 |  |
| bm25 | 0 | False | 0.0000 | 0.0000 | 0.0000 | 5.1597 | functional: exact memory text must retrieve its own ID first; metric: recall_at_k=0.0000 below 0.7500; metric: mrr_at_k=0.0000 below 0.6500; metric: precision_at_k=0.0000 below 0.2000 |
| bm25 | 1 | True | 0.9722 | 0.9083 | 0.2389 | 2.8916 |  |
| jaccard | 0 | True | 0.9306 | 0.8958 | 0.2278 | 2.2784 |  |

## 生成与修复轨迹

| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |
| --- | --- | --- | --- | --- |
| tfidf | 0 | reproduce_reference | False |  |
| bm25 | 0 | reproduce_reference | False |  |
| bm25 | 1 | targeted_repair | False |  |
| jaccard | 0 | reproduce_reference | False |  |

## 冻结后的最终测试

选定：`tfidf` 版本 0
SHA-256：`84fbe993c241919f3a551f0f81a7be700dfa09e16c23ae1a37b746ca7b432d6a`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 0.9527777777777777,
  "precision_at_k": 0.25333333333333335,
  "p95_ms": 6.612660607788707
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
| direct | 12 | 0.0000 | 0.0000 | 0.0000 | 12 | 0 |
| hard_negative | 8 | 0.0000 | 0.0000 | 0.0000 | 8 | 0 |
| multi_relevance | 8 | 0.0000 | 0.0000 | 0.0000 | 8 | 0 |
| paraphrase | 8 | 0.0000 | 0.0000 | 0.0000 | 8 | 0 |

失败案例（排名错误、漏召回、相近负例混淆）：

- v01：MySQL 连接池等待导致请求超时怎么排查；类型 missed_relevance；漏召回 ['m01']；负例优先 []
- v02：Redis 不存在的 key 一直查数据库；类型 missed_relevance；漏召回 ['m03']；负例优先 []
- v03：Docker 容器退出 137 内存不足；类型 missed_relevance；漏召回 ['m05']；负例优先 []
- v04：Git rebase 冲突如何处理；类型 missed_relevance；漏召回 ['m07']；负例优先 []
- v05：asyncio 同步请求阻塞事件循环；类型 missed_relevance；漏召回 ['m09']；负例优先 []
- v06：RAG 检索效果用哪些指标评估；类型 missed_relevance；漏召回 ['m11']；负例优先 []
- v07：Jetson Ollama 如何在局域网访问；类型 missed_relevance；漏召回 ['m13']；负例优先 []
- v08：文本分类类别不平衡不能只看 accuracy；类型 missed_relevance；漏召回 ['m16']；负例优先 []
- v09：SQLite 并发写事务被阻塞；类型 missed_relevance；漏召回 ['m19']；负例优先 []
- v10：BM25 和向量检索怎么融合；类型 missed_relevance；漏召回 ['m21']；负例优先 []
- v11：中文 TF-IDF 字符 ngram 余弦相似度；类型 missed_relevance；漏召回 ['m22']；负例优先 []
- v12：API 超时指数退避与幂等重试；类型 missed_relevance；漏召回 ['m18']；负例优先 []
- v13：MySQL 可借用的连接都占着，请求一直排队，怎样让资源释放；类型 missed_relevance；漏召回 ['m01']；负例优先 []
- v14：Redis 查一个根本没有的数据，每次又去后面的数据库，怎么挡住；类型 missed_relevance；漏召回 ['m03']；负例优先 []
- v15：asyncio 里用了同步 HTTP，其他协程全停了，怎样避免等它；类型 missed_relevance；漏召回 ['m09']；负例优先 []
- v16：RAG 怎样知道该找的资料有没有被找回来，第一条是否排对了；类型 missed_relevance；漏召回 ['m11']；负例优先 []
- v17：Ollama 在 Jetson 自己能用，另一台电脑却连不进去，先查什么；类型 missed_relevance；漏召回 ['m13']；负例优先 []
- v18：分类样本里少数类别太少，总体命中率很好但漏掉它们怎么评估；类型 missed_relevance；漏召回 ['m16']；负例优先 []
- v19：SQLite 两个人同时保存，后一个一直拿不到写机会，怎样缩短等待；类型 missed_relevance；漏召回 ['m19']；负例优先 []
- v20：检索只看词面常漏掉换种说法的笔记，BM25 怎样配合语义向量；类型 missed_relevance；漏召回 ['m21']；负例优先 []
- v21：MySQL Access denied，密码或授权主机应该怎样核对；类型 missed_relevance；漏召回 ['m25']；负例优先 []
- v22：Redis 一个热点 key 刚过期就有大量请求回源，互斥重建怎么做；类型 missed_relevance；漏召回 ['m28']；负例优先 []
- v23：Docker 拉镜像报 no space left on device，要清理什么；类型 missed_relevance；漏召回 ['m29']；负例优先 []
- v24：Git 提交已经共享，我想保留历史再产生一笔反向修改；类型 missed_relevance；漏召回 ['m08']；负例优先 []
- v25：Python ImportError cannot import name，锁文件里的包版本不兼容；类型 missed_relevance；漏召回 ['m34']；负例优先 []
- v26：RAG 回答引用了资料却编造结论，应该检查哪种质量；类型 missed_relevance；漏召回 ['m35']；负例优先 []
- v27：API 返回 401，Bearer token 已过期还应该一直重试吗；类型 missed_relevance；漏召回 ['m41']；负例优先 []
- v28：SQLite SELECT 扫描太多行，怎么用执行计划给 WHERE 建索引；类型 missed_relevance；漏召回 ['m43']；负例优先 []
- v29：MySQL 请求超时，如何分别排查连接池耗尽和慢查询索引；类型 missed_relevance；漏召回 ['m01', 'm02']；负例优先 []
- v30：Redis 不存在的数据穿透和许多 TTL 同时失效，两种故障如何保护数据库；类型 missed_relevance；漏召回 ['m03', 'm04']；负例优先 []
- v31：Docker 容器既出现 OOMKilled 又无法访问外部服务，要从内存与网络两边查；类型 missed_relevance；漏召回 ['m05', 'm06']；负例优先 []
- v32：Git rebase 冲突解决后如何继续，以及共享的错误提交如何撤销；类型 missed_relevance；漏召回 ['m07', 'm08']；负例优先 []
- v33：RAG 的文档怎么分块，之后怎么用 Recall 和 MRR 检查检索；类型 missed_relevance；漏召回 ['m11', 'm12']；负例优先 []
- v34：Jetson Ollama 的局域网访问与模型共享内存不足，分别怎样配置；类型 missed_relevance；漏召回 ['m13', 'm14']；负例优先 []
- v35：API 请求超时怎样做幂等退避，同时日志如何避免保存密钥；类型 missed_relevance；漏召回 ['m17', 'm18']；负例优先 []
- v36：个人记忆新旧事实冲突如何保留证据，检索又怎样按租户控制访问；类型 missed_relevance；漏召回 ['m24', 'm46']；负例优先 []

## 查询类型分析：bm25 r1 / validation

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

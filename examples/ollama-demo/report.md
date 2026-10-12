# 算法能力工厂验证报告

运行：`20261012T073909_2ed8189e`
提供方：**ollama**；模型：`qwen3.5:2b`；执行器：`docker`
状态：**passed**

## 需求
请为软件研发人员的个人技术记忆库生成中文与英文混合检索算法。输入 query、memories 和 top_k，返回相关记忆的 ID 和分数，默认返回前 5 条。比较 TF-IDF 和 BM25，验证检索效果、空输入行为、接口规范与运行稳定性，保留验证通过的实现用于未来个人记忆系统。


## 方案依据
为软件研发人员的个人技术记忆库生成中文与英文混合检索算法，比较 TF-IDF 和 BM25 两种算法。TF-IDF 基于词频和逆文档频率，适合处理混合语言且词面相近的记录；BM25 基于词频和文档长度，适合精确匹配组件名或故障词。两种算法均支持空输入返回空列表，并保留 ID 排序规则。

## 候选版本验证

| 算法 | 版本 | 通过 | Recall@k | MRR@k | P95 ms | 错误 |
| --- | --- | --- | --- | --- | --- | --- |
| tfidf | 0 | True | 1.0000 | 1.0000 | 5.6205 |  |
| bm25 | 0 | False | — | — | — | v01: NameError: name 're' is not defined; v02: NameError: name 're' is not defined; v03: NameError: name 're' is not defined; v04: NameError: name 're' is not defined; v05: NameError: name 're' is not defined; v06: NameError: name 're' is not defined; v07: NameError: name 're' is not defined; v08: NameError: name 're' is not defined; v09: NameError: name 're' is not defined; v10: NameError: name 're' is not defined; v11: NameError: name 're' is not defined; v12: NameError: name 're' is not defined; contract:exact: NameError: name 're' is not defined; contract:repeat: NameError: name 're' is not defined; contract:large_k: NameError: name 're' is not defined; contract:unicode: NameError: name 're' is not defined |
| bm25 | 1 | True | 1.0000 | 1.0000 | 1.1683 |  |

## 冻结后的最终测试

选定：`bm25` 版本 1
SHA-256：`336689cd3a839de85b18a4bb41e499928cc87c921a2827b3fccbed41f88ed0f6`

```json
{
  "recall_at_k": 1.0,
  "mrr_at_k": 1.0,
  "p95_ms": 4.649784904904659
}
```
最终测试通过：True

## 可复现性与边界

- 模拟数据、切分与文件哈希已记录。测试结果不进入生成、修复或候选选择。
- 所有报告指标由可信验证器计算，mock 模式只替代 LLM 响应。
- P95 是本次小语料的函数耗时，包含索引构建，不含解释器/依赖导入。单次小样本不可作为生产性能承诺。
- local 是限时子进程及 AST 检查，并非可靠安全沙箱；真实 LLM 默认要求 Docker。
- 这是一套检索算法工厂及可复用插件，个人记忆事实图谱仍需另行构建。

# 分步学习与后续开发

## 第一步：跑通并理解离线闭环

运行 README 的快速开始命令，查看报告和 `workflow.jsonl`。
对照 examples/demo 中 BM25 r0 的错误、diagnosis 与 r1 修复后的报告。
mock 首版故意丢弃所有检索结果，是显式演示故障；其余检查和指标计算均真实执行。
尝试把 `config.json` 的 max_repairs 改为 0，再运行，观察失败候选不能进入比较。

## 第二步：接入 Jetson Ollama

按 mac-jetson-ollama.md 保持 Mac 工厂运行，通过用户建立的 SSH 隧道调用 Jetson 的 qwen3.5:2b。
先做 ingest，检查抽取证据；再做 run，检查角色 JSON、生成代码及验证错误。
真实模型闭环已完成并归档在 examples/ollama-demo；新增插件真实模型记录见 examples/plugin-jaccard-ollama-demo。
模型若无法稳定输出代码或 schema，可选更合适模型或提高提示约束，保留失败记录。

## 第三步：替换为更接近真实的记忆数据

按 id/text/source 接口整理自己愿意使用的技术笔记。人工标注查询和相关 ID。
增加同义改写、模糊描述、多个相关结果与易混淆负例；保持验证与最终测试查询独立。
最终测试结果被用于后续人工优化后，需要重新建立未见过的测试集。
本地查询功能不需要调用 LLM；Ollama 只在算法工厂理解、抽取、生成和修复阶段使用。

## 第四步：接入个人记忆应用

优先用 `selected_retrieval.py` 作为检索模块，或调用 `MemoryRetriever`。
个人记忆应用负责收集记忆、来源、更新时间和权限，再把候选记忆传给检索插件。
然后才加入实体关系抽取、冲突管理、长期记忆更新与回答生成。
算法能力图谱继续保存算法及验证知识；个人事实图谱单独建 schema，按数据 ID 关联。

## 第五步：完成笔试提交

检查归档的真实 Ollama 运行报告；完善更难查询；检查 Git diff；在新环境跑测试和演示。
仓库已推送至 https://github.com/CRAZY-NEO/memory-agent-factory。继续提交时保留 mock 与真实 LLM 证据的区分；新增的 GitHub Actions 检查依赖、测试、打包与 Docker 离线闭环，详见 ci.md。当前 v2 真实模型结果见 examples/ollama-v2-demo；仓库 AST 抽取及工作流集成见 repository-extraction.md。
不要把 .venv、个人记忆、服务密钥或本机 state 数据库提交到公开仓库。

## 可以扩展的接口

- 新检索算法：添加能力材料、参考实现及 AlgorithmSpec 注册模块，在配置中启用，无需修改核心枚举。详见 plugins.md。
- 新指标：添加可信计算函数及 MetricSpec 注册模块，在配置中启用；验证、报告和候选排序自动读取注册信息，模型不能修改可信实现。
- 新任务：增加任务专用的输入输出 schema、可信验证器及数据加载器，再在工作流路由。
- 大语料：把每次查询重建索引改为 build_index/search，单独评估构建时间、查询耗时及索引版本。
- 语义检索：增加向量检索及 RRF 混合候选，同时记录模型、向量维度和资源使用。
- 工作流恢复：添加 LangGraph checkpointer 和幂等阶段标记，避免中断后重复写版本。

算法和指标注册扩展已实现；其余方向仍是后续工作，当前版专注检索任务的完整可复现闭环。

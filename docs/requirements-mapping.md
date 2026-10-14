# 笔试要求与交付对应

## 核心闭环

| 要求 | 当前实现 | 可查看证据 |
| --- | --- | --- |
| 理解自然语言能力描述 | Understander 结构化需求；mock 为规则，真实模式调用 Ollama | examples/demo/llm 中 understander 记录 |
| 材料抽取与相关知识检索 | 三份算法材料、Pydantic、连续原文证据；字符检索+图关系遍历 | knowledge/extracted_capabilities.json、retrieved_knowledge.json |
| 规划算法 | 引用知识节点的配置预算内的候选方案，来源匹配检查 | examples/demo/plan.json |
| 生成代码 | 独立 retrieve 插件；mock 模板、Ollama 可实际生成 | examples/demo/tfidf、bm25、selected_retrieval.py |
| 自动执行与评估 | 可信外部标注评分，接口/功能/稳定性/性能检查 | validation.py、各版本 validation.json |
| 代码修复 | 最多两次，修订和审查信息不可覆盖 | BM25 r0 失败与 r1 成功样例 |
| 验证结果沉淀 | 实现、环境、验证、未验证建议与已验证修复经验回写 | examples/demo/knowledge/graph.json |

## 基础交付

- 代码库：agent_factory；Git 历史已推送至 https://github.com/CRAZY-NEO/memory-agent-factory。
- 模拟行业数据：data，软件研发与运维技术记忆。
- 算法材料及抽取文件：knowledge/sources、knowledge/extracted_capabilities.json。
- 图谱 schema：docs/schema.md。
- Agent 工作流：workflow.py、examples/demo/workflow.mmd。
- 完整生成例子与报告：examples/demo。
- 独立验证脚本：CLI validate、validation.py；自动测试 scripts/run_tests.sh。
- 配置与运行文档：README、config.json、requirements.txt、requirements.lock.txt、pyproject.toml。

## 已实现的进阶点

Web、CLI 与鉴权异步 API；多轮有限修复；三个算法候选比较；插件注册与自动发现；Precision@k 及可配置指标排序；图谱 HTML/GraphML；自动报告；知识回写；
来源证据；AST 安全检查与 Docker 执行选项；失败经验；能力及代码版本；设计依据；性能计时。

真实 Python 仓库的 AST 能力候选抽取：`ingest-repo` 提取函数/方法、签名、注释、import、代码行号、Git HEAD 与内容哈希；扫描结果写回图谱，并作为未验证证据进入规划和当前算法的生成/修复上下文。见 [仓库指南](repository-extraction.md)、[实际抽取 JSON](../examples/repository-demo/extracted_repository.json)及 [AST + Docker 完整报告](../examples/repository-demo/report.md)。不自动执行仓库代码，不把注释或人工算法关联当成正确性证明。

角色协作由同一个模型串行承担，不能将其描述成并发自治 Agent 群。
图遍历用于知识检索，没有实现 Beam Search/MCTS 算法搜索。
检索任务支持算法/指标插件和外部包发现；尚未实现跨任务验证配置。

## 新数据、真实模型与自动检查

1. 已由 Mac 工厂经 SSH 隧道调用 Jetson qwen3.5:2b 完成真实闭环，历史记录保留。最新 [v2 真实报告](../examples/ollama-v2-demo/report.md)使用 48 条记忆、36 条验证查询与 30 条最终测试，共 8 次真实模型调用，三候选首版通过，选定 TF-IDF；本次无修复，历史修复证据仍见 examples/repair-ollama-demo。
2. 数据已增加同义改写、相近负例、多相关文档及分组失败分析，仍为小型模拟集；真实业务专家审核、更大语料和无答案查询是后续方向。
3. 新增 [GitHub Actions](../.github/workflows/ci.yml)：锁定依赖安装、代码检查、全量测试、wheel 资源及独立安装、AST + mock/Docker 闭环，保存诊断产物。该检查无须连接 Jetson；真实模型报告单独归档。远端状态以仓库 Actions 页面为准。

题目允许 mock/规则模板演示，因此当前版本能展示核心闭环；真实 LLM 运行有助于更充分证明代码生成与修复能力。

剩余缺项、容易补齐的功能及较大工作量方向见 docs/roadmap.md；生成与修复改进见 docs/generation-repair.md。

新增 Web 操作界面对应进阶用户体验：见 docs/web-ui.md；扩充评估集与按类型失败分析见 data/README.md、examples/web-evaluation-demo/report.md。原有 CLI/API 保留，数据和指标阈值来源继续记录在报告中。

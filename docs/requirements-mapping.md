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

- 代码库：agent_factory；Git 本地版本控制，提交到 GitHub 需配置用户实际远端。
- 模拟行业数据：data，软件研发与运维技术记忆。
- 算法材料及抽取文件：knowledge/sources、knowledge/extracted_capabilities.json。
- 图谱 schema：docs/schema.md。
- Agent 工作流：workflow.py、examples/demo/workflow.mmd。
- 完整生成例子与报告：examples/demo。
- 独立验证脚本：CLI validate、validation.py；自动测试 scripts/run_tests.sh。
- 配置与运行文档：README、config.json、requirements.txt、requirements.lock.txt、pyproject.toml。

## 已实现的进阶点

CLI 与鉴权异步 API；多轮有限修复；三个算法候选比较；插件注册与自动发现；Precision@k 及可配置指标排序；图谱 HTML/GraphML；自动报告；知识回写；
来源证据；AST 与 Docker 执行选项；失败经验；能力及代码版本；设计依据；性能计时。

角色协作由同一个模型串行承担，不能将其描述成并发自治 Agent 群。
图遍历用于知识检索，没有实现 Beam Search/MCTS 算法搜索。
检索任务支持算法/指标插件和外部包发现；尚未实现跨任务验证配置。

## 提交前仍需补充

1. 已由 Mac 工厂经 SSH 隧道调用 Jetson qwen3.5:2b 完成真实闭环，见 examples/ollama-demo；新插件记录见 examples/plugin-jaccard-ollama-demo。提交时保留原始调用与报告。
2. 扩大和改写检索数据，降低简单词面重合带来的虚高指标。
3. 配置自己的 GitHub 远端并推送；仓库公开前排除私人资料与密钥。

题目允许 mock/规则模板演示，因此当前版本能展示核心闭环；真实 LLM 运行有助于更充分证明代码生成与修复能力。

剩余缺项、容易补齐的功能及较大工作量方向见 docs/roadmap.md；生成与修复改进见 docs/generation-repair.md。

新增 Web 操作界面对应进阶用户体验：见 docs/web-ui.md；扩充评估集与按类型失败分析见 data/README.md、examples/web-evaluation-demo/report.md。原有 CLI/API 保留，数据和指标阈值来源继续记录在报告中。

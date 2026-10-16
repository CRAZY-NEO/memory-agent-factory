# 笔试要求实现情况清单

核对日期：2026-10-16。按笔试原文逐项对照现有代码、文档和归档运行证据。

状态说明：勾选 `[x]` 表示已实现并有对应证据；未勾选 `[ ]` 配合“部分实现”或“未实现”说明缺口。这里的“已实现”指原型范围内完成，不代表生产级能力或任意输入下的正确性保证。进阶要求、加分项与基础要求分开统计。

当前支持**技术记忆检索**与**个人文本记忆分类**，任务为 `memory_retrieval` / `memory_classification`，产出 `retrieve(...)` / `classify(...)`。两任务复用同一 Factory 状态图与知识库，各自使用独立数据契约和验证配置；未实现任意功能代码的通用验证。

归档证据包括检索→文本分类的参考辅助真实模型运行和 ZIP 独立执行。证据：[分类报告](../examples/classification-demo/report.md)、[迁移验收](../examples/transfer_validation.json)、[浏览器验收](../examples/classification_web_validation.json)。之前的自动发现/比较/复用证据继续保留。

新增业务需求的无模板生成已有一次 DeepSeek 完整验收通过：过滤＋去重、关闭历史、一轮修复、最终 Accuracy 91.67% / Macro-F1 0.9167；此前 7B 失败记录仍保留。此前小模型历史开关对照均为 0/6；新增 18 次 DeepSeek 配对中，首版通过为关闭 6/9、开启 9/9，最终均 9/9。只观察到本样本减少修复，不能推断普遍效果，见 [新对照结果](history-ablation.md)；见 [DeepSeek 验收记录](deepseek-api.md#结果与实验边界)和 [历史实验状态](spec-generation-and-experiments.md#2026-10-16-验收状态)。

## 1. 任务要求

- [x] **1. 使用开源模型或 LLM 接口完成理解、抽取、生成或修复。** 支持 Ollama 与 DeepSeek API，保留实际模型调用与运行报告；另有离线 mock 演示。DeepSeek 接入说明见 [配置指南](deepseek-api.md)，各次真实验收状态单独记录。证据：[LLM 接入](../agent_factory/llm.py)、[真实模型三候选报告](../examples/ollama-v2-demo/report.md)、[真实模型发现与修复报告](../examples/github-discovery-demo/report.md)。
- [x] **2. 选择一个具体行业场景。** 选择软件研发与运维知识管理，使用故障排查、工具经验和项目决策笔记作为模拟材料。题目中的场景是举例，并非必须从列举的六种任务中选择。证据：[项目说明](../README.md)、[数据说明](../data/README.md)。
- [x] **3. 构建算法能力知识库或知识图谱。** SQLite 保存能力与历史，NetworkX 表达图关系；包含输入输出、适用条件、指标、依赖、代码版本、验证与失败经验。证据：[schema](schema.md)、[知识存储与检索](../agent_factory/knowledge.py)、[抽取结果](../knowledge/extracted_capabilities.json)。
- [x] **4. 从自然语言描述复刻可运行算法代码。** 网页统一按算法规格生成；CLI/API 保留高级完整参考复刻参数；真实输出仍须验证，功能支持不等于每次生成成功。新方法可通过 GitHub 源码发现路径适配；确定性源码组装单独标记为复用，不计作模型独立生成。标准流程支持检索与分类；GitHub 新方法发现仍限检索。证据：[标准工作流](../agent_factory/workflow.py)、[发现工作流](../agent_factory/discovery.py)、[生成证据说明](spec-generation-and-experiments.md)。
- [x] **5. 统一验证功能、指标、稳定性和接口规范。** 可信验证器执行生成代码，检查接口、边界输入、输出规范、一致性与指标门槛；最终测试前冻结选定代码。证据：[验证器](../agent_factory/validation.py)、[执行器](../agent_factory/runner.py)、[验证测试](../tests/test_validation.py)。
- [x] **6. 提供 UI、CLI 或 API。** 三者均有：Web 提交与结果展示、命令行生成与验证、FastAPI 异步任务接口。证据：[Web 指南](web-ui.md)、[CLI](../agent_factory/cli.py)、[API](../agent_factory/api.py)。

## 2. 完整工作流 a–g

- [x] **a. 理解用户输入的算法能力描述。** Understander 将描述转换为受 Pydantic 契约约束的需求。证据：[工作流](../agent_factory/workflow.py)、[真实调用记录目录](../examples/ollama-v2-demo/llm/)。
- [x] **b. 从文档、代码示例或图谱检索相关能力知识。** 检索能力材料、历史经验和关联节点；已抽取的仓库函数也可作为未验证参考证据。证据：[知识检索](../agent_factory/knowledge.py)、[仓库抽取指南](repository-extraction.md)。
- [x] **c. 规划算法实现方案。** Planner 根据能力来源和候选预算形成方案，并校验来源关联。证据：[示例方案](../examples/ollama-v2-demo/plan.json)。
- [x] **d. 生成可运行代码。** Coder 输出完整 Python 插件，保存各修订与最终选定代码。证据：[真实生成的 TF-IDF](../examples/ollama-v2-demo/selected_retrieval.py)、[外部源码适配的 BM25Plus](../examples/github-discovery-demo/selected_retrieval.py)。
- [x] **e. 自动执行测试与指标评估。** 评分在可信父进程计算，相关性标注不交给执行代码；输出 Recall@k、MRR@k、Precision@k、P95 等结果。证据：[验证器](../agent_factory/validation.py)、[报告样例](../examples/ollama-v2-demo/report.md)。
- [x] **f. 根据错误修复或优化。** Reviewer 分析验证错误，Repairer 在预算内修复，最多两次；已有真实模型修复成功与失败记录。并不保证每次都能修复成功。证据：[修复机制说明](generation-repair.md)、[真实修复记录](../examples/repair-ollama-demo/report.md)、[来源公式修复记录](../examples/github-discovery-demo/report.md)。
- [x] **g. 将验证结果沉淀回知识库或图谱。** 保存实现、环境、验证与经验节点，区分未验证建议和已验证修复；新方法通过验收后可登记复用。证据：[工作流回写](../agent_factory/workflow.py)、[发布与登记](../agent_factory/releases.py)、[示例图谱](../examples/github-discovery-demo/knowledge/graph.json)。

## 3. 开发要求 a–g

- [x] **a. Git 版本控制，提交信息清晰。** 已保留多次功能提交并推送至 [GitHub 仓库](https://github.com/CRAZY-NEO/memory-agent-factory)。本地未提交改动不代表已交付到远端。
- [x] **b. 完整项目文档。** 有项目说明、环境配置、运行指南、架构、schema、插件、Web、仓库抽取与发现指南。入口：[README](../README.md)。
- [x] **c. 模块化结构与注释。** 理解/生成编排、知识管理、插件注册、验证、执行、报告及接口分模块实现，关键边界有注释与说明。证据：[架构说明](architecture.md)、[代码目录](../agent_factory/)。
- [x] **d. 可在本地复现运行。** 提供 Python 环境、锁定依赖、离线 mock、Docker 执行与固定数据；真实模式另需可访问的 Ollama。证据：[快速开始](../README.md#1-快速开始)、[依赖锁文件](../requirements.lock.txt)。
- [x] **e. 建议使用 Python。** 核心编排、算法、验证与服务均使用 Python，Web 页面使用原生 HTML/CSS/JavaScript。证据：[项目配置](../pyproject.toml)。
- [x] **f. 工具选择须说明理由。** README 解释 LangGraph、Ollama、Pydantic、SQLite、NetworkX、FastAPI、Docker 等用途与选择理由；题目列出的框架不要求全部使用。证据：[技术选择](../README.md#2-系统架构与技术选择)。
- [x] **g. 无法部署模型时允许 mock、模板或 API 演示。** 已提供 mock/规则模板模式，代码执行、评分、修复循环与回写真实运行；也已完成真实模型运行。证据：[离线演示报告](../examples/demo/report.md)、[真实模型报告](../examples/ollama-v2-demo/report.md)。

## 4. 基础交付内容

- [x] **完整项目代码库。** [代码目录](../agent_factory/)、[GitHub 仓库](https://github.com/CRAZY-NEO/memory-agent-factory)。
- [x] **示例行业数据、文档或模拟业务材料。** 48 条模拟技术笔记、36 条验证查询、30 条最终测试查询，以及 64/24/24 条分类训练/验证/测试记忆和算法能力材料。证据：[数据目录](../data/)、[算法材料](../knowledge/sources/)。
- [x] **知识图谱 schema 设计说明。** 包含实体、关系、版本与验证状态边界。证据：[schema 文档](schema.md)。
- [x] **知识抽取结果文件。** 包含能力 JSON、仓库 AST JSON、图谱 JSON/GraphML。证据：[能力抽取 JSON](../knowledge/extracted_capabilities.json)、[仓库抽取 JSON](../examples/repository-demo/extracted_repository.json)。
- [x] **Agent 工作流代码或配置。** LangGraph 状态图、条件修复循环与运行轨迹。证据：[工作流代码](../agent_factory/workflow.py)、[示例流程图](../examples/demo/workflow.mmd)。
- [x] **至少一个描述到算法代码生成的完整示例。** 已归档离线、真实模型、仓库抽取与外部方法发现案例。证据：[真实三候选案例](../examples/ollama-v2-demo/)、[BM25Plus 发现案例](../examples/github-discovery-demo/)。
- [x] **自动验证脚本。** CLI `validate` 可独立验证；测试入口统一为 `bash scripts/run_tests.sh`。证据：[CLI](../agent_factory/cli.py)、[测试脚本](../scripts/run_tests.sh)。
- [x] **验证报告样例。** 保存通过/失败状态、候选比较、修订、最终测试、分组指标与失败案例。证据：[真实报告](../examples/ollama-v2-demo/report.md)、[历史失败报告](../examples/plugin-ollama-failure/report.md)。
- [x] **README.md。** [README](../README.md)。
- [x] **requirements.txt 或 environment.yml。** 提供兼容范围及准确版本锁文件。证据：[requirements.txt](../requirements.txt)、[requirements.lock.txt](../requirements.lock.txt)。
- [x] **使用示例和测试用例。** 有命令、请求文件、结果归档与接口/验证/修复/插件/仓库抽取测试。证据：[示例请求](../examples/request.txt)、[测试目录](../tests/)。

## 5. 进阶要求

- [x] **Web 界面、CLI 或 API。** 已提供全部三种入口，Web 支持进度、报告、候选比较和产物下载。证据：[Web 指南](web-ui.md)、[API](../agent_factory/api.py)。
- [x] **多轮代码修复。** 配置默认最多两次修复，保留各版代码与验证结果，并检测重复修改。证据：[配置](../config.json)、[修复代码](../agent_factory/repair.py)。
- [x] **多个候选算法自动比较。** 当前比较 TF-IDF、BM25、Jaccard；验收登记的新方法也能参加后续比较。证据：[三候选真实报告](../examples/ollama-v2-demo/report.md)、[四候选复用报告](../examples/github-reuse-demo/report.md)。后者是 mock + Docker，不能表述为四候选真实模型运行。
- [x] **知识图谱可视化。** 导出可离线打开的 HTML 和 GraphML。证据：[可视化代码](../agent_factory/visualization.py)、[示例图谱](../examples/graph/graph.html)。
- [x] **自动生成验证报告。** 运行时输出结构化结果与 Markdown 报告。证据：[报告模块](../agent_factory/reporting.py)。
- [x] **验证结果回写知识库或图谱。** 代码版本、执行环境、验证与经验均保留。证据：[知识模块](../agent_factory/knowledge.py)、[schema](schema.md)。
- [x] **支持不同任务类型的验证配置。** 检索与文本分类有各自的数据/接口/指标/执行契约，共用 TaskSpec 调度。分类使用训练/验证/测试隔离、Accuracy、Macro-F1 和整批 P95。证据：[任务适配](../agent_factory/tasks.py)、[分类配置](../config/classification.json)、[分类验证器](../agent_factory/classification.py)、[跨任务测试](../tests/test_task_workflow.py)。
- [x] **插件式接入算法模板或评估指标。** `AlgorithmSpec` / `MetricSpec` 支持注册与发现，已新增 Jaccard 和 Precision@k；外部源码方法可通过验证后登记。证据：[注册表](../agent_factory/registry.py)、[插件指南](plugins.md)、[发布模块](../agent_factory/releases.py)。

## 6. 加分项

- [ ] **未实现：图搜索、Beam Search 或 MCTS 用于算法方案搜索。** 当前图遍历用于检索知识，候选方案按配置预算比较；尚无基于方案状态、动作和搜索预算的算法搜索。知识图谱遍历不能代替这项。
- [ ] **部分实现：多智能体协作，而非单次 prompt。** 已有 Extractor、Understander、Planner、Coder、Reviewer、Repairer 的多轮交接，但由同一模型串行承担角色。尚无独立自治 Agent 的工具协商、消息协调或冲突解决。证据：[架构说明](architecture.md)、[工作流](../agent_factory/workflow.py)。
- [x] **从真实仓库抽取函数、依赖和能力描述。** `ingest-repo` 静态提取 Python 函数/方法、签名、注释、import、行号、Git 提交与哈希。import 是依赖线索，不代表已解析所有传递依赖；抽取候选仍为未验证证据。证据：[AST 抽取器](../agent_factory/repository.py)、[指南](repository-extraction.md)、[实际结果](../examples/repository-demo/extracted_repository.json)。
- [x] **代码安全检查与沙箱执行。** AST 检查限制危险行为，真实模型代码使用禁网、只读、非 root、资源受限的 Docker 执行。隔离机制不是绝对安全证明，local 执行器也不是安全沙箱。证据：[安全检查](../agent_factory/safety.py)、[执行器](../agent_factory/runner.py)。
- [x] **自动分析失败并形成可复用经验。** 经验有失败/通过验证、代码哈希与图关系，分类与检索均支持使用。已补历史开关、隔离的配对实验及 18 次 DeepSeek 实际结果；本样本首版通过率有差异，最终通过率相同，见 [对照结果](history-ablation.md)。是否有普遍增益不能由功能打勾推导。证据：[经验机制](../agent_factory/repair.py)、[对照工具](../agent_factory/experiments.py)、[实验指南](spec-generation-and-experiments.md)。
- [x] **算法能力版本管理。** 能力抽取与实现不可覆盖，保存来源/代码哈希、修订关系、验证与发布版本；尚无专门的版本差异 UI。证据：[知识存储](../agent_factory/knowledge.py)、[发布清单](../agent_factory/releases.py)、[schema](schema.md)。
- [x] **自然语言解释方案设计依据。** 方案与报告保存依据和来源关联；模型解释用于说明选择，不作为算法正确性证据。证据：[方案](../examples/ollama-v2-demo/plan.json)、[报告](../examples/ollama-v2-demo/report.md)。
- [x] **跨场景迁移。** 同一 Factory、路由入口与 SQLite 从技术笔记检索扩展到个人记忆单标签分类，复用规划/生成/修复/回写并切换可信验证器；不是在旧检索任务上仅增加算法。证据：[真实双任务验收](../examples/transfer_validation.json)、[双任务验收脚本](../scripts/smoke_transfer.py)、[使用指南](classification-and-export.md)、[端到端测试](../tests/test_task_workflow.py)。范围为两类文本任务，尚无表格/图像任务。
- [x] **自动接口文档或部署配置。** FastAPI 提供 `/docs`、`/openapi.json`；已有 Dockerfile、Compose 与部署指南。已提供成功产物一键 ZIP 导出，包含独立入口、依赖、示例与 Dockerfile；分类包含冻结训练集。证据：[API](../agent_factory/api.py)、[Compose](../compose.yaml)、[部署指南](jetson-deployment.md)。
- [ ] **部分实现：性能优化与资源消耗分析。** 已测查询 P95、执行耗时，并设置容器 CPU/内存限制；尚无 CPU/内存峰值采样、性能剖析、持久索引优化或优化前后基准比较。资源限制不等于资源消耗分析。证据：[性能指标](../agent_factory/plugins/metrics/latency.py)、[执行器](../agent_factory/runner.py)。

## 7. 提交要求：README 中的十项说明

- [x] **1. 项目背景和目标。** README 开头说明行业场景、工厂输入与插件产物。
- [x] **2. 系统架构和模块设计。** README 第 2 节，另有 [架构文档](architecture.md)。
- [x] **3. 图谱 schema 和示例。** README 第 3 节，链接 schema、抽取结果与图谱。
- [x] **4. Agent 工作流设计。** README 第 4 节，说明角色、修复预算与选定规则。
- [x] **5. 环境配置和运行方法。** README 第 1、6 节，说明本地、Docker 与 Ollama 配置。
- [x] **6. 示例数据和测试任务。** README 第 5 节，另有 [数据说明](../data/README.md)。
- [x] **7. 生成算法代码示例。** README 第 4、7 节，链接完整生成代码。
- [x] **8. 验证结果和报告样例。** README 第 7 节，链接离线与真实模型报告。
- [x] **9. 挑战和解决方案。** README 第 9 节及 [生成修复说明](generation-repair.md)。
- [x] **10. 后续扩展方向。** README 第 9 节及 [后续路线](roadmap.md)。

以上入口见 [README](../README.md)。自动路由与默认 Web 操作说明已经同步，见 [自动路由指南](automatic-routing.md)。

## 8. 题目之外的扩展

以下功能有助于演示和使用，但题目没有明确要求自动联网寻找 GitHub 仓库，也没有要求通用功能代码生成。

- [x] **GitHub 搜索 → 固定提交下载 → AST 抽取 → 新方法生成 → 验证 → 登记。** 已完成真实 BM25Plus 案例，保留来源许可、哈希与验收清单。证据：[发现指南](github-discovery.md)、[真实报告](../examples/github-discovery-demo/report.md)。这属于复刻开源方法，不是发明新的数学算法。
- [x] **扩大评估集，加入同义改写与相近负例。** 已使用 v2 数据跑通真实模型；数据仍是小型模拟集，全部查询有答案。证据：[数据说明](../data/README.md)、[真实报告](../examples/ollama-v2-demo/report.md)。
- [x] **GitHub Actions 自动检查。** 配置包含静态检查、测试、打包与 AST + mock/Docker 闭环，不依赖 Jetson；不自动运行真实模型验证。证据：[CI 配置](../.github/workflows/ci.yml)、[CI 说明](ci.md)。
- [x] **自动选择已有能力复用或 GitHub 新方法发现。** 默认自动模式已接入 CLI/API/Web；实际 Chrome 提交两个真实模型任务，完成发现、登记、比较和再次复用，共 18 次 qwen3.5:2b 调用，全部 Docker 验收通过。该次验收的 140 项自动测试及离线 CI 闭环通过。证据：[路由指南](automatic-routing.md)、[发现报告](../examples/automatic-discovery-demo/report.md)、[比较报告](../examples/automatic-comparison-demo/report.md)、[复用报告](../examples/automatic-reuse-demo/report.md)、[浏览器记录](../examples/automatic_web_validation.json)。

- [x] **一键导出独立运行的 ZIP。** 通过 Web/CLI/API 导出已验收代码、最少依赖、示例、Dockerfile、指标摘要和来源许可证，分类附带冻结训练数据。导出前验证哈希与最终测试状态。证据：[导出模块](../agent_factory/exporting.py)、[使用指南](classification-and-export.md)、[独立运行记录](../examples/transfer_validation.json)。

## 9. 尚未完成的重点

- [ ] **用于算法方案搜索的图搜索、Beam Search 或 MCTS。** 当前没有实现这类搜索机制。
- [ ] **更完整的自治多 Agent 协作。** 当前交付是多角色串行工作流。
- [ ] **性能优化与实际资源消耗分析。** 当前只有耗时统计和执行资源限制。

版本差异视图、更多指标、更大或真实业务评估集，以及中断恢复也可继续完善，详见 [后续路线](roadmap.md)。这些额外改进不应全部算成笔试基础要求的缺项。

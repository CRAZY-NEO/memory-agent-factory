# 面向个人记忆检索与分类的算法能力工厂

使用 Agent 把自然语言算法需求转换为可运行、经过验证的记忆算法插件。软件研发与个人知识管理场景包含两项任务：从技术笔记检索相关记忆，以及把个人记忆分成技术知识、项目记录、个人偏好与待办提醒。

工厂输入是自然语言算法需求，产物为 Python 代码、依赖说明、验证报告、版本历史与可独立运行的 ZIP。检索产物提供 `retrieve(query, memories, top_k=5)`；分类产物提供 `classify(texts, training_examples)`。检索与分类保留原接口；新增字段完整性、来源引用检查，通过统一能力契约和任务适配器复用同一套 LangGraph 编排、知识库与有限修复。

**当前可运行模式**：离线 mock/规则模板演示、远端 Ollama 原生 API，以及 DeepSeek 云端 API。DeepSeek 已完成不提供完整模板的分类扩展验收及历史经验开／关对照，见 [DeepSeek 接入与验收](docs/deepseek-api.md) 和 [历史经验对照](docs/history-ablation.md)。mock 只替代模型响应，代码执行、测试、指标和知识回写都真实运行。已通过本机 11435 转发接口调用 `qwen3.5:2b`，在 v2 评估集上完成真实模型抽取、三候选生成、Docker 验证及知识回写，见 [三候选真实模型报告](examples/ollama-v2-demo/report.md)；历史运行保留真实代码修复记录。工厂和算法执行容器运行在 Mac；远端硬件由用户管理，SSH 隧道可通过本地启动器的 --ssh-target 一起启动。

新增信息设计场景：工厂可根据规格生成 `check_fields(payload)` 和 `check_references(payload)`，分别检查字段完整性与引用位置／原文一致性。两项新任务不提供完整参考实现，使用独立评估、隔离运行、统一 SDK 和可移植 ZIP。见 [统一能力工厂与接入指南](docs/capability-factory.md)。当前共四种已登记能力，不是任意功能生成器，尚未实现 MCP 服务或两个业务系统的直接部署。

本次改造已完成四能力真实验收、672 项回归及两项新能力的网页验收，结果与已知边界见 [统一工厂真实验收报告](examples/capabilities/real-demo/report.md)。

## 1. 快速开始

**生成证据说明**：网页统一按“需求 → 检索知识 → 生成 → 验证”提交任务，无须选择有模板或无模板。内部按规格生成（`spec`），不向 Coder/Reviewer/Repairer 提供完整模板；CLI/API 保留 `reference` 高级参数，用于明确需要完整参考实现的复刻或审计。报告记录各版本实际收到的参考和产物来源；系统尚未自动判断何时应提供完整模板。此前归档的真实分类和三算法样例使用完整参考实现，部分输出去除注释后与模板相同，不能据此声称模型独立实现了算法。新增业务要求和历史经验开关对照工具见 [规格生成与实验指南](docs/spec-generation-and-experiments.md)。mock 是模板基线，确定性源码适配是复用，两者均不计为模型独立编程。

已有本地环境可直接使用网页入口，详见 [Web 启动指南](docs/web-ui.md)：

```bash
cd ~/Downloads/agent-factory
.venv/bin/python scripts/serve_web.py
# 自动打开浏览器并登录，刷新保持连接，无须手动复制密钥
# 未配置 FACTORY_PROVIDER 时默认 mock + Docker；.env 可指定 deepseek
# Ollama 可一个终端管理隧道、网页和 worker
.venv/bin/python scripts/serve_web.py --provider ollama --ssh-target kylon@JETSON_IP
```

新机器先获取项目（已有目录可跳过）：

```bash
git clone https://github.com/CRAZY-NEO/memory-agent-factory.git ~/Downloads/agent-factory
```

需要 Python 3.11 或 3.12，建议 3.12。本机项目已创建 `.venv`；已有环境可直接激活并从演示命令开始。

```bash
cd ~/Downloads/agent-factory
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .

# 抽取算法能力知识，保存 SQLite 与 JSON 结果
agent-factory ingest

# 完整演示：首版 BM25 故意返回空结果，可信验证器发现失败后修复
agent-factory --verbose run --request-file examples/request.txt --inject-fault

# 调用工厂已经产出的插件
agent-factory query 'MySQL 连接池耗尽怎么排查' --top-k 3

# 导出能力、代码版本、验证历史与经验
agent-factory export

# 自动测试
scripts/run_tests.sh -q
```

也可使用 `python -m agent_factory` 代替 `agent-factory`。退出状态：通过为 0，验证失败或运行错误为 1。
全局参数 `--workspace /path/to/project`、`--config /path/to/config.json` 和 `--verbose` 必须位于子命令之前。
新机器优先使用准确版本的 `requirements.lock.txt`；`requirements.txt` 与 pyproject 为兼容范围。
该锁文件是在 macOS ARM64 / Python 3.12 上生成的，其他平台应验证对应二进制 wheel 是否可安装。

### 记忆分类与 ZIP 导出

已有环境升级后先重建执行镜像（旧镜像不认识分类接口）：

```bash
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
python -m agent_factory run --runner docker --description '为个人记忆生成文本分类算法，根据训练样本为新记忆预测类别，并比较两个候选'
# 换成上一步返回的实际 run_id
python -m agent_factory export --run-id YOUR_RUN_ID
```

网页点击“填入记忆分类示例”并提交，成功后点击“一键导出 ZIP 部署包”。包内有选定代码、运行入口、依赖、示例、Dockerfile 与验证摘要；分类包还包含冻结的训练文本。解压、安装依赖后即可预测，不需要 Ollama、Jetson 或整个工厂项目。完整说明见 [分类、跨任务复用与导出](docs/classification-and-export.md)。

| 任务 | 候选 | 配置 | 质量指标 |
| --- | --- | --- | --- |
| 技术记忆检索 | TF-IDF、BM25、Jaccard，以及已登记来源算法 | [config.json](config.json) | Recall@k、MRR@k、Precision@k |
| 文本记忆分类 | 字符 TF-IDF + Logistic Regression / MultinomialNB | [分类配置](config/classification.json) | Accuracy、Macro-F1 |

分类使用 [64/24/24 条训练/验证/测试数据](data/classification/README.md)，包含中英文、同义改写和相近负例。生成代码只能使用训练标签；模型调用不接收最终测试文本或标签。GitHub 发现目前仍只支持检索，新任务不能自动继承检索验证器。

## 2. 系统架构与技术选择

| 技术 | 用途与理由 |
| --- | --- |
| Python | 将算法、评估与编排放在同一语言，降低原型复杂度 |
| LangGraph | 显式状态图、条件分支和有限修复循环，工作流容易审计 |
| Ollama + httpx | 对接已有 Jetson 模型服务；原生 chat 支持 JSON schema，无须额外模型部署框架 |
| DeepSeek + httpx | 提供云端生成与修复接口，减少本地模型资源限制；沿用同一套验证门槛 |
| Pydantic | 校验需求、能力抽取、候选方案、代码产物、诊断和插件输出 |
| SQLite | 无额外数据库服务，保存不可覆盖的能力与验证版本 |
| NetworkX | 图遍历、能力到历史经验的关系检索、JSON/GraphML 导出 |
| scikit-learn | TF-IDF、逻辑回归、朴素贝叶斯基线与能力知识检索 |
| Python 标准库 BM25 / Jaccard | 无外部模型依赖的词面检索候选，便于复刻与解释 |
| 插件注册表 | 自动发现可信算法/指标模块，配置预检；统一生成、验证、比较和报告 |
| Docker | 隔离真实模型生成代码，禁网、只读、非 root 与资源限制 |
| pytest | 检验接口、失败分支、修复预算、数据边界和插件复用 |
| FastAPI + SQLite 任务队列 | 提供鉴权业务接口，长时间生成任务异步处理；可信 worker 串行执行 |
| 原生 HTML/CSS/JavaScript | 同源 Web 操作界面，无构建步骤；任务轮询、报告比较与鉴权下载 |

```text
算法材料 → 能力抽取及证据校验 → SQLite 能力图谱
                                        ↓
能力需求 → 理解 → 知识检索 → 规划 → 生成代码 → 可信验证
                                      ↑            ↓
                                      └── 诊断与修复
                                                   ↓
                     候选选择 → 冻结 → 最终测试 → 报告/经验回写
                                                   ↓
                                     可复用的 retrieve / classify 插件与 ZIP
```

模块边界和角色交接详见 [架构说明](docs/architecture.md)。
本版是角色分工明确、串行协作的工作流；没有实现多个自治 Agent 并发协商。

### 从 GitHub 增加新方法

除了复刻已登记的 TF-IDF、BM25、Jaccard，新增 `discover` 工作流可以自动搜索公开 GitHub 算法仓库、下载固定提交、抽取源码证据、生成新检索实现。只有 Docker 验证集和冻结版本最终测试都通过，才将它登记为后续可复用的算法。无需预先添加该方法的模板或 Python 注册模块。

```bash
.venv/bin/python -m agent_factory --verbose discover \
  --provider ollama --model qwen3.5:2b --base-url http://127.0.0.1:11435 \
  --description '发现尚未登记的 BM25Plus 技术笔记检索方法。自动搜索 GitHub 算法源码，保留公式和默认参数，适配 retrieve 接口，验证合格后登记。'
.venv/bin/python -m agent_factory plugins
```

Web、CLI `run` 与工厂 API 默认自动判断：已有能力优先复用，指定未登记方法时自动发现；无须勾选 GitHub。混合需求先发现登记，再比较请求的方法，保留两份报告。高级选项仍可限制执行路径。现有服务需重启，详见 [自动路由](docs/automatic-routing.md)。发现路径不提供内置 BM25 模板：能够从固定来源源码静态提取评分核与适配器时，系统直接组装实现，标记为确定性来源适配或复用，不调用模型回传已有代码；其他受支持的来源生成路径由模型实现评分函数。框架提供算法无关的分词、统计和接口，最终组合代码完整接受验证。来源仓库只被静态读取，不直接导入或安装；提取并适配的代码在执行器中接受检查。完整步骤、登记目录和边界见 [GitHub 算法发现指南](docs/github-discovery.md)。这里增加的是工厂尚未登记的开源方法；这条 GitHub 发现路径仅支持 `memory_retrieval`；分类已有独立标准工作流适配，尚未接入来源发现。

## 3. 能力知识图谱与抽取结果

节点：Source、Capability、Implementation、Environment、ValidationRun、Lesson、FactoryRun，以及 RepositorySnapshot、CodeCandidate、GitHubSnapshot、AlgorithmRelease。
关系连接来源证据、能力、代码修订、依赖环境、验证结果及失败经验。
抽取的 evidence 必须存在于原文；材料与抽取输出使用哈希版本，节点不可覆盖。

- [完整 schema](docs/schema.md)
- [原始算法材料](knowledge/sources/)
- [实际抽取 JSON](knowledge/extracted_capabilities.json)
- [演示图谱 JSON](examples/demo/knowledge/graph.json)
- `agent-factory export` 输出离线 `state/export/graph.html`，浏览器悬停节点可查看属性；同时输出 GraphML。

**能力知识图谱保存算法知识；未来个人记忆图谱保存个人事实和关系，两者分别设计。**

也支持从实际 Python 仓库进行 AST 静态抽取：

```bash
# 扫描已存在的 Git 工作目录中的 Jaccard 实现，不导入或运行被扫描代码
agent-factory ingest-repo . --include 'agent_factory/templates/jaccard.py' --algorithm jaccard
# 同一工作目录中后续 run 会检索这些仓库参考证据
agent-factory run --request-file examples/request.txt
```

输出函数/方法、签名、参数与返回类型、docstring、注释、import、源码行号、文件/代码哈希与 Git HEAD，写入 RepositorySnapshot → Source → CodeCandidate。`--algorithm` 为人工关联，候选始终标记为未验证；不会自动安装依赖或注册执行插件。默认 `spec` 模式只向生成角色提供规格，不传仓库源码或完整模板；抽取证据仍保存在知识库。显式选择 `reference` 模式时，规划使用概要，生成/修复接收当前算法的限长源码参考，并为使用这些参考的生成版本保存 `context_from` 来源边。详见 [仓库抽取指南](docs/repository-extraction.md)、[实际抽取结果](examples/repository-demo/extracted_repository.json)和 [AST + mock/Docker 闭环报告](examples/repository-demo/report.md)；归档报告保留其原有参考复刻方式。

## 4. 工作流与生成算法

Extractor 从材料抽取结构化能力；Understander 理解开发需求；Planner 引用检索到的能力，在配置预算内规划候选（当前三个，最多八个）；Coder 生成 Python；Reviewer 的诊断保存在审计记录中；Repairer 根据可信执行错误、静态发现和公开规格返回完整修订代码，不接收 Reviewer 未验证的自由文本建议。

统一插件接口：

```python
def retrieve(query: str, memories: list[dict], top_k: int = 5) -> list[dict]:
    # memories: [{"id": "m01", "text": "MySQL 连接池排查记录", "source": "notes://m01"}]
    # 返回：[{"id": "m01", "score": 1.23}]
    ...
```

候选：字符 TF-IDF + 余弦相似度；中文单字/双字与英文词的 BM25、Jaccard 集合相似度。
每个候选最多两次代码修复。只有通过验证者能被选中；按配置的指标顺序及插件声明的高低方向比较，同分优先依赖更少，再按配置算法顺序与修订次数选择。
有源码时优先定点修复，保留已经实现的行为；无可用源码、上一轮实现仍未改变，或最后一轮确认最近两次执行没有可观测验证进展时，按当前 `spec` / `reference` 模式重建。定点修复请求先提供上一版源码，再提供可信失败反馈；定点修复与重建都合并重复反馈，同时保留全部失败条件与检查编号，完整证据另存日志。新增需求异常保留安全处理后的类别与源码位置。Coder / Repairer 的连接失败或模型服务错误耗尽响应重试后停止工作流，保留 `failed_generation`，不会继续调用审查和修复来处理断线。已进入算法检查但未通过的情况保留 `failed_validation`。
最终测试失败即停止，不利用测试结果修复或重新选择方案。

可查看 [完整生成代码样例](examples/demo/selected_retrieval.py)、[角色调用记录](examples/demo/llm/) 与 [流程图](examples/demo/workflow.mmd)。
mock 使用仓库内可检查模板，不宣称其能够泛化理解任意需求或自由创作代码。

当前能力边界：2026-10-16 的 DeepSeek 无模板“过滤＋去重”分类验收已通过，一轮代码修复后独立最终测试 Accuracy 91.67%、Macro-F1 0.9167，扩展业务规则全部通过，部署 ZIP 已验证。此前 7B 失败记录仍保留；另已完成 18 次 DeepSeek 历史开／关对照：首版通过为关闭 6/9、开启 9/9，最终均 9/9。小样本显示减少修复，不能推断普遍效果，见 [对照结果](docs/history-ablation.md)。具体条件与全部尝试见 [DeepSeek 验收结果](docs/deepseek-api.md#结果与实验边界)和 [历史实验状态](docs/spec-generation-and-experiments.md#2026-10-16-验收状态)。

## 5. 数据与验证机制

当前 v2 包含 48 条技术记忆、36 条验证查询、30 条全新措辞的最终测试查询；均为项目内编写的模拟资料及标注，无真实个人信息。新增同义改写、相近负例与多相关文档，并附标注理由；v1 数据和历史报告保留。
通过 `scripts/create_demo_data.py` 确定性重建。详见 [数据说明](data/README.md)。
TF-IDF/BM25 在记忆语料上构建统计量，Jaccard 使用词集合，无须训练分类模型；验证/测试的相关文档标注用于评估检索质量。

| 检查维度 | 实现 |
| --- | --- |
| 功能正确性 | 完整记忆文本查询应返回自身；空查询、空语料和非正 top_k 返回空列表 |
| 接口规范 | 参数名与默认值、输出字段、ID 合法性、去重、有限非负分数、降序及同分排序 |
| 指标表现 | Recall@k 为前 k 个结果中找到的相关文档占全部相关文档的比例；MRR@k 为首个相关结果倒数排名的平均值；Precision@k 为相关命中数除以 k 的平均值 |
| 稳定性 | 不修改输入，同一进程重复查询与新进程查询得到一致结果；超时/异常不通过 |
| 性能 | 每次查询函数耗时的 P95，包含索引重建；解释器和依赖导入另计为 worker wall_seconds |
| 来源与可复现性 | 材料证据、代码与数据 SHA-256、Python/依赖版本、候选修订关系、完整调用日志 |
| 分组与失败分析 | 按查询类型统计指标；输出漏召回、首个相关排名与相近负例优先案例；这些是诊断项，总体门槛不变 |

阈值及修复次数位于 `config.json`，由用户配置，Agent 不能修改。项目配置为 Recall≥0.75、MRR≥0.65、Precision≥0.2、P95≤1000ms。Precision 分母固定为 k；样例多数查询只有一个相关文档，Recall@5=1 时 Precision@5=0.2 是合理结果。
可信父进程保留相关 ID 标注并评分，执行器只收到无标注输入。
最终测试的文本、标注和结果不进入本次生成或修复提示，后续能力检索也排除最终测试反馈。
管理员能访问项目数据，因此这是一项系统设计边界，不是对宿主管理员的访问隔离。

独立验证：

```bash
agent-factory validate --code examples/demo/selected_retrieval.py --runner local
```

新评估集已经包含自然改写与相近负例，但仍为小型模拟数据。真实场景还需要业务专家审核、无答案查询、更大语料及真实用户评估。

## 6. 接入真实模型

### DeepSeek API（无需 Jetson）

项目已支持 `--provider deepseek`，默认 `deepseek-v4-pro`。Mac 直接调用官方接口，代码仍在本机 Docker 中执行验证。把 `DEEPSEEK_API_KEY=你的密钥` 保存在项目根 `.env`，不要提交密钥。CLI、Web 启动器与 worker 自动读取 `.env`，已导出的环境变量优先。设置 `FACTORY_PROVIDER=deepseek` 可使普通启动默认使用它。

```bash
# 启动网页；先停止旧网页启动器及其 worker，避免竞争同一队列
.venv/bin/python scripts/serve_web.py --provider deepseek --runner docker

# 新工作空间：完整抽取、规格生成“过滤＋去重”、Docker 验证、有限修复及结果沉淀
.venv/bin/python scripts/run_deepseek_demo.py
```

演示默认不提供模板、关闭跨运行历史，最多两轮修复；通过独立最终测试后才发布插件和 ZIP。失败保留原始结果，不切换 mock。DeepSeek 使用无固定采样 seed 的重复配对协议（`--repeats`）；Ollama 继续使用固定 seed / digest 协议。真实开关对照结果与复验方法见 [历史经验实验](docs/history-ablation.md)。配置、调用边界与验收记录说明见 [DeepSeek 指南](docs/deepseek-api.md)。

### Jetson Ollama（仍可使用）

电脑负责工作流、Docker 验证和知识库，Jetson 只运行 Ollama 模型服务。当前通过 SSH 隧道访问已安装的 `qwen3.5:2b`，本机转发端口为 11435。详细说明见 [Mac + Jetson Ollama 运行指南](docs/mac-jetson-ollama.md)。

```bash
# 第一次准备执行镜像，需要 Docker 正常运行
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .

# 保持 SSH 隧道连接，首次抽取能力知识后运行完整流程
# 单独使用 state/ollama-real 保存真实模型知识库与结果
bash scripts/run_ollama_demo.sh

# 使用独立的新目录，确保载入当前 v2 数据；已有目录不会自动替换历史数据
FACTORY_DEMO_WORKSPACE=state/ollama-v2-new bash scripts/run_ollama_demo.sh
```

使用 Ollama 原生 `/api/chat`，`stream=false`，`think=false`。完整 Coder / Repairer 使用 `python_source_v1`：直接返回 Python 源码，不再把整段代码序列化为 JSON 字符串，也不设置 `format`；其他结构化角色及来源评分函数角色继续使用 JSON schema。默认上下文 8192，单次 HTTP 等待上限 600 秒，每次角色调用最多尝试两次（首次加一次重试）。`llm/` 保留原始响应、响应协议、结束原因、token 用量、耗时和重试错误。
Ollama 上下文容量可通过 `OLLAMA_NUM_CTX` 配置，默认 8192；内存有限时可尝试 4096，并结合实际调用日志核查输入和输出是否完整。该设置调整内存需求，不保证代码正确。

在 Ollama 路径中，当前检索与分类的完整生成/修复角色均使用 4096 输出 token、温度 0.3 和记录的随机种子；`spec`/`reference` 及历史经验 on/off 使用相同采样设置。来源评分函数及其他角色使用 1024 输出 token；Qwen3.5 来源评分函数角色使用温度 1.0，其他结构化角色为温度 0；诊断最多六条建议。默认 `run` 的 `spec` 模式向 Coder、Reviewer、Repairer 提供算法规格、公开行为要求、经筛选的历史经验及本轮反馈，不传原始材料全文、仓库源码或完整模板；显式 `reference` 模式才提供原始材料与完整基线作为复刻辅助。两种模式均要求模型返回完整代码，系统不以模板替换无效响应。`discover` 能静态适配的来源实现直接确定性组装，不调用模型生成包装；其余受支持路径生成评分函数后组合通用接口。实际参数与输入记录在本次调用 JSON 中；此前归档报告反映各自运行时的参考模式和采样设置，不能作为当前默认规格生成的证据。见 [规格生成与实验指南](docs/spec-generation-and-experiments.md)及 [发现与生成说明](docs/github-discovery.md)。
CLI 也读取 `OLLAMA_URL`、`OLLAMA_CHAT_MODEL`、`FACTORY_PROVIDER` 环境变量。
源码响应仅接受原文或一个完整 Python 代码围栏；解包保留内部源码，不改写算法。响应解析后仍由 Pydantic 和 AST 检查，截断、格式或契约错误最多重试一次；重试收到上一份响应及实际错误。拒绝的源码保留供审计和后续修复，但不会执行，也不计为通过验证。历史经验冻结时按声明的协议重新解析原始响应，与记录的响应及源码哈希严格核对；旧 JSON 调用记录仍按旧协议核验。
某次调用失败不会自动切换 mock 并宣称 LLM 成功。
若电脑无法访问，先检查监听地址、局域网连接与防火墙；服务地址和模型未给定前，不能报告真实模型实测结果。
若明确接受本机代码执行边界，可加 `--runner local --allow-local`；默认推荐 Docker。

当前运行结构是 **Mac 工厂 → SSH 隧道 → Jetson Ollama**，算法执行容器和产物均在 Mac。
未来若需要将整个后端迁移到 Jetson，可使用 FastAPI 队列网关及 Jetson 宿主 worker；这是另一种部署方式，配置、启动和接口详见 [Jetson 后端部署指南](docs/jetson-deployment.md)。当前无需 embedding 模型。

## 7. 完整示例与验证报告

[示例需求](examples/request.txt) → [规划](examples/demo/plan.json) → [初版及修复代码](examples/demo/bm25/) → [最终插件](examples/demo/selected_retrieval.py) → [验证报告](examples/demo/report.md)。

报告包括提供方（mock/ollama）、实际指标、所有候选/修订、失败原因、选定代码哈希、独立最终测试与运行环境。
自动测试覆盖两类任务、验证、修复、知识、接口、仓库抽取、自动路由、部署包、规格与参考模式隔离、扩展行为和历史经验对照协议；运行 `bash scripts/run_tests.sh` 可查看当前结果。另有 [算法 Docker 执行记录](examples/docker_validation.json) 和 [实际 HTTP 网关容器验证](examples/api_validation.json)。这两份历史记录使用 mock，真实模型运行使用独立工作目录并记录实际结果。
真实模型例子见 [Ollama 完整报告](examples/ollama-demo/report.md)、[候选规划](examples/ollama-demo/plan.json)、[BM25 首版与修订](examples/ollama-demo/bm25/)、[模型调用记录](examples/ollama-demo/llm/) 和 [最终插件](examples/ollama-demo/selected_retrieval.py)。2026-10-12 的样例调用八次模型；TF-IDF 首版通过，BM25 首版因缺少 `re` 导入失败，模型根据真实错误修复后通过。选定 BM25 修订版的最终测试 Recall@5=1.0、MRR@5=1.0、P95=4.65 ms；这些数值只描述 24 条模拟记忆与 10 条最终测试查询。
所有 JSON 报告可供程序读取；`runs/<run_id>/` 保存新运行；仓库内 `examples/demo/` 保存真实执行后的固定样例。
显式故障注入首版返回空结果，修复后重新执行检查；报告不会预填成功分数。

新增插件完整示例：[三算法 mock + Docker 报告](examples/plugin-demo/report.md)、[三算法真实模型报告](examples/plugin-ollama-demo/report.md)、[真实模型 Jaccard 报告](examples/plugin-jaccard-ollama-demo/report.md)。新报告自动展示 Precision@k 和插件版本，旧的两算法报告作为历史记录保留。Jaccard 真实模型样例的最终测试 Recall@5=1、MRR@5=1、Precision@5=0.22。另一次[真实模型失败记录](examples/plugin-ollama-failure/report.md)保留了候选漏项及未通过稳定性检查的修订，未发布产物。

```bash
# 查看自动发现的算法与指标
.venv/bin/python -m agent_factory plugins
# 三候选真实执行，显式故障触发修复（模型响应为 mock）
bash scripts/run_plugin_demo.sh --inject-fault
# 独立 Jaccard 能力复刻
FACTORY_DEMO_CONFIG=examples/plugins/jaccard-only.json \
FACTORY_DEMO_REQUEST=examples/plugins/jaccard-request.txt \
FACTORY_DEMO_WORKSPACE=state/jaccard-demo bash scripts/run_plugin_demo.sh
```

三算法真实模型运行 `20261013T172815_22ac726c` 共调用模型 13 次，复用已由真实模型抽取的三份能力知识。TF-IDF 三版索引越界，Jaccard 三版错误地切片集合，都被可信验证器拒绝；BM25 首版通过并被选定，最终测试 Recall@5=1、MRR@5=1、Precision@5=0.22、P95=1.15 ms。真实模型的不同次运行可能产生不同质量的代码，单个 Jaccard 通过样例不代表它每次生成都能通过。

2026-10-13 的旧版针对上述失败，增加算法专属上下文、错误源码定位、无效修复检测、第二轮参考实现重新复刻及中英文公开算法契约。[当时的真实复验](examples/repair-ollama-demo/report.md)共 12 次模型调用：重放旧失败代码后，TF-IDF/Jaccard 都在 r2 通过验证，BM25 本次新生成 r0 通过；选定 BM25 的最终测试 Recall@5=1、MRR@5=1、Precision@5=0.22、P95=0.7026 ms。历史重放明确标注，只有修复版本属于本次模型输出。首次复验曾遗漏英文拆字缺陷，记录单独保留；原因、契约补齐过程及当前修复策略见 [生成与修复说明](docs/generation-repair.md)。这些归档成绩不代表当前规格生成流程的验收结果。

三算法演示启用 `compare_all_algorithms=true`，规划及可信检查均要求覆盖全部启用算法，避免模型漏掉候选。新增算法只需材料、参考实现与注册模块；新增指标只需可信计算函数和注册模块。也可通过配置接入已安装的外部插件包，详见 [插件接入指南](docs/plugins.md)。当前支持检索与文本分类及已审查的执行环境，其他任务或新依赖环境需要扩展契约和镜像。

新版网页完整运行见 [v2 评估报告](examples/web-evaluation-demo/report.md)和 [真实浏览器验证记录](examples/web_validation.json)，提供方明确为 mock，算法在 Docker 中实际执行。三候选验证 Recall/MRR 分别为 TF-IDF 1.0000/0.9537、BM25 0.9722/0.9083、Jaccard 0.9306/0.8958，按原规则选定 TF-IDF。最终测试 Recall@5=1、MRR@5=0.9528、Precision@5=0.2533；同义改写仍存在排序与负例混淆。分组和失败明细保留，不修改历史 v1 结果。另有 [新版 Docker 网关验证](examples/web_container_validation.json)，该独立测试的 worker 为 mock/local，与浏览器测试的 mock/Docker 区分。

**v2 数据的真实模型复验已完成**：[qwen3.5:2b + Docker 报告](examples/ollama-v2-demo/report.md)，运行 `20261014T032846_393cdb11`，共 8 次真实模型调用，包括三份材料抽取、需求理解、规划及三次代码生成。三个算法均首版通过，无故障注入、历史重放或本次修复。选定 TF-IDF 后，在 30 条最终测试上得到 Recall@5=1、MRR@5=0.9528、Precision@5=0.2533、P95=7.08 ms。数据为当前 48/36/30 的 v2，已核对数据哈希、模型响应与产物代码哈希；两条最终测试排序错误仍完整保留。该运行未启用随后新增的仓库参考抽取，仓库集成另以离线端到端测试验证。

GitHub 自动检查见 [CI 说明](docs/ci.md)：无需 Ollama 或密钥，检查代码规范、自动测试、安装包资源，以及 Docker 下的 mock 生成、故障修复、独立最终测试和知识回写。真实模型质量以带模型调用记录的报告为证，不由 mock CI 代替。

**GitHub 新算法真实闭环**：[BM25Plus 发现与登记报告](examples/github-discovery-demo/report.md)，运行 `20261014T104638_183b968d`。实际搜索并下载 `xhluca/bm25s` 的固定提交 `03eac5a8fb3aa4958ce37415a4cf2220d35157f7`，使用 qwen3.5:2b 共 7 次调用；首版被来源公式公开数值检查拒绝，r1 修复后通过并自动登记 `github_bm25plus`。最终测试 Recall@5=0.9667、MRR@5=0.9250、Precision@5=0.2467、P95=4.03 ms；结果仍限定于当前 v2 模拟集。

来源文件与许可、AST 结果、模型记录、数值检查以及已批准的登记清单均归档在该示例中，源码原始字节哈希和登记产物哈希已核对。[后续四候选复用报告](examples/github-reuse-demo/report.md)使用 mock + Docker，未手工写新算法注册模块，自动加载 BM25Plus，与三个原有方法一起通过验证；选定 TF-IDF，不宣称新方法更优。[浏览器检查](examples/github_discovery_web_validation.json)验证真实发现报告、来源与代码下载；浏览器检查没有再次提交真实发现任务。

此次验证使用独立 workspace `state/github-discovery-final-20261014`，默认项目的三算法配置保留。查看这次运行可启动 `scripts/serve_web.py --workspace state/github-discovery-final-20261014 --provider ollama --runner docker`；在默认 workspace 提交发现任务时，登记和配置启用发生在默认目录。

本地演示入口已简化为自动打开浏览器并登录，刷新保留会话，可用 --ssh-target 统一启动 SSH 隧道；[实际 Chrome 验证](examples/session_validation.json)涵盖自动会话和 Cookie 鉴权的 mock/Docker 完整流程。API 密钥保留用于远程分享和 CLI 接入，详见 [Web 指南](docs/web-ui.md)。

**自动路由真实 Web 验收**：Chrome 实际提交默认 `auto` 任务，先[发现与登记 BM25Plus](examples/automatic-discovery-demo/report.md)，再[比较 TF-IDF 与 BM25Plus](examples/automatic-comparison-demo/report.md)，随后[再次复用 BM25Plus](examples/automatic-reuse-demo/report.md)，共 18 次真实 qwen3.5:2b 调用，全部使用 Docker。BM25Plus 首版通过；TF-IDF r0/r1 失败，r2 修复通过，比较后按指标选定 TF-IDF。再次请求 BM25Plus 未执行 GitHub 搜索，最终 Recall@5=0.9667、MRR@5=0.9250、Precision@5=0.2467。见 [浏览器检查](examples/automatic_web_validation.json)与[自动路由指南](docs/automatic-routing.md)。

来源依旧为 `xhluca/bm25s` 的固定提交，核与参数由静态源码适配保留，模型生成调用包装。先前验收暴露的方法遗漏、参数遗漏和完整接口重写错误都保留为[失败/撤销审计](examples/automatic-routing-audits.json)，没有将它们当成成功案例。上述登记位于独立测试 workspace，默认项目配置仍为三算法基线。

### 检索 → 记忆分类的真实模型验收

2026-10-15（UTC+8），同一工作空间使用 `qwen3.5:2b` + Docker 完成两类任务，共 13 次实际 API 尝试（其中检索路由遇到一次服务 EOF 后成功重试）：

| 任务 | 结果 | 冻结后的最终测试 |
| --- | --- | --- |
| BM25 技术记忆检索 | r0 通过 | Recall@5 0.9667、MRR@5 0.9278、Precision@5 0.2467 |
| 个人记忆文本分类 | 两候选 r0 均通过；选定 tfidf_logreg | Accuracy 0.9167、Macro-F1 0.9188，24 条测试记忆 |

证据：[检索报告](examples/transfer-retrieval-demo/report.md)、[分类报告](examples/classification-demo/report.md)、[分类代码](examples/classification-demo/selected_classification.py)、[真实模型调用](examples/classification-demo/llm/)、[双任务与独立 ZIP 运行记录](examples/transfer_validation.json)。分类的 7 次调用包括路由、两次抽取、理解、规划和两次代码生成。数据与门槛始终保持固定。

可直接下载示例 [分类 ZIP](examples/classification-demo/deployment.zip) 或 [检索 ZIP](examples/transfer-retrieval-demo/deployment.zip)。两个真实产物解压后均在禁网只读 Docker 内独立运行，分类额外验证了四条新的中文记忆。网页另有 [实际 Chrome + mock/Docker 验收](examples/classification_web_validation.json)；[离线故障修复示例](examples/classification-mock-demo/report.md)显示故障被检测并修复，不将 mock 称为真实模型。

改进前的抽取和代码生成失败见 [审计记录](examples/classification-generation-audits.json)。修正限制了单份材料的抽取范围、原文引用和分类提示体积，未用模板替换真实响应，也未根据失败修改测试标注或降低门槛。

## 8. 用于未来个人记忆系统

生成的算法不需要在查询时再调用 LLM。应用整理好记忆后可直接调用经过检查的生成文件：

```python
# 将通过最终测试的 selected_retrieval.py 放进个人记忆应用，按常规模块导入
from selected_retrieval import retrieve

hits = retrieve("MySQL 连接池耗尽", memories, top_k=5)
```

也可使用保留哈希检查与执行边界的适配器：

```python
from agent_factory.runtime import MemoryRetriever

retriever = MemoryRetriever("runs/YOUR_RUN_ID/plugin.json")
hits = retriever.retrieve("MySQL 连接池耗尽", memories, top_k=5)
```

能复用的部分为 LLM 适配、角色工作流结构、算法插件契约、检索算法、评估集及回归验证方法。
个人事实图谱、记忆冲突管理、自动更新、用户权限与回答生成是后续独立模块。

## 9. 挑战、边界与后续方向

- 控制生成代码风险：AST、受限导入、外部评分、Docker 及资源预算。local 子进程不构成可靠安全沙箱。
- 区分修复建议与事实：图谱中只有修复版本实际通过才记录 validated_fix；失败建议保持未验证标记。
- 保持跨进程稳定：BM25 查询词排序后再累加，避免无序集合造成浮点差异；计时结果不承诺逐次完全一致。
- 防止指标泄漏：候选只用验证集；最终测试在版本冻结后执行。多次人工查看并用于优化后，应重新建立最终测试集。
- 一周实现范围：CLI、鉴权 API 与 Web 界面、持久任务队列、检索/分类候选、按任务配置的指标与排序、有限修复、图检索、离线图谱、版本/经验回写及分组失败分析；暂未实现工作流中断恢复、MCTS、任意任务验证。
- 后续扩展：向量与混合检索、持久化索引、表格或时序任务验证、更严格隔离服务、跨文件语义分析、个人事实图谱。

分步学习和扩展路线见 [实现指南](docs/implementation-guide.md)。

## 10. 开发与提交

```bash
scripts/run_tests.sh -q
ruff check agent_factory scripts tests
git status

# 提交本地修改后，推送到已配置的远端
git push origin main
```

项目已提交至 [GitHub 仓库](https://github.com/CRAZY-NEO/memory-agent-factory)，保留原有提交历史。`.venv`、运行数据库、临时运行目录和密钥不纳入版本控制。
所有演示资料均由本项目编写；外部库遵循各自许可证。

参考：[LangGraph 状态图](https://docs.langchain.com/oss/python/langgraph/graph-api)、[Ollama chat API](https://docs.ollama.com/api/chat)、[scikit-learn TF-IDF](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)、[BEIR 检索评估](https://github.com/beir-cellar/beir)。

生成与修复的新策略、旧失败原因及复验命令见 [生成与修复说明](docs/generation-repair.md)。逐项实现情况见 [笔试要求 checkbox 清单](docs/requirements-mapping.md)；剩余功能及实现成本见 [补齐路线](docs/roadmap.md)。

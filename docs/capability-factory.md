# 面向个人记忆与信息设计的能力工厂

工厂接受能力需求，检索方法与经验，生成 Python 实现，在可信适配器中验证和有限修复，冻结版本后独立测试，交付可调用组件。个人记忆和信息设计系统是能力使用方；目前通过 Python SDK、CLI 或任务 API 接入，没有实现 MCP 服务。

## 当前范围

组件目录、版本选择、定向更新、重写和回退见 [组件生命周期](component-lifecycle.md)。字段任务新增独立契约版本 2；下表原有契约 1 与原接口继续保留。

| 任务 ID | 领域 | 生成入口 | 可信质量门槛 |
| --- | --- | --- | --- |
| memory_retrieval | personal_memory | retrieve(query, memories, top_k=5) | Recall、MRR、可配置业务扩展及稳定性 |
| memory_classification | personal_memory | classify(texts, training_examples) | Accuracy、Macro-F1、可配置业务扩展及稳定性 |
| document_field_completeness | information_design | check_fields(payload) | 18 条验证与 18 条最终测试，整例精确匹配 100% |
| document_source_references | information_design | check_references(payload) | 22 条验证与 22 条最终测试，整例精确匹配 100% |

完整业务验收仍限定于已登记的可信契约和评估适配器。组件入口遇到未登记任务时会继续检索和生成待验证候选，并运行通用安全与执行检查；没有业务验证器时不会认证或自动启用。通用候选暂限定纯 Python JSON 数据处理，详见 [组件生命周期](component-lifecycle.md)。两个文档任务处理现有文档解析器的结构化 JSON，不做 OCR、多模态理解、语义事实核查或网页视觉评分。

## 一套契约与任务适配机制

`CapabilityContract` 表达 task、domain、version、入口、参数与默认值、输入输出 JSON Schema、公开业务规则、执行环境及允许依赖。任务注册表由开发者维护；模型不能注册执行器、编写可信评分器或降低门槛。

`tasks.py` 不再通过“分类还是检索”选择实现。注册的 `TaskAdapter` 负责：

- `load` / `preflight`：读取评估数据，检查标注结构及验证／最终测试不重叠。
- `validate_cases`：只允许未标注输入进入执行器；训练标签只在分类任务允许。
- `evaluate_task`：执行领域功能、质量与稳定性评估。公共 `evaluate` 还检查实际导入是否满足候选声明的依赖。
- `prepare` / `frozen_inputs`：按需冻结训练数据，复核哈希。
- `code_rules`：给模型公开接口和行为约束，不提供评估数据。
- `create_runtime` / `invoke_runtime`：将应用的 JSON 调用映射到既有或新入口。
- `portable_validation` / `example_input`：提供可移植的接口检查和合成使用示例。

统一 Runner 根据可信契约传递入口、参数和默认值；独立 worker 检查签名后调用。新增纯函数任务不需要修改 worker 的任务分支。检索与分类保持原函数接口及冻结训练行为；测试还注册了一个第三方计数适配器，验证执行器不依赖四个内置任务的枚举。

新文档策略 `field_presence` / `exact_citation` 只有算法材料与行为规格，`AlgorithmSpec.reference=None`。真实生成不读取完整基线；`document_baselines.py` 仅用于明确标记的离线 mock。术语 algorithm 在候选字段中保留以兼容现有报告，这里也表示一种组件实现策略。

## 字段完整性

输入为 records（record_id、fields）和有序的 required_fields。必填字段为字面顶层键，不把 `company.name` 当路径。

缺失：键不存在、null、空白字符串、空列表、空字典。0、false、非空列表与非空字典有效，不递归检查容器内容。保留记录及必填字段顺序，返回逐记录 missing_fields / complete 和整体 complete。空记录列表整体为 true。

## 来源引用

输入为 sources（source_id、segments），以及 fields（field_id、references）。每项引用含 source_id、segment_id、start、end、quote。

逐引用按顺序检查并返回第一个适用状态：source_missing → segment_missing → invalid_range → quote_mismatch → valid。文本块只在其来源文件中查找；范围必须满足 `0 <= start < end <= len(text)`，索引按 Python Unicode 字符计数，end 不含。原文切片必须与 quote 完全一致，不做去空白、大小写转换或全文搜索。

字段没有引用为 missing_reference；有引用时全部有效才为 valid，否则为 invalid_reference。保留字段和引用顺序、重复项以及索引。整体 valid 当且仅当所有字段有效，空字段列表为 true。

**位置和引文正确不代表语义上支持字段值。** 业务审查 Agent 仍应判断含义、时间、主体和上下文。

## 运行与接入

已有环境先重建执行镜像：

```bash
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
python -m agent_factory capabilities
python -m agent_factory run --provider deepseek --runner docker \
  --task document_field_completeness \
  --description '生成字段完整性检查组件，按公开契约检查必填字段，0 和 false 有效，保持顺序。'
```

来源检查将 task 换为 `document_source_references`，需求写“生成来源引用检查组件，检查文件、文本块、Unicode 范围与精确引文”。不指定 task 时也支持自然语言自动路由；显式 task 会核对路由结果，防止静默切换。

```bash
# 替换为刚才返回的实际运行 ID
python -m agent_factory invoke --run YOUR_RUN_ID --input examples/capabilities/fields-input.json
python -m agent_factory export --run-id YOUR_RUN_ID
```

应用程序使用统一 SDK：

```python
from agent_factory.runtime import CapabilityPlugin

component = CapabilityPlugin('/absolute/path/to/run/plugin.json')
result = component.invoke(payload)
```

检索输入为 query、memories、可选 top_k；分类输入只有 texts，训练数据由组件内部加载冻结副本，调用方不能替换。文档检查输入直接是各自 payload 对象。SDK 核对契约、代码哈希、通过的验证／最终测试及本地契约文件，再交给可信适配器运行。

HTTP 接入沿用鉴权任务队列：`GET /v1/capabilities` 列出契约，`POST /v1/factory/runs` 提交需求，`POST /v1/capabilities/invoke` 接受 `{"run_id":"...","input":{...}}` 并返回 job_id，结果通过原 jobs 接口读取。网关不执行生成代码。

网页有“字段完整性示例”和“来源引用示例”，同样提交到原 Factory 工作流，展示报告并下载组件 ZIP。

## 验证与交付边界

- 两个文档任务共 80 条固定合成评估样例，标注静态保存，运行时不调用被测代码生成正确答案。涵盖 false/0、嵌套容器、字面键、空输入、Unicode、源内定位、错误优先级、多引用与顺序。
- 每个 split 独立执行三次，检查结构、功能、确定性和输入未修改；金标留在可信父进程。向模型只返回公共 case ID、错误类别、代码位置及聚合指标，不返回探针、预测或金标。
- 最终测试前冻结选定实现。最终测试失败不修复、不换候选、不发布。契约、代码、数据和训练副本有哈希记录；篡改会拒绝调用或导出。
- 依赖门禁检查所有实际导入，包括未使用导入，避免“开发环境能跑、独立交付缺依赖”。
- ZIP 包含生成核心、可信可移植接口检查、契约、声明依赖、示例、Dockerfile 与指标摘要，不包含模型密钥、提示或评估样例。文档包的接口检查使用 Pydantic；其版本随包固定。
- 固定合成集上的通过不证明任意客户文档上的效果，也不证明历史经验有增益。当前真实验收为工程验证，不是新的历史开关实验。

## 复验四种能力

```bash
# 离线模板基线：演示两个新增文档任务，不计模型生成能力
FACTORY_DISABLE_DOTENV=1 python scripts/run_capability_demo.py --provider mock \
  --tasks document_field_completeness,document_source_references --verify-exports

# 真实模型：需要 .env 中的 DeepSeek 配置和 Docker
python scripts/run_capability_demo.py --provider deepseek --verify-exports
```

脚本中的检索和分类配置启用了过滤、去重等额外业务要求，mock 模板不保证满足这些要求；四种能力的完整复验应使用真实模型命令。

输出目录必须是新的；可用 --workspace 指定。每种任务使用独立工作目录，保留调用、全部代码修订、失败记录、验证、最终测试和发布产物。protocol.json 固定任务及源码哈希，integrity.json 检查运行期间源码与 runner 镜像未改变。云端权重无法独立固定，不承诺每次复跑结果相同。

接入两个真实业务系统时，各自负责加载数据、调用组件和处理建议。工厂并未直接连接个人记忆数据库，也未修改信息设计系统的 Agent 工作流。


## 已完成的真实验收

最终结果、保留的失败记录与生成源码样例见 [真实验收报告](../examples/capabilities/real-demo/report.md)。软件回归为 672 项通过；这不替代模型产物的独立质量验收。两种文档能力及保留的检索／分类均完成实际模型生成、独立测试、SDK 调用及交付包 Docker 启动。

# 组件工厂：复用、定向更新、验证与回退

组件层服务个人记忆与信息设计两类场景，但本次未连接两个真实业务系统。它管理经过验证的组件，而不是每次处理业务资料都重新生成代码。

## 决策与边界

一次需求会得到以下动作：

- `reuse`：已有版本满足任务、契约和规则，直接复用，不调用 Coder、不生成新版本。
- `parameterize`：只修改调用默认参数，例如 required_fields。组件代码与版本不变。
- `update`：向模型提供明确标记的旧版源码与新契约，定向修改。报告记录来源、旧代码哈希和代码差异，不计为无参考独立生成。
- `rewrite`：按目标规格重新实现；保留旧版本关联，但不向 Coder 提供旧版完整实现。
- `create`：没有适用版本时创建组件。
- `handle_data`：资料缺字段、引用错误等业务数据问题，交回上游处理，不改代码。
- `draft`：尚无可信业务验证器时，仍检索组件库与公开 GitHub、静态抽取参考、提出接口与假设、生成并有限修复候选。候选基础检查通过后可下载和显式试用，但不自动成为已验证版本。
- `blocked`：完整性/评估版本门禁不满足，或目标算法有歧义等无法安全执行的情况。模型不能登记自己的评分器或认证未知规则。

目录匹配检查任务、契约版本、已验证规则、明确要求的算法及产物完整性。优先保持符合要求的当前绑定；其他版本按可信配置的质量指标排序，平分时选择稳定的已有版本。已损坏版本不参与复用。检索新方法的 GitHub 搜索仍可通过原 Factory 自动发现入口使用；组件目录不把尚未发现/验证的方法当作已具备能力。

缺少业务验证配置不再阻止候选研究和生成。候选保存在 runs 下并以 DraftCandidate 节点回写知识库；组件库检索可以参考已有批准版本或历史候选的完整性已核对源码片段。旧版本证据、许可及来源身份会保留。候选不写入 approved 版本表，不改变 slot，也不能冒充通过独立最终测试。

当前可信规则包括四类任务的基础行为、原有检索/分类的过滤去重扩展，以及新增字段邮箱格式检查。新业务规则仍须开发者提供契约和独立测试；任意自然语言要求不自动获得“已验证”状态。

## 版本和调用位置

组件版本形如 `company-review@1`，对应一个冻结 run、源码、契约、报告、数据哈希及来源版本。SQLite 目录位于 `state/components/catalog.sqlite`。版本证据有哈希校验，调用/切换时重新核对。

业务调用位置（slot）指向某个组件版本，保存默认参数和单调递增的 revision。切换和审计事件在同一事务中提交，通过 expected_revision 防止并发覆盖。重复复用同一版本和相同参数不会重复切换。request_id 防止重试时重复生成或发布；同一个 ID 配不同请求会被拒绝。进程中断后可查询 jobs/目录与审计记录；不宣称自动续跑未完成的模型工作流，未结束的请求应核实后用新 request_id 提交。

契约 1 保留，字段检查契约 2 新增 field_formats、invalid_fields 和 valid。旧版消费者可以继续固定契约 1。契约变化时默认仅交付已验证候选，返回 migration_required，保留当前绑定；显式 allow_contract_change 后才能切换。回退会恢复旧版本及旧默认参数。没有通用的隐式 schema 转换。

## 邮箱规则和可信验证

契约 2 保留完整性语义：键不存在、null、空白字符串、空列表和空字典缺失；0、false 和非空容器不缺失。field_formats 是字段名到 `email` 的有序映射；只检查非缺失值。非字符串或不完整匹配公开正则的值无效，不做 trim：

```text
[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+
```

这是限定的格式规则，不是完整 RFC 标准验证，不证明邮箱存在或可投递。

每条记录输出 missing_fields / complete 和 invalid_fields / valid。complete 只表示必填字段完整；valid 同时要求完整且格式正确。invalid_fields 保持 field_formats 顺序。资料被判为 valid=false 是正常检查结果，不会自动触发返修或回退。

契约 2 有 31 条验证、31 条独立最终测试，包含原完整性金标的扩展，以及手工标注的邮箱格式、非字符串、空值、换行、Unicode、顺序和可选字段案例。金标不由被测代码生成，也不进入模型上下文。

更新/重写先经过原工厂的有限修复、冻结和最终测试，再做旧规则回归与旧实现对新规则的比较。契约 1→2 的回归明确投影到原完整性输出，验证旧行为；不宣称输出 schema 相同。其他同契约更新检查原质量门槛及最终测试的主要质量指标不下降，保留原业务扩展。比较结果不回流修复；失败版本标为 rejected，不替换旧绑定。原始 run 证据保留，不篡改失败记录。

新增报告：`component-comparison.json`、`component-change.diff`。组件调用同时核对契约、源代码和报告哈希。执行异常可在旧版本满足相同契约和全部当前规则时回退；不以牺牲新规则或改变响应 schema 换取“成功”。无效业务入参不会触发自动回退。

## 本地命令

```bash
cd ~/Downloads/agent-factory
source .venv/bin/activate
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .

# 先看计划，不生成组件代码；真实模式仍可能调用需求理解模型
python -m agent_factory components plan --provider deepseek \
  --request-file examples/components/base.json

# 创建；重复同一需求将复用已有版本
python -m agent_factory components ensure --provider deepseek \
  --request-file examples/components/base.json

# 基于旧代码增加邮箱规则；示例明确允许切换到契约 2
python -m agent_factory components ensure --provider deepseek \
  --request-file examples/components/email.json

python -m agent_factory components list
python -m agent_factory components invoke company-review \
  --contract-version 2 --input examples/components/input.json

# 按目标规格重新实现，不提供旧版完整源码
python -m agent_factory components ensure --provider deepseek \
  --request-file examples/components/email.json --action rewrite

python -m agent_factory components history --slot company-review
```

回退命令中的 revision 从目录或上一次结果读取，不要固定使用过时数字：

```bash
python -m agent_factory components rollback company-review --revision CURRENT_REVISION
python -m agent_factory components activate company-review company-review@2 \
  --revision CURRENT_REVISION --allow-contract-change \
  --parameters '{"required_fields":["name","email"],"field_formats":{"email":"email"}}'

# 将原 Factory 已通过测试的 run 纳入目录（run 必须在同一 workspace）
python -m agent_factory components import --run YOUR_RUN_ID --component company-review
python -m agent_factory components feedback company-review --kind data_issue \
  --summary '公司资料缺少邮箱，交回信息提取流程处理'
```

`--provider mock` 是明确的离线模板基线，不证明模型编程能力。默认真实模式使用 Docker；本地执行不提供操作系统隔离。

## 网页与其他 Agent 的工具入口

```bash
python scripts/serve_web.py --provider deepseek --runner docker
```

网页默认只需填写需求、点击“开始执行”，工厂自动匹配、复用或更新。可从“试试示例”选择完整性、邮箱扩展、检索、分类或来源引用任务。成功后显示“试用组件”“下载代码包”“查看验证报告”；试用使用普通表单，JSON 输入收在开发者选项中。

首次试用建议先运行“检查资料完整性”，再选择“为完整性组件增加邮箱检查”。如果更新改变输入输出规范，候选验证通过后页面才展示具体变化和“确认使用新版”。确认前仍调用旧版，不需要预先勾选契约开关。确认使用冻结的候选、参数和原修订号；并发更新会拒绝旧确认，不能覆盖后来版本。

强制重写、组件名称、默认参数 JSON、计划预览收在“高级设置”；版本切换、回退、登记旧 run 和反馈收在“组件与版本管理”。没有启用组件时隐藏调用与切换控件，目录自动刷新。多算法比较和 GitHub 发现保留在“更多工具”中。调用结果与生成报告分开显示。

API 沿用鉴权和任务队列，网关不持有模型密钥、不执行生成代码：

| 接口 | 用途 |
| --- | --- |
| GET /v1/components | 版本、当前绑定、可信规则 |
| POST /v1/components/plan | 查看动作计划 |
| POST /v1/components/ensure | 复用/配置/生成并验证，按请求切换 |
| POST /v1/components/import | 登记原工厂的已验证 run |
| GET /v1/components/history?slot=company-review | 决策、切换、回退和反馈 |
| GET /v1/components/slots/{slot} | 当前版本和 revision |
| POST /v1/components/slots/{slot}/invoke | 指定消费者契约版本调用 |
| POST /v1/components/slots/{slot}/activate | 指定版本及 expected_revision 切换 |
| POST /v1/components/slots/{slot}/rollback | expected_revision 回退 |
| POST /v1/components/slots/{slot}/feedback | 分类反馈；不会仅凭反馈修改代码 |

POST 返回 job_id，通过 GET /v1/jobs/{job_id} 读取结果。job succeeded 表示操作完成；仍须看领域结果 status（例如 rejected / blocked / migration_required），不能把队列完成等同于组件通过。

Python 可使用 ComponentService（同工作目录）或 ComponentClient（HTTP 工具客户端）。客户端提供 catalog、submit、invoke、wait；具体 Agent 框架只需包装这些方法。当前没有修改两个业务系统，没有 MCP 服务，也没有基于真实业务流量的泛化结论。

## 可复现验收

```bash
# 完全离线，执行真实验证、目录和回退；模型输出使用明确的 mock 基线
FACTORY_DISABLE_DOTENV=1 python scripts/run_component_demo.py --provider mock --verify-exports

# 离线模型响应 + 实际 Docker 隔离和独立镜像运行（CI 使用）
FACTORY_DISABLE_DOTENV=1 python scripts/run_component_demo.py --provider mock --runner docker --verify-exports

# 真实模型，fresh workspace；会产生 API 调用费用
python scripts/run_component_demo.py --provider deepseek --verify-exports

bash scripts/run_tests.sh -q
```

脚本检查创建、无生成复用、参数调整、定向更新、旧行为回归、新规则区分、调用、业务数据反馈、回退、旧契约调用、无旧源码重写、幂等重放及独立交付包运行。每次使用新目录，保存 protocol、results、完整模型响应和失败证据，不从旧记录伪造成功。


## 新任务：没有业务验证配置也可生成候选

在同一个网页需求框输入“帮我生成一个文本信息提取组件，提取文本中的所有数字”，点击开始执行。系统提出可审阅的输入输出规格、假设和样例，搜索组件库及公开 GitHub 源码，再生成 `execute(payload) -> dict` 候选。代码使用 re/math/collections 和受限 Python 内建能力；当前通用候选环境面向纯 JSON 数据处理，不保证实现网络服务、OCR 或任意第三方依赖。

通用检查覆盖语法、静态安全、固定接口、JSON 返回、输入不变、重复调用、跨进程稳定性及模型提议样例。模型提出的期望不等于独立业务金标；界面与报告始终单独记录 `business_verified=false`。GitHub 没有合适来源或限流时会保存真实失败原因，继续独立生成，不伪造下载或复刻成功。

检查分开记录 `technical_passed`（安全、接口、执行和稳定性）与 `examples_passed`（本次提议示例的一致性），两者都通过才交付可试用候选。代码能运行但输出不符时，页面显示“示例结果不符”，列出失败输入、期望、实际输出及差异位置；不会把它误报为运行失败。旧报告也可从原有 observations 展示失败示例，不修改已封存证据。

提议规格在生成前冻结，修复不得更改其示例期望。每次返修接收具体失败案例、结构差异与之前通过的案例。最多两轮返修；使用 AST 指纹检测只改注释、空行或 docstring 的重复失败实现，下一轮要求从冻结规格重新实现。即使模型解释“示例不是独立金标”，也不能据此跳过不一致项或宣称修复成功。真正矛盾的规格应保留失败与解释，交给人工澄清。通过这些示例仍不等于业务正确性已被独立证明。

冻结前新增 `draft_spec_reviewer`：接收原始需求、接口、假设和示例输入，**看不到规划者的示例期望和生成代码**，先独立推导每个期望，再由程序比较规划与审查结果。发现矛盾时最多重新规划两次，每版规格及意见都保存；仍有矛盾则停止生成并提示澄清。DeepSeek 的路由、规划和审查默认启用单独的思考配置。两个角色仍使用同一模型，不是独立业务金标，不能据此认证业务正确性。进入生成后不会再改期望来迁就代码。

路由向模型提供完整能力契约，而非仅任务名；推断结果只有“附加规则”而缺少所属基础任务时进入候选流程，不能自动补成检索或分类。`execute(payload)`、`text.strip()` 等普通调用符号不当作新算法名称。这些通用修正来自词频统计与独立文本去重实验，不新增这两个任务的生产模板或验证适配器。

```bash
python -m agent_factory components ensure --provider deepseek --runner docker \
  --description '帮我生成一个文本信息提取组件，提取文本中的所有数字'
# 用返回的 run_id，显式在 Docker 中试用；input.json 内容应符合候选报告中的建议接口
python -m agent_factory components draft-invoke --run YOUR_RUN_ID --input input.json --runner docker
```

网页可点“试用候选”“下载候选代码”“查看候选报告”。候选 ZIP 包含源码、建议规格、基础检查、许可/来源和 README；它是待业务验证的源码交付包，不是已认证的部署包。已验证组件的原有部署导出不变。候选 API 为 `POST /v1/components/drafts/{run_id}/invoke`，与所有组件接口一样要求鉴权并进入 worker 队列。

`job.status=succeeded` 只表示队列处理完毕。网页进度和最近任务依据实际领域结果显示“候选已生成，待业务验证”“示例结果不符”“候选检查未通过”“生成或修复中断”“等待确认切换”等，不把队列完成当作生成成功。

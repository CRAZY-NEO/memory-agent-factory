# 规格生成、业务扩展与历史经验对照

## 生成输入的边界与审计

网页只提供统一的生成流程，普通用户不选择“有模板／无模板”。以下 `spec` / `reference` 是内部上下文策略及高级实验参数，用于明确模型实际得到什么材料，不能将配置名本身当作生成能力的证明。当前不会自动决定是否提供完整模板。

默认 `generation_mode=spec`：Coder、Reviewer、Repairer 只接收算法规格、接口、依赖、公开行为要求、经过验证的结构化经验及本轮执行反馈。完整 `reference_example.code`、仓库源码和来源全文不进入这些角色；Planner 使用经过筛选的能力摘要，Coder 不继承自由文本方案理由。需要重建时使用 `rebuild_from_spec`，不会偷偷提供模板。

`generation_mode=reference` 明确提供完整参考实现，用于复刻或适配。`templates/` 继续作为可直接执行的基线。旧真实运行档案保留原样，其中多份产物与完整参考模板结构相同；它们证明参考复刻闭环，不能单独证明独立代码生成能力。

mock 始终是开发者编写的模板基线，即使配置写了 spec，也会在产物中标注 `mock_template_baseline`。它可能无法满足新增业务要求，这时验证失败是正确行为。实际 Ollama 输出不被模板替换。

已能从固定源码确定性提取评分核与适配器时，GitHub 路径直接组装代码，标记 `deterministic_source_adaptation` / `deterministic_source_reuse`，不再让模型原样回传适配函数。这是来源代码复用，不计为 LLM 独立编程。需要业务扩展时，先登记来源方法，再提交规格生成任务。

## 响应协议与有限修复

标准 Factory 的真实 Coder / Repairer 使用 `python_source_v1`，直接返回完整 Python 源码，不再要求把代码放进 JSON 的 `code` 字符串。其余结构化角色和 GitHub 来源评分函数角色仍使用 JSON schema。源码协议接受原始源码或一个完整的 `python` / `py` 代码围栏；只移除围栏，内部内容原样保留。混合说明文字、多个围栏、截断响应不会被自动拼接或补写为有效代码。

每次角色调用最多两次响应尝试，首次失败后把原响应和实际错误传回模型。源码必须通过响应契约和 AST 检查，后续仍须通过安全、接口、指标及稳定性检查。即使两次响应均被拒绝，只要已解码出源码，就保留源码及对应原始响应路径供审计和修复；该版本标记 `artifact_accepted=false`、`executed=false`，不会进入执行器。系统不会补导入、手改模型代码或用模板替换失败输出。

默认每个候选最多两次算法修复，与上述响应重试分别计数。有可用源码时采用 `targeted_repair`，保留已实现的部分；无源码，或第二轮发现上一轮代码除注释/文档字符串外没有变化时，采用 `rebuild_from_spec` / `rebuild_from_reference`。重建时保留失败代码哈希，不再传错误源码全文。Reviewer 的自由文本建议仅保存在 `diagnosis.json` 和未验证的知识记录中；Repairer 使用可信执行诊断、静态发现和验证反馈，不把 Reviewer 的猜测当作修复指令。

若 Repairer 返回与失败版本 AST 相同的源码，当前响应立即记为无进展，不再把同一源码重复放进第二次响应请求；剩余算法修复轮次按原预算重建。重建前会核验本轮同候选旧版本的文件哈希和 AST，仅在实现一致时恢复其可信运行错误及需求检查，记录 `feedback_origin`。只有源码字节相同才保留旧报错行号；仅 AST 相同时移除旧位置。原拒绝报告仍为 `executed=false`，不会将旧执行冒充本次执行，也不引用最终测试或其他候选的反馈。

定点修复的实际消息按“任务规格 → 模型上一版源码 → 本轮可信失败反馈”排列，末条请求明确要求修复失败行为并保留已通过行为。定点修复与重建都会去除诊断和验证对象中的相同副本；重复错误按完整消息分组，保留全部来源 ID 和原顺序，重复执行诊断还保留各 case 的次数与源码位置。只有分组更短时才使用它。进展和来源记录只向模型发送原因与来源说明，完整哈希、比较依据和未压缩反馈仍保存在原始 `context`；日志另存实际 `messages`。旧源码、新响应和失败行为要求均不被改写。这是提示组织改进，不构成成功率提升的证据。

新增需求检查会保留失败探针的公共 case ID、异常类别和生成源码行号；单类别和空词表异常转换成固定的公共描述。原始异常可能包含私有输入，不直接转发；探针内容、预期答案及局部变量不会进入修复上下文。只有实际发生的异常才产生执行诊断，单纯结果不符不会虚构堆栈。

最后允许的修复轮还检查同候选最近两版的执行报告：代码文件哈希可信、失败错误集合与需求检查集合相同、且质量指标没有改善时，记录 `no_observable_progress` 并转为重建。缺少可核验的执行证据、错误已经变化或质量指标改善时不触发；运行耗时波动不作为算法质量改善。该判断不证明两版程序语义等价，也不扩大修复预算。报告记录参与比较的实现 ID、修订号及源码哈希。

Ollama 请求的上下文容量由 `OLLAMA_NUM_CTX` 设置，默认 8192，最小 1024；内存有限的设备可使用 4096。实际容量写入每次调用的 `options.num_ctx`，日志记录实际消息字符数以及服务返回的输入、缓存和输出 token 计数。字符数不是 token 数；仅凭 `done_reason=stop` 或单个输入计数，不能确认上下文未发生截断。更改上下文是新的实验条件，应保留旧运行并核查实际输入／输出 token、结束原因和服务错误；它不能保证生成代码正确。

Coder / Repairer 的连接失败或模型服务错误耗尽响应重试后，标准 Factory 停止后续审查、修复和候选生成，报告 `failed_generation`。全部候选都未通过响应契约、未进入算法执行时也使用该状态。候选已接受算法检查但没有通过者，报告 `failed_validation`；冻结版本的最终测试失败为 `failed_test`。这些状态均不发布插件。中断后继续执行尚未实现；修复连接后需重新提交，原失败记录保留。

`llm/*.json` 记录 `response_protocol`、`raw_content`、解析后的响应、错误、采样参数、实际 `messages` 和耗时。实验汇总为实际消息保存独立 `wire_messages_sha256`，并检查消息中确实送达的历史经验及模板隔离；旧记录没有消息字段时保留原有上下文审计，不伪造实际消息哈希。冻结经验时按协议重新解码原文，核对完整响应及代码哈希；解码出的源码与产物必须逐字一致，不能仅凭 AST 相同跳过空白或注释差异。缺少协议字段的旧记录按 `json_artifact_v1` 核验，不改写旧档案。协议调整后的真实成功率需另做实验，不能沿用旧版参考复刻成绩。

## 分类规格与可信修复目标

分类接口明确 `texts` 为字符串列表、`training_examples` 为字典列表，按 `text` / `label` 键取值；`enabled` 缺省表示启用。分类规格明确字符 TF-IDF 的 `analyzer="char"` 和 `ngram_range=(1, 3)`，保持插件指定的 LogisticRegression 或 MultinomialNB。`random_state=42` 仅适用于此处的 LogisticRegression，另外两个组件不接收该参数。公共边界要求按执行阶段说明：空预测输入先返回；对训练行执行启用的预处理；有效训练为空时抛错；随后计算多数标签、处理单标签、拟合及预测。两项分类扩展同时启用时，先过滤禁用行，再去重，与用户列出要求 ID 的顺序无关。空白预测文本须保留原位置并使用多数标签回退。

可信验证反馈保留每项失败的 case / contract ID，只去除完全重复的错误，不截去列表尾部或执行诊断。`failed_base_behaviors` 将实际基础契约失败映射为公共行为说明，例如保留输出数量、支持单标签和空词表；新增要求另列为 `failed_required_behaviors`。这些目标来自验证器的失败记录，不采用 Reviewer 的自由建议。异常中包含的具体单个训练标签会脱敏，反馈不携带探针输入、预期标签或逐条预测。

这些补充明确规格与错误定位，不提供完整模板或测试答案；`spec` 的上下文边界、原有门禁及最终测试隔离保持不变。提示与反馈变更后的结果应使用新的实验目录和代码哈希，不能与旧条件下的运行合并计算成功率。

## 模板尚未覆盖的四项需求

| 要求 ID | 接口行为 |
| --- | --- |
| `retrieval_exclude_archived` | 拟合向量器、统计语料和评分前，排除 `archived: true` 记忆。缺省或 false 仍有效。 |
| `retrieval_deduplicate_text` | 按 `text.strip().casefold()` 分组，每组保留字典序最小 ID 的原始记忆；拟合和评分前去重，最后 top_k。若同时启用 `retrieval_exclude_archived`，先排除归档项，再去重。 |
| `classification_ignore_disabled` | 所有统计、回退和训练前排除 `enabled: false` 样本；非空预测输入且无有效训练时抛 ValueError。 |
| `classification_deduplicate_training` | 以精确 `(text,label)` 对去重，保留第一条；相同文本不同标签仍是不同对。若同时启用 `classification_ignore_disabled`，先过滤禁用样本，再去重。 |

基础接口、数据集指标、安全、稳定性与最终测试门禁仍然执行。扩展检查由 `agent_factory/requirements.py` 提供，开发验证和最终测试使用不同的合成文本及 ID；模型只看到行为规范，不会看到探针输入和正确答案。原模板不实现这些要求，扩展失败不能通过降低基础阈值解决。

检索输入允许可选布尔字段 `archived`；分类训练行允许可选布尔字段 `enabled`，不能使用字符串 `"false"`。包含禁用行的训练集必须显式开启对应要求。精确重复的训练文本只在开启训练去重要求时允许；跨训练、验证、测试的文本泄漏仍会拒绝。冻结训练集、运行时插件和导出 ZIP 保留 `enabled`，不会在进入模型代码之前偷偷过滤或去重，因此实际接受检查的是生成的实现。导出包包含这些训练行，分享前应检查其内容。

```bash
# 根据规格生成检索函数，并真正验证归档过滤及去重
python -m agent_factory run --provider ollama --model qwen3.5:2b \
  --base-url http://127.0.0.1:11435 --runner docker \
  --generation-mode spec --history-mode on --seed 11 \
  --requirement retrieval_exclude_archived --requirement retrieval_deduplicate_text \
  --description '用 Jaccard 检索技术记忆，排除归档项并按规范化文本去重'

# 分类使用独立的任务配置，接口参数不变
python -m agent_factory run --provider ollama --model qwen3.5:2b \
  --base-url http://127.0.0.1:11435 --runner docker \
  --generation-mode spec --history-mode off \
  --requirement classification_ignore_disabled --requirement classification_deduplicate_training \
  --description '用 TF-IDF + Logistic Regression 分类记忆，忽略 enabled=false 的训练样本并按精确文本标签对去重'
```

Web 固定提交 `generation_mode=spec`，即使旧工作区配置为 `reference` 也不会自动提供完整模板；高级选项保留历史经验开关和扩展要求。CLI/API 保留明确复刻与实验所需的输入策略参数；API `POST /v1/factory/runs` 接受 `generation_mode`、`history_mode`、`model_seed`、`requirements`。网页报告逐版本展示实际参考输入；离线模板和 `deterministic_source_adapter` 来源代码复用单独标记，不计为模型独立生成。任务不匹配会拒绝，不能默默忽略扩展。独立 CLI `validate --requirement ID` 也会执行扩展门禁。

## 历史经验机制

`history_mode=off` 从 Planner、Coder、Reviewer、Repairer 的上下文中排除跨运行经验及历史验证结果，本轮错误反馈和有限修复仍然开启。on 保留相关任务/算法的精简经验；分类不再在上下文压缩时丢弃经验。

成功修复经验必须有之前失败、之后通过的验证结果、对应代码哈希和图关系。经验规则从实际代码变化归纳，不把 Reviewer 的猜测直接当成已验证事实。旧版仅记录“修复成功”而无可用规则的经验不会用于新实验。

经验入库和进入提示词只说明机制工作；是否提高生成成功率必须通过下面的实验判断。一次修复成功、同一个请求反复跑通或 mock 故障注入均不能替代对照实验。

## 可复现的成对实验

先在独立工作空间用真实模型完成失败到成功的修复。历史失败代码可通过 `--repair-from` 重放，但要明确 r0 是历史代码，不能记为本次生成。没有真实且可验证的经验时，冻结工具会拒绝，不会自动造经验。

```bash
# 经验来源和结果建议放在仓库外；下面路径均替换为实际目录
python scripts/run_ablation.py freeze \
  --workspace /path/to/warmup-retrieval --workspace /path/to/warmup-classification \
  --output /path/to/frozen-history

python scripts/run_ablation.py run \
  --snapshot /path/to/frozen-history --output /path/to/ablation-results \
  --seeds 11,29 --provider ollama --model qwen3.5:2b \
  --base-url http://127.0.0.1:11435 --runner docker --max-repairs 2
```

上述 Ollama 协议默认三种业务需求、两个种子，共六对、十二次生成。每一对使用相同算法、数据、经验快照、参数和修复预算，仅切换历史经验；使用独立知识库副本，交替执行顺序。经验写回不会污染后续组。协议保存代码、数据与知识快照哈希；若修改了任何实验条件，必须新建输出目录，不能混入旧结果。

输出包含 `protocol.json`、全部 `raw-results.json`、每次模型调用与生成代码、`aggregate.json`。统计首版验证通过率、修复预算内最终测试通过率、修复次数、模型调用、token、耗时，保留失败与传输错误。上下文审计检查 off 无历史、on 确实收到冻结经验、spec 无完整参考代码。

首版通过允许模型响应格式的既有一次重试，统计定义会在报告中明确。不同任务、有限种子的结果只构成小样本工程观察；提升、无差异和下降均应保留，不预设结论。模板与模型代码差异可以辅助审查，但 AST 不同不是质量指标，也不能代替行为验证。

## 2026-10-16 验收状态

在接入 DeepSeek 前，软件回归为 503 项通过，代码检查通过；这不等于模型产物通过。针对无模板的分类“过滤禁用行＋按 `(text, label)` 去重”，`qwen2.5-coder:7b-instruct-q4_K_M` 在 4096 上下文、历史关闭、最多两轮修复的最新固定 seed29 复验中仍为 `failed_validation`（运行 `20261015T184844_18dab00c`）。首版混淆内部元组与字典，第一轮返回相同执行逻辑而被拒，最后重建仍错误解包训练字典。未进入最终测试，也未发布插件。此次七次调用均返回 HTTP 200，源码与模型版本在运行前后保持一致。

已修正反馈遗漏、无效修复后的反馈丢失、输入类型说明和重复消息；当前模型与配置仍未可靠完成组合任务。服务返回的缓存计数已保留，但不能未经确认地与输入计数相加或相减，也不能据此排除上下文容量影响。完整原始实验及模板对比按项目约定保留在仓库外，不以手改输出或模板替换获得“成功”。随后接入 DeepSeek，保持原业务验收门槛进行新验收，结果见下文。

此前 qwen3.5:2b 的历史经验对照中，启用与关闭均为 0/6 最终通过，该组实验没有显示增益。这次 7B 工程复验关闭历史且修改过流程，不能替代新的配对实验。

## DeepSeek 提供方

普通生成与修复可选择 `deepseek`，采用相同的源码协议、可信验证器与有限修复预算。结构化角色使用 JSON Object 输出并在本地校验 Pydantic schema；源码角色不使用 JSON 包装。`thinking`、结束原因、服务返回模型名/响应 ID、原始 usage 及实际消息保留在 trace，密钥不入日志。

DeepSeek 不发送 Ollama 的 `seed` / `num_ctx` 参数；报告中的 `model_seed=null`、`seed_supported=false`，请求配置另存。云端模型权重不能像本地 Ollama digest 一样独立锁定。DeepSeek 新增 `cloud_repeated_pairs_v1` 重复配对协议，以 `--repeats` 标识重复次数；Ollama 保留固定 seed/digest 协议。实际消息中的历史正文与冻结内容逐项核验，云端身份仍标记为不可独立锁定。已验证的云端修复可作为带实际消息和原文哈希的经验来源。详见 [DeepSeek API 配置](deepseek-api.md)。

2026-10-16 DeepSeek 的无模板组合任务已完整通过（运行 `20261016T030536_8cce6d5f`）：思考关闭、历史关闭，6 次模型调用；首版因单类别分支使用未开放的 `next` 失败，一轮自动修复后通过验证集、独立最终测试与发布。最终 Accuracy / Macro-F1 均为 0.9167，扩展规则在两个 split 各 12/12 通过。首次余额不足和开启思考时的截断/主动停止记录均保留，详情见 [验收结果](deepseek-api.md#结果与实验边界)。云端成功与此前本地 7B 失败属于不同模型和推理配置的工程观察，不能作受控因果比较。

## 2026-10-16 历史经验新对照

已按预注册计划完成 3 类分类需求 × 3 组重复 × 开／关，共 18 次 DeepSeek 真实运行。关闭组首版 6/9、最终 9/9、修复 3 次；开启组首版 9/9、最终 9/9、无修复。3 次首版失败都来自沙箱未开放的 `next`，与冻结历史所记录的环境兼容错误一致。当前样本显示首版通过差异与修复成本减少，不能说明最终通过率增加、统计显著或跨任务泛化。完整协议、结果、限制与命令见 [历史经验对照](history-ablation.md)。

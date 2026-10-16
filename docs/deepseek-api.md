# DeepSeek API 接入与复验

Mac 运行 Factory、网页和 Docker 验证器，DeepSeek 官方接口负责模型推理。此模式不需要 Jetson 或 SSH 隧道；原 Ollama 模式仍可选择。

## 配置一次

在项目根目录新建 `.env`（文件名就是 `.env`，不要保留 `.example` 后缀），填写：

```dotenv
FACTORY_PROVIDER=deepseek
DEEPSEEK_API_KEY=在本机填写自己的密钥
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_THINKING=disabled
DEEPSEEK_MAX_TOKENS=8192
FACTORY_RUNNER=docker
```

CLI、网页启动器和 worker 自动读取工作目录的 `.env`，已有环境变量优先，不需要手动 `source`。`.env` 被 Git 和 Docker 构建上下文忽略；密钥不要放在命令参数、截图、报告或仓库文件中。配置文件作为数据解析，不执行命令替换或变量展开。`run_deepseek_demo.py` 与插件演示脚本会先读取项目根 `.env`，然后创建独立工作目录。

默认模型按 2026-10-16 [官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)设置为 `deepseek-v4-pro`，也可通过 `DEEPSEEK_MODEL` 切换官方账户可用的模型。URL 仅允许官方 HTTPS 主机及可选 `/v1` 路径，禁止重定向携带凭证。

## 启动网页

先停止旧的网页启动器及其 worker，避免两个 worker 竞争同一任务队列。只改端口不能隔离工作队列。

```bash
cd ~/Downloads/agent-factory
.venv/bin/python scripts/serve_web.py --provider deepseek --runner docker
```

启动器先检查 `/models`，随后启动网关、worker 并打开网页。网关仅处理任务队列，进程环境中不保留 `DEEPSEEK_API_KEY`；worker 调用模型。Docker 执行器使用环境白名单，生成代码不接触密钥。网页访问密钥与 DeepSeek API Key 是两种不同凭证，前者仍由启动器自动管理。

如需同时保留旧服务，必须指定不同的 `--workspace` 和端口，并在该工作目录准备配置、数据与能力材料。

## 单次完整验收

保持 Docker Desktop 可用，首次先准备执行镜像：

```bash
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
.venv/bin/python scripts/run_deepseek_demo.py
```

脚本每次创建新的 `state/deepseek-demo-时间戳/`，拒绝覆盖已有目录；也可用 `--workspace /绝对路径/新目录` 指定输出位置。它使用同一分类数据和原有门槛，只启用字符 TF-IDF + Logistic Regression，需求为先排除 `enabled=false`，再按精确 `(text, label)` 去重。生成模式为 `spec`、历史为 `off`，最多两轮修复。

执行顺序是：真实能力抽取 → 理解需求 → 知识检索 → 规划 → 生成 → Docker 验证 → 必要时修复 → 选定后执行一次独立最终测试 → 回写知识库。完整模板不会发送给代码生成角色，失败响应不会被模板替换。只有通过最终测试才发布插件和部署 ZIP。每次输出包含协议、原始模型调用、运行结果与源码完整性检查；失败也保留。

普通 CLI 同样支持 DeepSeek：

```bash
.venv/bin/python -m agent_factory --config config/classification.json run \
  --provider deepseek --runner docker --mode reuse \
  --generation-mode spec --history-mode off \
  --requirement classification_ignore_disabled \
  --requirement classification_deduplicate_training \
  --description '实现记忆分类；先过滤禁用训练样本，再按完全相同的文本和标签去重'
```

该 CLI 示例保留分类配置中的两个候选；单次验收脚本只验证一个候选，便于限定调用预算。

## 响应与失败处理

按 [官方 Chat Completions 协议](https://api-docs.deepseek.com/api/create-chat-completion/)调用 `/chat/completions`。结构化角色使用 `response_format=json_object`，提示中包含 schema，本地继续做 Pydantic 校验；Coder / Repairer 使用纯源码协议。单独返回的 `reasoning_content` 保存为审计字段，不会拼进可执行源码。

默认关闭思考模式，输出上限 8192。此次真实调用在开启思考时两次耗尽 8192 token，均未返回源码；关闭后完成生成和修复。可显式设置 `DEEPSEEK_THINKING=enabled` 开启思考，此时使用 `reasoning_effort=high` 且不传温度；关闭时完整源码角色温度为 0.3，其余为 0。思考与最终内容共享输出预算，应保留截断记录并调整配置后在新目录复验。`DEEPSEEK_MAX_TOKENS` 可设为 1–32768。系统不发送 Ollama 的 `seed`、`num_ctx`、`format`、`think` 或 `options`。

每个角色最多两次响应尝试。格式或语法错误可在预算内重试；HTTP 429/5xx 与网络错误最多重试一次。401/402/403 等账户或请求错误立即停止，避免继续花费修复调用。异常及 HTTP 错误正文会去除密钥；认证头不写日志。仅正常结束的完整响应可进入后续检查，截断或其他异常结束不算成功。

- 401/403：检查 Key 与账户权限。
- 402：接口明确返回余额不足，需在该 Key 所属开放平台账户补充余额；模型列表查询成功不代表账户能完成生成。
- 429/5xx：额度、并发或服务异常；保留失败记录后再决定是否重跑。
- `length`：输出不完整；不会擅自拼接或补写源码。

## 结果与实验边界

`llm/*.json` 保存请求模型、返回模型、响应 ID、服务指纹、原始 usage、归一 token 统计和实际消息。报告标注 `model_seed=null`、`seed_supported=false`，请求配置值单独保留；云端模型权重无法按本地 Ollama digest 独立核验。

普通生成与修复支持 DeepSeek；`run_ablation.py` 已增加无固定采样 seed 的重复配对协议，使用 `--repeats`，Ollama 继续使用 `--seeds`。真实修复经验须核验来源、原始响应、源码和实际消息后冻结。18 次 DeepSeek 对照中，首版通过为关闭 6/9、开启 9/9，最终均 9/9；本样本减少了修复，但未显示最终成功率提升，详见 [实验结果与复验](history-ablation.md)。

2026-10-16 真实验收 `20261016T030536_8cce6d5f` 已通过：`deepseek-v4-pro`、思考关闭、spec 无模板、history off，6 次实际调用，一轮代码修复。首版的过滤和去重逻辑已实现，但单类别分支调用了沙箱未开放的 `next`；修订版改用已允许的列表访问后通过。

| 检查 | 结果 |
| --- | --- |
| 验证集（24 条） | Accuracy 95.83%，Macro-F1 0.9580 |
| 独立最终测试（24 条） | Accuracy 91.67%，Macro-F1 0.9167 |
| 过滤＋去重业务检查 | 验证与测试各 15 个探针、12 条规则全部通过 |
| 最终批处理 P95 | 17.19 ms（含训练与预测，不含导入） |
| 发布与部署 | 插件、ZIP 已生成；ZIP 在无网络 Docker 中独立运行通过 |

64 条训练样本、验证集和最终测试集分离；验收阈值仍为 Accuracy ≥ 0.75、Macro-F1 ≥ 0.70、P95 ≤ 2000 ms。原模板在同一验证集上分类指标达标，但新增业务规则只有 4/12 通过，不能直接交付该组合需求。生成源码与原始模型响应一致，没有手改或模板替换。

全部尝试保留在仓库外：第一次被 HTTP 402 阻塞；第二次开启思考，两次 Coder 输出截断，在后续修复请求期间主动停止；第三次关闭思考完成验收。第二次不是完整算法验收失败，也不能将配置调整后的单次成功解释为统计成功率或历史经验收益。原始记录中的一条经验将需求失败误归因于过滤/去重修改；实际修改仅为 `next` 白名单兼容，后续经验提炼已改为保守记录，原始证据保持不变。

详细原始记录和模板对比按项目约定存于仓库外；复制仓库后可运行上述单次验收脚本，生成自己的记录。

最终接入与归因修复后的软件回归：587 项通过（2026-10-16）；Ruff 与差异格式检查通过。软件回归与上述真实模型验收分别记录。

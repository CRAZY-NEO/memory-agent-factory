# 自动路由：复用与发现

Web、CLI `run` 和 `POST /v1/factory/runs` 默认使用 `auto`。直接描述需要的检索能力，无须勾选 GitHub 发现入口。当前任务类型仍是 `memory_retrieval`。

## 选择规则

| 需求 | 行为 |
| --- | --- |
| 普通检索，未指定方法 | 使用配置中启用的候选，生成、比较与验证 |
| 指定已登记的方法 | 本次只生成和比较指定方法，无须 GitHub 搜索 |
| 指定一个未登记的方法 | GitHub 搜索、固定提交下载、AST、源码适配、验证及登记 |
| 比较已有方法和一个未登记的方法 | 先发现及登记，再运行所请求方法的候选比较 |
| 登记后再次请求同一方法 | 复用其材料与已验证实现，再生成和验证 |
| 分类、预测等其他任务 | 拒绝，需对应的数据契约和验证器 |

模型提取意图，确定性代码对照真实注册表决定路径。名称按精确边界匹配，BM25 不匹配 BM25Plus/BM25+。原文中的明确复刻/比较表达补充有限范围的方法名，记录为 `literal_method_mentions`，防止模型因名称不在目录而遗漏它。模型提取的名称也必须出现在原文或对应已提到的注册别名，否则最多纠正一次，仍错误则停止。

原文规则覆盖有限的命名表达，模型理解也可能出错。建议明确写出方法，例如“比较 TF-IDF 与 BM25Plus，用于技术记忆检索”；业务实体不会仅因出现在笔记中就触发发现。

## 使用

```bash
cd ~/Downloads/agent-factory
# 复用用户已经保持的隧道，不另建 SSH
OLLAMA_URL=http://127.0.0.1:11435 OLLAMA_CHAT_MODEL=qwen3.5:2b \
  .venv/bin/python scripts/serve_web.py --provider ollama --runner docker

.venv/bin/python -m agent_factory run \
  --provider ollama --model qwen3.5:2b --base-url http://127.0.0.1:11435 \
  --runner docker \
  --description '比较 TF-IDF 与 BM25Plus，用于技术记忆检索，返回前5条相关记忆并验证。'
```

Web 保持默认“自动判断”后提交；高级选项可限制为“仅使用已有能力”或“发现新算法”。“填入新方法示例”使用 BM25Plus，首次发现、登记后复用。旧服务需在原终端 Ctrl+C 后重启。CLI 可用 `--mode auto|reuse|discover`。

鉴权 API 提交 `{"description":"复刻 BM25Plus，用于技术记忆检索","mode":"auto"}`，省略 `mode` 也是自动。通过 `/v1/jobs/{id}` 轮询。原 `discover` CLI 与 `/v1/algorithms/discover` API 保留。

## 证据与边界

- `routing.json` 和报告保存模型判断、原文方法名、已登记/缺失方法、执行路径及原因；初始 `route` 阶段记录在事件中。
- 混合请求保留独立的发现与比较报告。后者标记 `compare_after_discovery`，关联 `discovery_run_id`；网页可点击“查看发现与登记报告”。比较保留本次阈值、修复预算和执行限制。
- 已批准的纯算术来源方法再次生成时，静态读取其已验证评分核，模型只生成调用包装，通用接口重新组合；仍重新执行所有验证和最终测试。普通内置方法继续完整代码生成。
- 一次最多发现一个新方法、比较八个候选。指定新方法必须与提议名称一致，不能静默替换成已有方法。
- 发现需要 Ollama、GitHub 网络与 Docker。离线模式遇到缺失方法明确提示；手动 `reuse` 也会拒绝缺失方法。手动 `discover` 不能重复登记已有方法。
- 源码证据优先保留目标评分函数和构造参数。标量 TF/IDF 路径无法推导来源数值检查时停止，不能只凭业务指标登记。
- 可静态适配的纯算术源码使用精简的 source_adapter 角色，只生成来源评分核的调用包装，温度 0；仍保留真实模型响应。真实代码响应先做不执行代码的语法与所需调用检查，错误消耗已有的一次响应重试。原始响应仍保留，不替换模板、不转 mock；后续执行验证照常进行。
- 只有验证与冻结最终测试均通过才登记；来源、许可和哈希可审查。数值样例不等于完整数学等价性证明。

## 验收方法

测试入口为 `bash scripts/run_tests.sh`，覆盖复用、变体区分、模型遗漏/幻觉、环境限制、任务拒绝、混合比较、方法一致性与策略保留。

实际 Chrome 验收脚本先提交“TF-IDF 与未登记的 BM25Plus”，再提交已登记的 BM25Plus。需在新 workspace 中启动 Ollama + Docker 服务；workspace 含 `data/`、`knowledge/sources/` 和基线 `config.json`。

```bash
NODE_PATH=/path/to/node_modules PLAYWRIGHT_CHANNEL=chrome \
FACTORY_API_TOKEN_FILE=/path/to/workspace/state/web/access-token.txt \
  node scripts/smoke_automatic.cjs http://127.0.0.1:8013 /path/to/workspace/browser-validation.json
```

脚本不启动 SSH、不伪造响应或指标，检查真实提交、路径、登记、来源公式、报告链接、下载哈希、复用、历史和移动端布局。截图位于忽略的 `state/web/automatic/`；失败保存记录并返回非零状态。

## 实际验收结果（2026-10-15，UTC+8）

实际 Chrome 提交两个默认 auto 任务，共产生三份报告，使用 qwen3.5:2b 和真实 Docker：

| 阶段 | 运行 ID（UTC） | 模型调用 | 最终 Recall@5 / MRR@5 / Precision@5 | 结果 |
| --- | --- | --- | --- | --- |
| 发现及登记 | 20261014T160901_22b20fbf | 5 | 0.9667 / 0.9250 / 0.2467 | BM25Plus r0 通过 |
| 发现后比较 | 20261014T161001_92e82969 | 9 | 1.0000 / 0.9528 / 0.2533 | TF-IDF r2 与 BM25Plus r0 通过，选定 TF-IDF |
| 再次复用 | 20261014T161126_0568ab6e | 4 | 0.9667 / 0.9250 / 0.2467 | 已登记 BM25Plus 首版通过，无 GitHub 搜索 |

证据：[发现](../examples/automatic-discovery-demo/report.md)、[比较](../examples/automatic-comparison-demo/report.md)、[复用](../examples/automatic-reuse-demo/report.md)、[浏览器检查](../examples/automatic_web_validation.json)。来源为 xhluca/bm25s，MIT 许可，固定提交 03eac5a8fb3aa4958ce37415a4cf2220d35157f7；来源公式检查和登记哈希均保留。当前共 140 项测试通过，另通过 Docker 离线闭环与安装包资源检查。

验收前的失败与一次撤销登记见 [审计记录](../examples/automatic-routing-audits.json)，不计入上述成功调用数。最终运行位于独立 workspace `state/automatic-acceptance-20261015`，默认目录没有登记该新方法；导师从默认目录首次提交 BM25Plus 时仍可演示自动发现。

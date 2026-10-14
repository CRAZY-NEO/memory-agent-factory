# Mac 运行工厂，Jetson 运行 Ollama

当前使用结构：Mac 上运行 CLI / Agent 工作流、SQLite 知识库、可信验证器和 Docker 算法执行容器；Jetson 只提供 Ollama 模型推理。代码、数据、知识库和报告保存在 Mac 的项目目录。无需把仓库传到 Jetson，也无需在 Jetson 安装项目 Python 依赖。

## 连接模型

在 Mac 的一个终端连接，替换为自己实际使用的 SSH 地址或别名：

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:11435:127.0.0.1:11434 kylon@JETSON_IP
```

连接存续期间可以重复提交多个工厂任务，不必每次重新 SSH。隧道关闭、休眠或网络中断导致连接失效后重新连接。上述保活参数帮助检测连接中断，不会自动重连。Ollama 可以继续只监听 Jetson 的本机地址。

在 Mac 的另一个终端检查：

```bash
curl -fsS --max-time 10 http://127.0.0.1:11435/api/tags
```

## 运行完整工厂流程

先打开 Docker Desktop；首次运行构建执行镜像：

```bash
cd ~/Downloads/agent-factory
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
bash scripts/run_ollama_demo.sh
```

脚本使用已安装的 `.venv`，默认模型 `qwen3.5:2b`，默认服务地址 `http://127.0.0.1:11435`。可用 `OLLAMA_CHAT_MODEL` 和 `OLLAMA_URL` 环境变量覆盖。

已对当前 `qwen3.5:2b` 服务验证 `think=false` 可以返回结构化答案。调用关闭额外思考输出，设置 8192 上下文与 4096 输出 token 上限；每次响应仍经过 schema 验证。说明参考 [Ollama thinking](https://docs.ollama.com/capabilities/thinking) 与 [structured outputs](https://docs.ollama.com/capabilities/structured-outputs)。初次尝试曾遇到 HTTP 200 但内容为空，后续调用会保留原始内容、结束原因与用量，便于区分输出截断和接口错误。

首次创建独立工作目录 `state/ollama-real`，复制示例数据和能力材料，从真实模型重新抽取知识。后续运行复用该知识库，并检索历史验证和失败经验。要重新演示空知识库抽取，可使用新的工作目录，例如：

```bash
FACTORY_DEMO_WORKSPACE=state/ollama-real-2 bash scripts/run_ollama_demo.sh
```

闭环包括：能力材料抽取 → 理解需求 → 检索知识 → 规划候选 → 生成代码 → Docker 验证 → 必要时审查与修复 → 比较候选 → 冻结后最终测试 → 写回知识库。候选和修复预算由工作目录 config.json 控制；新插件演示明确比较三个候选，每个候选默认最多修复两次；失败不会替换成 mock。

模型可阅读对应 TF-IDF/BM25 基线代码示例进行复刻，参考文件来源和哈希记录在每次生成上下文中。发布文件使用实际模型响应中的代码，未经人工替换；验证与测试指标由项目验证器计算。

运行结束时打印状态、运行 ID 和报告路径。输出目录为 `state/ollama-real/runs/<run_id>/`：

- `llm/`：实际模型调用、响应和耗时。
- `plan.json`：候选方案与设计理由。
- `<algorithm>/r*/`：各版代码和可信验证结果。
- `report.md`、`report.json`：完整报告。
- `knowledge/`：包含本次验证记录的图谱导出。
- 只有通过最终测试才生成 `selected_retrieval.py` 和 `plugin.json`。

## 调用通过验证的插件

```bash
.venv/bin/python -m agent_factory --workspace state/ollama-real \
  query 'MySQL 连接池耗尽怎么排查' --top-k 5
```

这里直接运行检索插件，不再调用 Ollama，因此不需要模型隧道；仍需本地 Docker 可用。`latest` 默认选择该工作目录中最近一次发布的插件；也可用 `--run <run_id>` 指定。

`docs/jetson-deployment.md` 是未来将后端也迁移到 Jetson 的另一种部署方式，当前不需要执行其中的远端安装步骤。

## 已验证的真实模型样例

`20261012T073909_2ed8189e` 已完成八次 `qwen3.5:2b` 调用，包含两次能力抽取、需求理解、规划、两个候选生成、一次审查和一次修复。TF-IDF 首版通过；BM25 首版遗漏 `re` 导入，自动修复后通过并被选定。最终独立测试 Recall@5=1.0、MRR@5=1.0、P95=4.65 ms；数据为小型模拟技术记忆库，不代表真实个人记忆数据的泛化表现。

报告、各版代码、模型响应和知识图谱归档到 [examples/ollama-demo](../examples/ollama-demo/report.md)，离线模板演示仍单独保存在 `examples/demo`。

## 插件机制演示

新增 Jaccard 与 Precision@k 的真实模型通过记录见 [Jaccard 报告](../examples/plugin-jaccard-ollama-demo/report.md)，保留六次真实模型调用、Docker 执行、冻结代码与知识回写。
三算法完整比较可使用以下命令；当前配置要求 TF-IDF、BM25、Jaccard 都出现在候选列表中：

```bash
FACTORY_PROVIDER=ollama FACTORY_DEMO_WORKSPACE=state/plugin-ollama-demo \
OLLAMA_URL=http://127.0.0.1:11435 OLLAMA_CHAT_MODEL=qwen3.5:2b \
bash scripts/run_plugin_demo.sh
```

保持 SSH 隧道连接直到模型流程结束。若发生模型代码失败，检查报告中所有修订与原始响应，不应将失败记录改写为成功；参见 [保留的失败例子](../examples/plugin-ollama-failure/report.md)。

三算法真实模型比较已完成并归档到 [完整报告](../examples/plugin-ollama-demo/report.md)：实际规划三种算法，共 13 次调用；BM25 通过独立最终测试，TF-IDF 与 Jaccard 的错误版本耗尽修复预算后排除。此运行复用先前真实模型抽取的能力知识，抽取原始响应保留在失败运行的 llm 目录中。

## 一个终端演示入口

现在也可由用户在 Mac 运行 `.venv/bin/python scripts/serve_web.py --provider ollama --ssh-target kylon@JETSON_IP`，由启动器管理自己创建的隧道、API、worker，并自动打开浏览器登录。替换为自己的实际 SSH 目标；已有连接会被复用。详情见 [Web 指南](web-ui.md)。

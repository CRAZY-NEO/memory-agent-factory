# Jetson 部署与 Memority 配置对应

## 请求链路

```text
App → 新项目 HTTPS 业务接口 → FastAPI 任务网关 → SQLite 队列
                                                       ↓
                              Jetson 可信 worker → 本机 Ollama qwen3.5:2b
                                       ↓
                       每次生成算法在禁网 Docker 容器中运行
```

API 在收到任务后立即返回 202 与 job_id，前端轮询任务状态，避免长时间代码生成占住 HTTPS 请求。
接口检查 Bearer token、输入长度与原始请求体上限；最多排队 20 项，同一时间只处理一项任务。
这是单用户原型的私有 token 认证，尚未接入 Memority 用户账户/JWT 或租户权限。

可信 worker 在 Jetson 宿主机运行，管理 Docker 算法容器；FastAPI 网关可在宿主机或 Docker 中运行。
网关容器只挂载任务队列及只读结果，不挂载 Docker socket，也不执行生成代码。
worker 的 Docker 权限仅应给可信服务账户；模型生成代码运行在另一个受限容器。

## 地址与模型

你的已有模型配置为 `qwen3.5:2b`，embedding 为 `qwen3-embedding:0.6b`。
本版生成代码使用 chat 模型，TF-IDF/BM25 检索不使用 embedding。

- **当前宿主 worker**：`OLLAMA_URL=http://127.0.0.1:11434`。
- **调用模型的代码在 Jetson 容器内时**：`OLLAMA_URL=http://host.docker.internal:11434`，并使用下述映射。
- `host.docker.internal` 是当前 Docker 宿主机的内部地址，不能拿来从 Mac 访问 Jetson。

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  OLLAMA_URL: http://host.docker.internal:11434
  OLLAMA_CHAT_MODEL: qwen3.5:2b
  OLLAMA_EMBED_MODEL: qwen3-embedding:0.6b
```

上面适用于未来把调用模型的后端放入容器；当前 compose 中 API 只收发任务，因此无须为 API 配置 Ollama 地址。
如果容器访问 Ollama，需确保 Ollama 监听可从桥接网络到达的地址；只监听 127.0.0.1 时 host-gateway 映射本身不能解决访问。

## 在 Jetson 准备

把仓库放到 `~/agent-factory`。确认使用 Python 3.11/3.12；旧 JetPack 系统可能需要另外安装兼容的解释器。
ARM64 上安装依赖后跑测试。已在本机 ARM64 Docker Linux 验证执行链，但尚未实测你的 Jetson/JetPack。

```bash
cd ~/agent-factory
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
scripts/run_tests.sh -q
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
cp .env.example .env
```

编辑 `.env`：设置私有随机 FACTORY_API_TOKEN，并按 `id -u`、`id -g` 设置用户编号。
可以在本机用 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 生成 token，勿提交或分享。
`.env` 已被 Git 忽略。模型名称保持你提供的 `qwen3.5:2b`。

## 启动：两种网关方式

容器网关：

```bash
mkdir -p state/jobs runs
docker compose up -d --build api
set -a
source .env
set +a
python -m agent_factory.job_worker
```

worker 在当前终端持续处理任务。仅首次调试需手工运行；长期运行可使用提供的用户 systemd unit。
路径采用 `~/agent-factory`；如果仓库位置不同，修改 unit 的 WorkingDirectory/EnvironmentFile/ExecStart。

```bash
mkdir -p ~/.config/systemd/user
cp deployment/agent-factory-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-factory-worker
```

用户服务是否在登出后继续运行取决于系统 linger 配置；按你现有 Memority 运维方式设置。
将 `deployment/nginx-location.conf` 接入该项目的实际 HTTPS 域名配置；此处没有假定你的域名、证书或现有 Nginx 路径。

也可以先在宿主机运行网关，少构建一个容器；同样需要另一个终端运行 worker：

```bash
set -a
source .env
set +a
uvicorn agent_factory.api:create_app --factory --host 127.0.0.1 --port 8010
```

## 业务接口

所有业务接口使用 `Authorization: Bearer <private-token>`。`/health` 只表示网关存活，不证明 worker/Ollama 可用。

| 方法和路径 | 工作 |
| --- | --- |
| POST /v1/factory/runs | 提交算法开发需求，body 为 description |
| POST /v1/knowledge/ingest | 抽取项目已配置算法材料 |
| POST /v1/memory/search | 调用已发布检索插件，body 为 query、top_k，可选 run_id 和 memories |
| GET /v1/jobs/{job_id} | queued / running / succeeded / failed；完成后包含 result |
| GET /v1/runs/{run_id}/report | 返回该次可信验证报告 |
| GET /docs | 自动生成接口文档，不包含任务数据 |

示例在 Jetson 本机执行，用环境变量里的 token；前端将地址换成项目实际 HTTPS 域名：

```bash
curl http://127.0.0.1:8010/v1/factory/runs \
  -H "Authorization: Bearer $FACTORY_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"description":"为中文技术记忆库生成检索算法，比较 TF-IDF 和 BM25，返回前 5 条"}'

curl http://127.0.0.1:8010/v1/jobs/YOUR_JOB_ID \
  -H "Authorization: Bearer $FACTORY_API_TOKEN"

curl http://127.0.0.1:8010/v1/memory/search \
  -H "Authorization: Bearer $FACTORY_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"query":"MySQL 连接池耗尽怎么排查","top_k":5}'
```

队列采用 rollback journal 与短事务、显式关闭数据库连接，避免 Docker bind mount 下 WAL 共享内存状态不一致；目录应在 Jetson 本机文件系统，避免网络文件系统。
worker 心跳租约过期的任务标记失败，不自动重跑；重新提交需明确创建新任务。
队列保存提交的数据和结果，应保持目录私有并制定清理策略。该原型未实现 API 取消任务与多用户数据隔离。
未来 PKG 应优先直接调用已验证检索插件，工厂接口留给开发/优化阶段。

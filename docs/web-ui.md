# 本地 Web 界面

网页与现有 FastAPI 网关同源，使用原任务队列与后台 worker。没有新增前端框架、CDN 或前端构建步骤；HTML/CSS/JavaScript 随 Python 包分发。浏览器提交需求，不直接连接 Ollama 或执行算法代码。

## 快速启动

```bash
cd ~/Downloads/agent-factory
# 首次安装 API 依赖；已有环境可跳过
.venv/bin/python -m pip install -r requirements.api.lock.txt
# Docker 保持运行，镜像没有准备过时构建
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
# 一个命令启动网页与后台执行器，默认 mock + Docker
.venv/bin/python scripts/serve_web.py
```

访问 [http://127.0.0.1:8010](http://127.0.0.1:8010)。启动器会在 state/web/access-token.txt 创建仅用户可读的随机访问密钥，不在日志打印密钥；页面输入该文件内容连接。

Mac 可在另一个终端复制：

```bash
cd ~/Downloads/agent-factory
cat state/web/access-token.txt | pbcopy
```

然后在页面粘贴，点击“连接”→“填入三算法比较示例”→“提交生成任务”。页面轮询实际阶段、候选修订与结果；成功后可下载选定代码、插件清单及 Markdown/JSON 报告，也能下载失败候选代码用于复核。历史失败运行同样展示，最终测试失败不会提供可发布插件下载。

密钥只存在页面内存，不写浏览器 localStorage/sessionStorage，刷新后重新连接，历史任务与报告仍由服务器保存。若启动前已设置 FACTORY_API_TOKEN，启动器沿用它而不写入文件。轮询中断不会取消服务器任务，重新连接后点击最近任务即可查看。

## 接入 Jetson 真模型

先保持原来的 SSH 隧道，停止 mock 启动器，再启动真实模式：

```bash
OLLAMA_URL=http://127.0.0.1:11435 OLLAMA_CHAT_MODEL=qwen3.5:2b \
.venv/bin/python scripts/serve_web.py --provider ollama --runner docker
```

模型提供方由后台配置决定，提交者不能绕过配置。报告显示实际 provider/model/runner，mock 与真实模型明确区分。worker 运行在 Mac；Jetson 只运行 Ollama。真实生成代码必须用 Docker。

Ctrl+C 同时停止网关与 worker。建议让任务结束后停止；强制中断的任务按现有 lease 规则标记失败，需要明确重提，不宣称中断恢复。端口占用可用 --port 8012；--workspace 可指向有 config.json、data、knowledge 的独立目录。

## Compose / 独立启动

Compose API 容器也提供 `/` 网页，新版本额外挂载 data 与 config.json 供鉴权后展示数据规模。API 不挂 Docker socket。更新后运行 docker compose up -d --build，继续按原部署文档启动宿主 worker；不要再用 serve_web.py 启动第二个网关。

非本机使用现有 HTTPS 部署方式。根页面与静态资源公开；任务、数据概要、报告、历史和下载都需要 Bearer 鉴权。模型文本用 textContent 显示，报告不作为 HTML 渲染。下载用带鉴权的 fetch，不把密钥放进 URL。

## 新增接口

- GET / 与 GET /assets/*：中文网页与静态资源。
- GET /v1/settings：算法配置和数据版本，不含查询全文、标注或服务密钥。
- GET /v1/jobs、GET /v1/runs：最近任务与运行摘要，数量有上限。
- GET /v1/jobs/{job_id}：增加 run_id 与实际 workflow.jsonl 阶段事件。
- GET /v1/runs/{run_id}/artifacts/{filename}：固定允许的报告、清单、选定代码。
- GET /v1/runs/{run_id}/code/{algorithm}/{revision}：候选代码下载。

不能通过下载接口读取任意文件、提示日志或目录外的符号链接；失败候选仅下载源文件，不执行。旧队列自动增加 run_id 字段并保留任务，迁移支持网关与 worker 同时启动。

## 浏览器回归

正常测试仍用 scripts/run_tests.sh。另提供独立 Playwright smoke；它对已运行网关做真实 HTTP 和浏览器操作，不替换响应制造成功，也不选择后台模型模式。只有 HTML 注入安全测试在浏览器侧伪造文字，随后恢复真实报告。

```bash
# Playwright 是可选开发依赖，装在独立目录；已有环境可跳过
npm install --prefix /tmp/agent-factory-browser playwright
# 本机已有 Chrome，可直接使用 chrome channel
NODE_PATH=/tmp/agent-factory-browser/node_modules PLAYWRIGHT_CHANNEL=chrome \
FACTORY_API_TOKEN_FILE=state/web/access-token.txt \
node scripts/smoke_web.cjs http://127.0.0.1:8010
```

检查鉴权、提交与轮询、阶段、候选与分组报告、下载哈希、HTML 注入防护、移动端宽度、刷新后的历史及浏览器错误。输出 examples/web_validation.json 与 state/web/screenshots/；截图和下载文件位于 Git 忽略的 state。若没有 Chrome，需按自己的 Playwright 环境准备浏览器，不影响正常网页运行。

## 本次运行证据

真实 Chrome 浏览器向本机网关提交需求，mock 响应下运行禁网 Docker 算法，完成三候选比较、冻结测试与回写。运行 20261013T195942_1359d9f1 选定 TF-IDF r0，最终 Recall@5=1、MRR@5=0.9528、Precision@5=0.2533。记录见 [完整报告](../examples/web-evaluation-demo/report.md)、[浏览器检查](../examples/web_validation.json)、[Docker 网关检查](../examples/web_container_validation.json)。本次未新增 Jetson 实机验证或真实模型成绩。

[桌面截图](images/web-desktop.png) · [手机宽度截图](images/web-mobile.png)。59 项自动测试及浏览器回归通过，安装包已检查包含 HTML、CSS 与 JavaScript。

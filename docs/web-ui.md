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

启动器会检查端口，等待 API 就绪，随后自动打开默认浏览器并登录。直接点击“填入三算法比较示例”→“提交生成任务”；页面展示执行阶段、候选修订与结果，成功后可下载代码和报告。退出启动器用 Ctrl+C。

本地自动登录不需要手动复制密钥。启动器只把一个 5 分钟内有效、只能兑换一次的随机开页凭据放入浏览器 URL fragment；前端立即移除 fragment，再换取 HttpOnly、SameSite=Strict 的浏览器会话 Cookie。真正的 API Bearer 密钥不会放进 URL、页面输入框或 localStorage/sessionStorage。会话只在 API 进程内有效，最长 12 小时，刷新页面继续连接；点击“断开”撤销会话，API 重启后旧会话失效。Cookie 鉴权的写请求检查同源 Origin。

## 真模型：一个终端启动全部服务

首次使用时，将下面的目标替换成自己的 SSH 别名或用户名和 Jetson 地址：

```bash
.venv/bin/python scripts/serve_web.py \
  --provider ollama --runner docker \
  --ssh-target kylon@JETSON_IP
```

命令由用户在 Mac 执行：先检查 Ollama；如尚未连接，启动带保活与转发失败检查的 SSH 隧道，将本机 11435 转发到 Jetson 的 127.0.0.1:11434。SSH 密码或首次主机确认（如有）在同一个终端完成。然后启动网页和 worker，打开浏览器自动登录。默认模型 qwen3.5:2b、OLLAMA_URL=http://127.0.0.1:11435；也可通过环境变量配置。

如果旧网页启动器仍在运行，先在其终端按 Ctrl+C 停止。想由新入口统一管理隧道时，也先停止自己以前手动运行的 SSH 隧道。已有 Ollama 连接会被复用，不额外创建或关闭它。

启动器只管理它创建的子进程。Ctrl+C 同时停止自己的网页、worker 和 SSH 隧道；已有外部隧道保持由用户管理。子进程意外退出会停止本次其他服务；当前不实现自动重连、任务中断恢复。建议任务结束后退出。

若已保持自己的隧道，只需：

```bash
.venv/bin/python scripts/serve_web.py --provider ollama
```

真实模式启动前校验模型列表与模型名称。连接失败、模型缺失或端口占用会直接提示，避免进入网页后才提交失败任务。端口占用请停止原启动器；更换端口仍应避免同一 workspace 混用多个 worker。

## 手动或远程访问

API 仍要求鉴权。向导师分享 HTTPS 页面时使用访问密钥；本地自动会话只用于本启动器打开的浏览器，不会给其他浏览器自动授权。

```bash
# 不自动打开浏览器，保留手动连接方式
.venv/bin/python scripts/serve_web.py --no-open-browser
# Mac 上复制备用密钥
cat state/web/access-token.txt | pbcopy
```

备用随机密钥保存在 state/web/access-token.txt，权限仅当前用户可读，不在终端打印；若已设置 FACTORY_API_TOKEN，则沿用它，不生成该文件。手动粘贴的密钥只保存在页面内存，刷新后需重新连接。API 与历史任务不会因关闭网页而消失。

轮询中断不会取消服务器任务；重新登录后点击最近任务查看。强制中断按现有 lease 规则标记失败，需重新提交，不宣称中断恢复。--workspace 可指定有 config.json、data、knowledge 的独立目录。

## Compose / 独立启动

Compose API 容器也提供 `/` 网页，新版本额外挂载 data 与 config.json 供鉴权后展示数据规模。API 不挂 Docker socket。更新后运行 docker compose up -d --build，继续按原部署文档启动宿主 worker；不要再用 serve_web.py 启动第二个网关。

非本机使用现有 HTTPS 部署方式。根页面与静态资源公开；任务、数据概要、报告、历史和下载都需要 Bearer 鉴权。模型文本用 textContent 显示，报告不作为 HTML 渲染。下载用带鉴权的 fetch，不把密钥放进 URL。

## 新增接口

- GET / 与 GET /assets/*：中文网页与静态资源。
- POST /v1/session/bootstrap、GET/DELETE /v1/session：仅本地启动器开启的浏览器会话，开页凭据一次性，刷新恢复、断开撤销。
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

## 自动登录回归

新增 tests/test_demo_launcher.py 检查凭据重放与过期、同源写入、鉴权保留、端口冲突、SSH 子进程创建/复用及清理。scripts/smoke_local_session.cjs 使用实际 Chrome 检查自动登录、刷新保持连接、退出与 Cookie 鉴权的 Docker 完整流程。测试将启动器的浏览器回调捕获到权限为 0600 的临时文件以供无头 Chrome 打开；不会实际建立 Jetson SSH 连接。

本次真实 Chrome 自动会话回归见 [验证记录](../examples/session_validation.json)。该记录为 mock + Docker；隧道创建/复用/清理由进程替身测试覆盖，没有另行连接 Jetson SSH。

## 从 GitHub 发现新算法

以 Ollama + Docker 启动并连接后，保持默认“自动判断”，填写能力描述即可。指定未登记方法时自动发现，已有方法优先复用。可点击“填入新方法示例”，无需勾选。报告展示执行路径、来源、提交、许可和登记状态。

只有验证及冻结最终测试均通过才登记，之后请求同一方法会复用。混合需求登记后自动比较，并提供发现报告链接。高级选项保留手动限制；离线模式遇到缺失方法会提示。旧服务需退出启动器后重启。见 [自动路由](automatic-routing.md)及[发现指南](github-discovery.md)。

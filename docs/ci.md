# GitHub Actions 自动检查

工作流定义在 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。向 `main` 推送、提交 Pull Request，或在 GitHub Actions 页面手动运行 `Offline checks` 时触发。无需配置 Ollama、Jetson、SSH 隧道或 API Key。首次运行仍需联网下载 Python、锁定依赖、Actions 和 Docker 基础镜像；“离线”指业务工作流使用 mock LLM，不依赖模型服务。

检查使用 Ubuntu 24.04、Python 3.12 和 `requirements.lock.txt`。一个作业依次执行：

1. 创建 `.venv`，安装锁定依赖，运行 `pip check`。
2. 按现有 `pyproject.toml` 规则检查 Python；检查 Web JavaScript 语法。
3. 用 `bash scripts/run_tests.sh` 运行完整测试，保存 JUnit XML。
4. 构建 wheel，逐字节核对 Web 静态资源、算法模板和插件材料；安装 wheel 后以 `python -I` 验证独立导入及资源读取。
5. 构建 `Dockerfile.runner`，在全新目录复制当前数据与配置。先从当前仓库的 Jaccard 模板执行 `ingest-repo` AST 静态抽取，再执行三算法 mock 流程并注入首版故障。生成代码实际在禁网 Docker 容器中执行。
6. 核对失败检测、代码确实修改、所有候选最后一版通过验证、冻结代码哈希、最终测试、配置指标和数据哈希，以及图谱中的验证结果和修复经验。检查 AST 产物保留未验证标记、Jaccard 生成上下文引用仓库候选，且存在 `context_from` 溯源边；另检查模型角色上下文没有包含最终测试查询。

任何一步失败都会使检查失败，不会自动改阈值、切换到本地执行或伪造通过结果。`scripts/check_ci.py smoke` 拒绝复用已有目录，避免错误读取历史报告或旧评估数据。`report` 子命令可独立核对一个烟测目录。当前流程没有模拟 Ollama 的生成质量、Jetson 部署或 ARM64 运行结果。

无论成功或失败，Actions 都尝试上传 `state/ci/`，保留 7 天。其中包含测试结果、wheel、Docker 镜像信息、烟测日志、原始运行报告、代码、角色调用轨迹和知识图谱。查看 Actions 运行页面的 **Artifacts**，下载 `offline-checks-运行ID-尝试次数`。如果安装依赖阶段失败而目录尚未产生，诊断在该步骤日志中。报告中的 provider 明确为 `mock`。

仓库权限只有 `contents: read`，checkout 不保留凭证；工作流不发布软件包、不部署、不使用仓库 secrets。同一分支新运行会取消旧运行，作业最长 20 分钟，Docker 烟测步骤最长 6 分钟。普通 `pull_request` 事件避免在带有写权限的 `pull_request_target` 环境执行外部提交。

第三方 Actions 固定为官方发布的完整 commit SHA，版本见 YAML 注释；更新时同时核对官方 tag 与 SHA：[checkout](https://github.com/actions/checkout/releases)、[setup-python](https://github.com/actions/setup-python/releases)、[upload-artifact](https://github.com/actions/upload-artifact/releases)。Python 依赖锁定不代表 Docker 基础镜像与 wheel 构建工具已做到字节级可重现。

## 在本地运行相同检查

以下命令在项目根目录执行，要求 Python 3.12、Docker 和 Node.js 已安装。已配置 `.venv` 的开发环境可跳过前两行。使用新检出目录验证安装最接近 CI。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pip check
.venv/bin/ruff check agent_factory tests scripts
node --check agent_factory/web/app.js
mkdir -p state/ci-local
bash scripts/run_tests.sh -q --junitxml=state/ci-local/tests.xml
.venv/bin/python -m pip wheel --no-deps --wheel-dir state/ci-local/wheels .
.venv/bin/python scripts/check_ci.py wheel state/ci-local/wheels
.venv/bin/python -m pip install --no-deps --force-reinstall state/ci-local/wheels/*.whl
.venv/bin/python -I scripts/check_ci.py installed
docker build -f Dockerfile.runner -t agent-factory-runner:0.1 .
.venv/bin/python scripts/check_ci.py smoke state/ci-local/factory
```

重复执行时为 wheel 和 factory 选择一个新的输出目录。上述 wheel 安装会将当前虚拟环境中的可编辑安装替换为安装包；日常开发若依赖 editable 模式，可在检查后运行 `.venv/bin/python -m pip install --no-deps -e .` 恢复。运行 `python -I` 时刻意排除当前源码目录，能发现源码运行正常、打包却漏带资源的问题。

自动检查通过只证明所运行的离线检查通过。真实模型新评估集报告保留为独立证据；在 GitHub Actions 远端实际运行并显示绿色前，不将本地验证表述为远端 CI 已通过。

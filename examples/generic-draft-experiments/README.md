# 两个未登记任务的真实生成与修复证据

主报告：[实验设计、结果、失败与边界](../../docs/generic-generation-experiments.md)。

- [protocol.json](protocol.json)：真实需求、事前固定的 32 个人工用例。
- [summary.json](summary.json)：最终结果、实际 HTTP 调用和此前四轮工程尝试。
- [word_frequency/natural](word_frequency/natural/) 与 [text_deduplication/natural](text_deduplication/natural/)：自然流程的真实候选、规格、审查、模型调用及验收。
- 两项任务下的 `repair/`：受控故障副本、注入说明、失败差异和真实修复。首版错误代码是实验注入，不是模型自然失败。
- [earlier-failures](earlier-failures/)：此前失败报告与相关源码/规格。
- [known-bad-spec-recheck.json](known-bad-spec-recheck.json)：重新审查历史错误规格的实际结果。
- [evidence-manifest.json](evidence-manifest.json)：便携证据文件 SHA-256。

每项 16 个人工用例均通过，返修后复用同组用例再次通过。未新增专用生产模板或任务适配器，未将候选认证或启用。公开调用文件省略模型推理正文，保留实际请求、最终原文和响应；所有数据是合成示例。

复现：在仓库根目录运行 `.venv/bin/python scripts/run_generic_draft_experiments.py`。该命令真实调用 DeepSeek，需本机密钥和 Docker；结果保存到全新 `state/generic-drafts-时间戳/`。本目录供审查，不是完整注册工作区，不能直接复制成启用组件。

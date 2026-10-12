"""Portable reports for both the examiner and the future plugin consumer."""

import json
from pathlib import Path


def write_report(directory, summary):
    directory = Path(directory)
    (directory / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# 算法能力工厂验证报告", "", f"运行：`{summary['run_id']}`",
             f"提供方：**{summary['provider']}**；模型：`{summary['model']}`；执行器：`{summary['runner']}`",
             f"状态：**{summary['status']}**", "", "## 需求", summary["description"], "",
             "## 方案依据", summary["plan"]["design_explanation"], "",
             "## 候选版本验证", "",
             "| 算法 | 版本 | 通过 | Recall@k | MRR@k | P95 ms | 错误 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for attempt in summary["attempts"]:
        metrics = attempt["validation"]["metrics"]
        values = [f"{metrics[key]:.4f}" if key in metrics else "—" for key in
                  ["recall_at_k", "mrr_at_k", "p95_ms"]]
        error = "; ".join(attempt["validation"]["errors"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {attempt['algorithm']} | {attempt['revision']} | {attempt['validation']['passed']} | "
                     + " | ".join(values) + f" | {error} |")
    lines += ["", "## 冻结后的最终测试", ""]
    if summary["selected"]:
        lines += [f"选定：`{summary['selected']['algorithm']}` 版本 {summary['selected']['revision']}",
                  f"SHA-256：`{summary['selected']['code_hash']}`", "",
                  "```json", json.dumps(summary["test"]["metrics"], ensure_ascii=False, indent=2), "```",
                  f"最终测试通过：{summary['test']['passed']}"]
    else:
        lines.append("所有候选均未通过验证，未选定或发布插件。")
    lines += ["", "## 可复现性与边界", "",
              "- 模拟数据、切分与文件哈希已记录。测试结果不进入生成、修复或候选选择。",
              "- 所有报告指标由可信验证器计算，mock 模式只替代 LLM 响应。",
              "- P95 是本次小语料的函数耗时，包含索引构建，不含解释器/依赖导入。单次小样本不可作为生产性能承诺。",
              "- local 是限时子进程及 AST 检查，并非可靠安全沙箱；真实 LLM 默认要求 Docker。",
              "- 这是一套检索算法工厂及可复用插件，个人记忆事实图谱仍需另行构建。", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")

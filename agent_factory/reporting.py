"""Portable reports render whichever trusted metric plugins the run enabled."""

import json
from pathlib import Path


def write_report(directory, summary):
    directory = Path(directory)
    (directory / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    keys = list(summary["validation_config"].get("metrics", {}))
    if not keys:
        keys = list(dict.fromkeys(key for row in summary["attempts"] for key in row["validation"]["metrics"]))
    labels = {item["id"]: item["name"] for item in summary.get("plugin_catalog", {}).get("metrics", [])}
    columns = ["算法", "版本", "通过", *(labels.get(key, key) for key in keys), "错误"]
    lines = ["# 算法能力工厂验证报告", "", f"运行：`{summary['run_id']}`",
             f"提供方：**{summary['provider']}**；模型：`{summary['model']}`；执行器：`{summary['runner']}`",
             f"状态：**{summary['status']}**", "", "## 需求", summary["description"], "",
             "## 方案依据", summary["plan"]["design_explanation"], "", "## 候选版本验证", "",
             "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for attempt in summary["attempts"]:
        metrics = attempt["validation"]["metrics"]
        values = [f"{metrics[key]:.4f}" if key in metrics else "—" for key in keys]
        error = "; ".join(attempt["validation"]["errors"]).replace("|", "/").replace("\n", " ")
        lines.append("| " + " | ".join([attempt["algorithm"], str(attempt["revision"]),
                                          str(attempt["validation"]["passed"]), *values, error]) + " |")
    if any(attempt.get('generation') for attempt in summary['attempts']):
        lines += ['', '## 生成与修复轨迹', '',
                  '| 算法 | 版本 | 策略 | 结构重复 | 历史来源 |', '| --- | --- | --- | --- | --- |']
        for attempt in summary['attempts']:
            generation = attempt.get('generation', {})
            lines.append('| ' + ' | '.join([attempt['algorithm'], str(attempt['revision']),
                         generation.get('strategy', 'legacy'), str(generation.get('unchanged', False)),
                         generation.get('source_run', '')]) + ' |')
    lines += ["", "## 冻结后的最终测试", ""]
    if summary["selected"]:
        lines += [f"选定：`{summary['selected']['algorithm']}` 版本 {summary['selected']['revision']}",
                  f"SHA-256：`{summary['selected']['code_hash']}`", "", "```json",
                  json.dumps(summary["test"]["metrics"], ensure_ascii=False, indent=2), "```",
                  f"最终测试通过：{summary['test']['passed']}"]
    else:
        lines.append("所有候选均未通过验证，未选定或发布插件。")
    if summary.get("plugin_catalog"):
        lines += ["", "## 插件与比较规则", "",
                  "算法插件：" + ", ".join(f"{item['id']}@{item['version']}" for item in summary["plugin_catalog"]["algorithms"]),
                  "指标插件：" + ", ".join(f"{item['id']}@{item['version']}" for item in summary["plugin_catalog"]["metrics"]),
                  "指标比较顺序：" + ", ".join(summary["validation_config"]["selection_order"]),
                  "同分时按依赖数量、配置算法顺序、修订号选择。"]
    lines += ["", "## 可复现性与边界", "",
              "- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。",
              "- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。",
              "- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。",
              "- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。",
              "- 插件由开发者编写，真实模型代码默认在禁网 Docker 容器运行；local 不构成可靠安全沙箱。", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")

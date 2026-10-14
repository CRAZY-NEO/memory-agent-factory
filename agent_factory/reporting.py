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
    evaluations = [(f"{row['algorithm']} r{row['revision']} / validation", row['validation'])
                   for row in summary['attempts'] if row['validation'].get('by_category')]
    if summary.get('test') and summary['test'].get('by_category'):
        evaluations.append(('选定版本 / test', summary['test']))
    for title, evaluation in evaluations:
        lines += ['', f'## 查询类型分析：{title}', '',
                  '| 类型 | 查询数 | Recall@k | MRR@k | Precision@k | 有失败的查询 | 负例优先的查询 |',
                  '| --- | --- | --- | --- | --- | --- | --- |']
        for category, group in evaluation['by_category'].items():
            values = [f"{group['metrics'][key]:.4f}" if group['metrics'].get(key) is not None else '—'
                      for key in ['recall_at_k', 'mrr_at_k', 'precision_at_k']]
            lines.append('| ' + ' | '.join([category, str(group['query_count']), *values,
                         str(group['failure_count']), str(group['hard_negative_confusion_count'])]) + ' |')
        failures = [row for row in evaluation['per_query'] if row.get('failure_types')]
        if failures:
            lines += ['', '失败案例（排名错误、漏召回、相近负例混淆）：', '']
            for row in failures:
                query = row['query'].replace('\n', ' ').replace('|', '/')
                lines.append(f"- {row['query_id']}：{query}；类型 {', '.join(row['failure_types'])}；"
                             f"漏召回 {row['missed_relevant_ids']}；负例优先 {row['hard_negative_confusions']}")
    lines += ["", "## 可复现性与边界", "",
              "- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。",
              "- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。",
              "- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。",
              "- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。",
              "- 算法目录来自可信开发者模块或验证后登记清单；真实模型代码默认在禁网 Docker 容器运行，local 不构成可靠安全沙箱。", ""]
    if summary.get('discovery'):
        discovery = summary['discovery']
        source = discovery['source']
        lines += ['## GitHub 算法发现与登记', '',
                  f"来源：{source['url']}；固定提交：`{source['commit']}`；许可：{source['license']}",
                  '搜索词：' + ', '.join(discovery['search_queries']),
                  f"登记结果：{bool(discovery['release'])}；使用内置算法模板：False", '',
                  '下载仓库未导入或执行，模型根据来源片段复刻代码。验证证明当前数据与接口上的表现，不构成源码数学等价性证明。', '']
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")

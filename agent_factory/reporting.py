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
    policy = summary['validation_config']
    seed_label = ('不支持固定采样种子（配置仅为试验编号）' if summary['provider'] == 'deepseek'
                  else str(policy.get('model_seed', 'legacy')))
    lines[5:5] = [
        f"生成模式：`{policy.get('generation_mode', 'legacy_reference')}`；历史经验：`{policy.get('history_mode', 'legacy')}`；模型种子：`{seed_label}`",
        '扩展要求：' + (', '.join(policy.get('requirements', [])) or '无'),
        '范围说明：mock 是模板基线；确定性源码适配是代码复用；测试通过本身不证明模型独立开发能力。', '']
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
                   for row in summary['attempts'] if row['validation'].get('by_category') and 'per_query' in row['validation']]
    if summary.get('test') and summary['test'].get('by_category') and 'per_query' in summary['test']:
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
    if summary['request']['task'] == 'memory_classification':
        for title, evaluation in [('验证集', (summary.get('selected') or {}).get('validation')),
                                  ('冻结后的最终测试', summary.get('test'))]:
            if not evaluation or not evaluation.get('per_example'):
                continue
            lines += ['', f'## 记忆分类：{title}', '',
                      f"训练样本：{evaluation['training_count']}；评估样本：{evaluation['example_count']}",
                      '混淆矩阵行是真实类别，列是预测类别。', '', '```json',
                      json.dumps(evaluation['confusion_matrix'], ensure_ascii=False, indent=2), '```', '',
                      '| 样本 | 真实类别 | 预测类别 | 正确 |', '| --- | --- | --- | --- |']
            for row in evaluation['per_example']:
                lines.append('| ' + ' | '.join(str(row[key]).replace('|', '/').replace('\n', ' ')
                                               for key in ['id', 'label', 'prediction', 'correct']) + ' |')
    lines += ["", "## 可复现性与边界", "",
              "- 数据文件哈希、算法和指标插件版本、验证阈值均已记录。最终测试不参与修复和候选选择。",
              "- 所有报告指标由可信验证器计算，mock 只替代 LLM 响应。",
              *( ["- Accuracy 与 Macro-F1 来自可信分类验证器；Macro-F1 对评估中真实或预测出现的类别等权平均。",
                  "- 分类 P95 包含整批重新训练与预测，以三次整批调用估计，不是单条纯推理延迟。",
                  "- 分类部署包包含冻结训练样本；对外分享前检查数据是否适合公开。"]
                 if summary['request']['task'] == 'memory_classification' else
                 ["- Precision@k 使用固定分母 k，不足 k 条的返回位置计为不相关；多查询取平均。",
                  "- P95 包含查询内索引构建，不包含解释器和依赖导入，小样本结果不代表生产性能。"] ),
              "- 算法目录来自可信开发者模块或验证后登记清单；真实模型代码默认在禁网 Docker 容器运行，local 不构成可靠安全沙箱。", ""]
    if summary.get('discovery'):
        discovery = summary['discovery']
        source = discovery['source']
        lines += ['## GitHub 算法发现与登记', '',
                  f"来源：{source['url']}；固定提交：`{source['commit']}`；许可：{source['license']}",
                  '搜索词：' + ', '.join(discovery['search_queries']),
                  f"登记结果：{bool(discovery['release'])}；使用内置算法模板：False", '',
                  '下载仓库未导入或执行，模型根据来源片段复刻代码。验证证明当前数据与接口上的表现，不构成源码数学等价性证明。', '']
    if summary.get('routing'):
        routing = summary['routing']
        lines += ['## 自动工作流选择', '', f"提交模式：{routing['requested_mode']}；执行路径：{routing['action']}",
                  routing['reason'], '模型说明：' + routing['intent']['rationale'],
                  '未登记的方法：' + ', '.join(routing['missing_methods']), '']
        if routing.get('discovery_run_id'):
            lines += ['发现与登记运行：`' + routing['discovery_run_id'] + '`；本报告为随后候选比较。', '']
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")

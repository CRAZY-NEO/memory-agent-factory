"use strict";
// New results and historical reports share the same distinction between stages.
function factoryDraftOutcome(value) {
  if (value.status === "draft") return {label:"候选已生成，待业务验证", tone:"warning", note:"基础检查和提议示例已通过，独立业务验证尚未完成；未启用候选。"};
  if (value.failure_stage === "specification") return {label:"规格需要澄清", tone:"warning", note:"提议规格未通过一致性审查，尚未生成代码；请查看矛盾记录。"};
  if (value.error) return {label:"生成或修复中断", tone:"danger", note:"模型或执行流程中断，已保留已有代码与错误记录。"};
  const checks = value.checks || {};
  const technical = checks.technical_passed ?? value.technical_checks_passed;
  const examples = checks.examples_passed ?? value.examples_passed ?? checks.passed;
  if (technical === true && examples === false) return {label:"示例结果不符", tone:"danger", note:"安全、接口和运行检查通过，但输出与提议示例不一致；查看具体差异与修复记录。"};
  return {label:"候选检查未通过", tone:"danger", note:"已保留生成与检查记录，未启用候选。"};
}
// Queue completion and capability acceptance are separate outcomes.
function factoryJobOutcome(job) {
  const state = job.status;
  if (state === "queued") return {state, label:"排队中", tone:"neutral", note:"已提交，等待执行。"};
  if (state === "running") return {state, label:"执行中", tone:"neutral", note:"正在处理需求。"};
  if (state === "failed") return {state, label:"执行失败", tone:"danger", note:"执行中断，请查看错误记录。"};
  const value = job.result || {}, action = value.status || value.action || value.decision?.action;
  if (job.kind === "component_plan") return {state:"planned", label:"计划已就绪", tone:"success", note:"仅完成规划，尚未生成或切换组件。"};
  if (action === "draft" || action === "draft_failed") return {state:action, ...factoryDraftOutcome(value)};
  const outcomes = {
    blocked:["需求被拦截", "warning", "需求未通过当前门禁，本次没有生成代码。"],
    rejected:["验证未通过", "danger", "新版本未通过验证，现用组件保持原样。"],
    migration_required:["等待确认切换", "warning", "新版已验证，接口变化需确认后切换。"],
    handle_data:["需要处理资料", "warning", "这是业务资料问题，没有触发代码修改。"],
    draft_trial:["候选试用完成", "warning", "本次试用已执行；不代表独立业务验证通过。"],
    active:["组件已就绪", "success", "已生成或复用通过验证的组件。"],
    approved:["已验证", "success", "组件通过当前可信验证规则。"],
    error:["执行失败", "danger", "操作未完成，请查看错误信息。"]
  };
  if (outcomes[action]) { const [label, tone, note] = outcomes[action]; return {state:action, label, tone, note}; }
  const labels = {component_invoke:"试用已完成", component_activate:"版本已切换", component_rollback:"版本已恢复", component_feedback:"反馈已记录"};
  return {state, label:labels[job.kind] || "已完成", tone:"success", note:"操作已完成。"};
}
if (typeof module !== "undefined" && module.exports) module.exports = {factoryJobOutcome, factoryDraftOutcome};

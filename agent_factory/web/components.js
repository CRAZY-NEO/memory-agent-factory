"use strict";
// Use the shared authenticated helpers and render all model/user strings as text.
const componentLabels = {draft:"候选研究与生成", draft_failed:"候选检查未通过", draft_trial:"候选试用完成",create:"创建新组件", update:"更新已有组件", rewrite:"重新实现", reuse:"复用已有版本", parameterize:"调整默认参数", blocked:"暂不能验证这项需求", handle_data:"需要处理业务资料", active:"组件已就绪", approved:"已验证", rejected:"验证未通过", migration_required:"新版已验证，等待切换"};
let componentDraftContext = null;
let componentBusy = false;
let componentCatalog = {bindings:[], versions:[], rules:[]};
let componentCurrentSlot = "";
let componentPendingMigration = null;
let componentTrialKey = "";
let componentLastResult = null;
function componentJSON(id) {
  try {
    const value = JSON.parse($(id).value);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value;
  } catch { throw new Error(id === "component-parameters" ? "高级设置中的默认参数需要是 JSON 对象。" : "开发者输入需要是 JSON 对象。"); }
}
function componentBinding() { return componentCatalog.bindings.find(row => row.slot === componentCurrentSlot); }
function componentVersion(binding = componentBinding()) { return componentCatalog.versions.find(row => row.version === binding?.version); }
function componentSlot() { if (!componentCurrentSlot) throw new Error("请先生成并启用一个组件。"); return encodeURIComponent(componentCurrentSlot); }
function componentReveal(id) { $(id).hidden = false; $(id).scrollIntoView({behavior:"smooth", block:"nearest"}); }
function componentControls() {
  const unavailable = !connected || componentBusy;
  for (const button of document.querySelectorAll('[id^="component-"], #component-artifacts button')) if (button.tagName === "BUTTON") button.disabled = unavailable;
  for (const input of $("component-form").querySelectorAll("input, select, textarea")) input.disabled = unavailable;
  $("component-ensure").textContent = componentBusy ? "正在处理…" : "开始执行 ↗";
  $("component-invoke").disabled = unavailable || (!componentBinding() && !componentDraftContext);
  $("component-open").disabled = unavailable || !componentBinding();
  $("component-rollback").disabled = unavailable || !(componentBinding()?.revision > 1);
  $("component-activate").disabled = unavailable || !$("component-version").value;
  $("component-confirm-migration").disabled = unavailable || !componentPendingMigration;
}
function componentSummary(value) {
  if (value.status === "draft") return "候选代码已生成，基础接口与执行检查通过。独立业务验证尚未完成，未加入已验证组件目录，也未替换现用组件。";
  if (value.status === "draft_failed") return value.message + (value.error ? " " + value.error : "");
  if ((value.status || value.action) === "blocked") return "本次未生成代码。" + (value.decision?.reason || value.reason || "需求被当前门禁拦截。") + " 提取数字等尚无验证配置的新需求现在会继续生成候选；请重新提交，历史记录保持原样。";
  const lines = [];
  if (value.decision?.action || value.action) lines.push("处理方式：" + (componentLabels[value.decision?.action || value.action] || value.action));
  if (value.version) lines.push("组件版本：" + value.version);
  const reason = value.decision?.reason || value.reason;
  if (reason) lines.push(reason);
  if (value.status === "rejected") lines.push("新版本未通过验证，请查看报告中的具体问题。已有组件仍可继续使用。");
  if ((value.status || value.action) === "blocked") lines.push("请查看详情中的待支持规则，补充可信验证后再生成。");
  if ((value.status || value.action) === "handle_data") lines.push("这是资料问题，应交回提取或审查流程处理。");
  return lines.join("。 ");
}
function componentShowTrialResult(value) {
  const output = value.output;
  const lines = [];
  if (Array.isArray(output?.records)) for (const row of output.records) lines.push(row.record_id + "：" + (row.complete ? "必填字段齐全" : "缺少 " + row.missing_fields.join("、")) + (row.invalid_fields?.length ? "；格式错误：" + row.invalid_fields.join("、") : ""));
  else if (Array.isArray(output?.fields)) lines.push(output.valid ? "所有引用位置与原文一致。" : "发现缺失或无效引用，请检查资料。");
  else if (Array.isArray(output)) output.forEach((row, i) => lines.push(typeof row === "string" ? "第 " + (i + 1) + " 条：" + row : (row.id || "第 " + (i + 1) + " 条") + " · 得分 " + format(row.score)));
  else if (output && typeof output === "object") for (const [key, item] of Object.entries(output)) lines.push(key + "：" + JSON.stringify(item));
  $("component-trial-summary").textContent = lines.join("； ") || "调用完成，返回数据如下。";
  $("component-trial-result").textContent = JSON.stringify(value, null, 2);
  $("component-trial-details").hidden = false;
}
function componentSetMigration(value) {
  componentPendingMigration = value;
  $("component-migration").hidden = !value;
  if (!value) return;
  const old = componentCatalog.bindings.find(row => row.slot === value.slot);
  const prior = componentVersion(old);
  const target = componentCatalog.versions.find(row => row.version === value.version);
  const changes = prior?.task === "document_field_completeness" && prior.contract_version === "1" && target?.contract_version === "2"
    ? "新版新增邮箱格式检查，以及 invalid_fields、valid 返回字段。原有完整性规则已通过回归验证。"
    : "新版的输入输出规范与当前版本不同，请先查看生成和验证报告。";
  $("component-migration-note").textContent = changes + " 当前仍使用 " + (old?.version || "原版本") + "；确认后将切换到 " + value.version + "，旧版会保留。";
}
function componentArtifactButtons(value) {
  const links = $("component-artifacts"); links.replaceChildren();
  if (["draft", "draft_failed"].includes(value.status)) {
    if (value.status === "draft") {
      const trial = node("button", "试用候选"); trial.type = "button";
      trial.addEventListener("click", () => { componentPrepareDraftTrial(value); componentReveal("component-trial"); }); links.append(trial);
    }
    if (value.has_code !== false) links.append(downloadButton("下载候选代码", "/v1/runs/" + value.run_id + "/artifacts/candidate.zip", value.run_id + "-candidate.zip"));
    const report = node("button", "查看候选报告", "secondary"); report.type = "button";
    report.addEventListener("click", async () => { await selectRun(value.run_id); if (!$("draft-report").hidden) $("draft-report").scrollIntoView({behavior:"smooth", block:"start"}); }); links.append(report);
    return;
  }
  const version = componentCatalog.versions.find(row => row.version === value.version);
  const slot = value.decision?.slot || value.slot || value.binding?.slot || componentCurrentSlot;
  const binding = componentCatalog.bindings.find(row => row.slot === slot);
  if (binding && binding.version === value.version) {
    const trial = node("button", "试用组件"); trial.type = "button";
    trial.addEventListener("click", () => { componentCurrentSlot = slot; const current = componentBinding(); if (current) showComponentResult({...current, status:"active"}); componentPrepareTrial(); componentReveal("component-trial"); }); links.append(trial);
  }
  const runId = value.run_id || version?.run_id;
  if (runId) {
    if (version?.status === "approved") links.append(downloadButton("下载代码包", "/v1/runs/" + runId + "/export", runId + ".zip", false, "POST"));
    const report = node("button", "查看验证报告", "secondary"); report.type = "button";
    report.addEventListener("click", async () => { await selectRun(runId); if (!$("result").hidden) $("result").scrollIntoView({behavior:"smooth", block:"start"}); }); links.append(report);
  }
}
function showComponentResult(value) {
  if (value.status === "draft_trial") {
    if (componentDraftContext?.run_id === value.run_id) { componentShowTrialResult(value); return; }
    $("component-output").hidden = false; $("component-trial").hidden = true;
    $("component-state").textContent = "候选历史试用"; $("component-state").classList.add("warning");
    $("component-summary").textContent = "这是未完成独立业务验证的候选试用结果。";
    $("component-result").textContent = JSON.stringify(value, null, 2); $("component-result-details").open = true;
    $("component-artifacts").replaceChildren(); return;
  }
  if (value.output === undefined) { componentDraftContext = null; $("component-trial").hidden = true; }
  if (value.output !== undefined) {
    const binding = componentCatalog.bindings.find(row => row.version === value.version && row.revision === value.revision);
    if (binding) { componentCurrentSlot = binding.slot; componentPrepareTrial(); }
    else {
      componentLastResult = value;
      $("component-state").textContent = "历史调用结果";
      $("component-summary").textContent = "以下为 " + value.version + " 的归档结果，当前启用组件保持原样。";
      $("component-result").textContent = JSON.stringify(value, null, 2);
      componentSetMigration(null); componentArtifactButtons(value);
      $("component-state").classList.remove("failed");
      $("component-result-details").open = true;
    }
    $("component-output").hidden = false; $("component-trial").hidden = !binding;
    componentShowTrialResult(value);
    if (!binding) {
      $("component-summary").textContent += " " + $("component-trial-summary").textContent;
      $("component-trial-summary").textContent = ""; $("component-trial-details").hidden = true;
    }
    return;
  }
  componentLastResult = value;
  $("component-output").hidden = false;
  $("component-result").textContent = JSON.stringify(value, null, 2);
  const state = value.status || value.action || (value.version ? "active" : "");
  $("component-state").textContent = value.status ? factoryJobOutcome({status:"succeeded", kind:"component_ensure", result:value}).label : componentLabels[state] || "操作完成";
  $("component-state").classList.toggle("failed", ["rejected", "draft_failed"].includes(state));
  $("component-state").classList.toggle("warning", ["draft", "blocked", "migration_required"].includes(state));
  $("component-progress").textContent = "";
  $("component-summary").textContent = componentSummary(value);
  const slot = value.decision?.slot || value.slot || value.binding?.slot;
  if (slot) { componentCurrentSlot = slot; componentPrepareTrial(); }
  if (state === "draft" && value.status === "draft" && value.run_id) componentPrepareDraftTrial(value);
  componentSetMigration(state === "migration_required" ? {
    slot: value.decision.slot, version:value.version, expected_revision:value.decision.observed_revision,
    parameters:value.decision.parameters, allow_contract_change:true
  } : null);
  componentArtifactButtons(value);
}
window.showComponentResult = showComponentResult;
function componentAddInput(id, label, value, multiline = false, hint = "") {
  const caption = node("label", label); caption.htmlFor = id;
  const input = node(multiline ? "textarea" : "input"); input.id = id; input.value = value;
  if (multiline) input.rows = 3;
  $("component-trial-fields").append(caption, input);
  if (hint) $("component-trial-fields").append(node("p", hint, "muted"));
}
function componentPrepareTrial() {
  componentDraftContext = null;
  const binding = componentBinding(), version = componentVersion(binding);
  if (!binding || !version) { $("component-trial").hidden = true; componentTrialKey = ""; return; }
  const key = binding.slot + "/" + binding.revision;
  $("component-trial-caption").textContent = (taskNames[version.task] || version.task) + " · " + version.version;
  $("component-trial-fields").hidden = false; $("component-json-options").hidden = false; $("component-invoke").hidden = false;
  if (componentTrialKey === key) return;
  componentTrialKey = key;
  $("component-trial-fields").replaceChildren();
  $("component-trial-summary").textContent = ""; $("component-trial-details").hidden = true; $("component-use-json").checked = false;
  if (version.task === "document_field_completeness") {
    componentAddInput("trial-required", "需要哪些字段", (binding.parameters.required_fields || ["name", "email"]).join(", "), false, "多个字段用逗号分隔。试用仅检查一条记录；批量记录可用开发者输入。");
    componentAddInput("trial-fields", "资料内容", "name: 示例公司\nemail: bad-email", true, "一行一个字段，格式为“字段名: 内容”。空内容会被检查为缺失。");
    if (version.contract_version === "2") componentAddInput("trial-email", "需要检查邮箱格式的字段", Object.keys(binding.parameters.field_formats || {email:"email"}).join(", "));
  } else if (version.task === "memory_retrieval") {
    componentAddInput("trial-query", "想查找什么", "Python 日志");
    componentAddInput("trial-memories", "待检索的记忆（一行一条）", "Python 日志排查与异常处理\n周末旅行计划", true);
  } else if (version.task === "memory_classification") {
    componentAddInput("trial-texts", "待分类文本（一行一条）", "Python 异常日志排查", true, "使用生成时冻结的训练样本预测类别。");
  } else {
    componentAddInput("trial-source", "来源原文", "示例公司成立于2020年。", true);
    componentAddInput("trial-quote", "引用文字", "示例公司");
    componentAddInput("trial-start", "引用开始位置（从 0 计数）", "0");
    componentAddInput("trial-end", "引用结束位置（不包含该位置）", "4");
  }
  $("component-input").value = JSON.stringify(componentTrialInput(), null, 2);
}
function componentList(value) { return value.split(/[,，、\n]/).map(row => row.trim()).filter(Boolean); }
function componentTrialInput() {
  if (componentDraftContext) {
    if ($("component-use-json").checked) return componentJSON("component-input");
    return Object.fromEntries([...$("component-trial-fields").querySelectorAll("[data-key]")].map(input => [input.dataset.key, input.dataset.jsonType === "string" ? input.value : JSON.parse(input.value)]));
  }
  if ($("component-use-json").checked) return componentJSON("component-input");
  const binding = componentBinding(), version = componentVersion(binding);
  if (!version) throw new Error("请先选择已启用组件。");
  if (version.task === "document_field_completeness") {
    // Null-prototype fields retain literal keys such as __proto__ safely.
    const fields = Object.create(null);
    for (const line of $("trial-fields").value.split("\n")) {
      if (!line.trim()) continue;
      const match = line.match(/^([^:：]+)[:：](.*)$/);
      if (!match || !match[1].trim()) throw new Error("资料内容请一行填写一个“字段名: 内容”。");
      const key = match[1].trim();
      if (Object.hasOwn(fields, key)) throw new Error("字段名重复：" + key);
      fields[key] = match[2].trim();
    }
    const payload = {records:[{record_id:"company", fields}], required_fields:componentList($("trial-required").value)};
    if (version.contract_version === "2") payload.field_formats = Object.fromEntries(componentList($("trial-email").value).map(key => [key, "email"]));
    return payload;
  }
  if (version.task === "memory_retrieval") return {query:$("trial-query").value, memories:$("trial-memories").value.split("\n").filter(line => line.trim()).map((text, i) => ({id:"note-" + (i + 1), text, source:"synthetic://web-trial"})), top_k:binding.parameters.top_k || 5};
  if (version.task === "memory_classification") return {texts:$("trial-texts").value.split("\n").filter(line => line.trim())};
  const start = $("trial-start").value.trim(), end = $("trial-end").value.trim();
  if (!/^\d+$/.test(start) || !/^\d+$/.test(end) || !Number.isSafeInteger(Number(start)) || !Number.isSafeInteger(Number(end))) throw new Error("引用位置需要填写非负整数。");
  return {sources:[{source_id:"doc", segments:[{segment_id:"p1", text:$("trial-source").value}]}], fields:[{field_id:"name", references:[{source_id:"doc", segment_id:"p1", start:Number(start), end:Number(end), quote:$("trial-quote").value}]}]};
}
function renderComponentCatalog() {
  const catalog = componentCatalog;
  if (!catalog.bindings.some(row => row.slot === componentCurrentSlot)) componentCurrentSlot = catalog.bindings[0]?.slot || "";
  const drafts = catalog.drafts || [];
  $("component-catalog-caption").textContent = catalog.bindings.length + " 个启用组件" + (drafts.length ? " · " + drafts.length + " 条候选记录" : "");
  $("component-draft-library").hidden = !drafts.length; $("component-draft-list").replaceChildren();
  for (const draft of drafts.slice().reverse()) {
    const item = node("li"), button = node("button", draft.title + " · " + (draft.status === "draft" ? "待业务验证" : "检查未通过"), "history-button"); button.type = "button";
    button.addEventListener("click", async () => { await selectRun(draft.run_id); $("draft-report").scrollIntoView({behavior:"smooth", block:"start"}); }); item.append(button); $("component-draft-list").append(item);
  }
  $("component-empty").hidden = !!catalog.bindings.length;
  $("component-management-controls").hidden = !catalog.bindings.length;
  const targets = $("component-target"); targets.replaceChildren();
  for (const binding of catalog.bindings) {
    const version = componentVersion(binding), option = node("option", (taskNames[version?.task] || binding.slot) + " · " + binding.slot);
    option.value = binding.slot; targets.append(option);
  }
  targets.value = componentCurrentSlot;
  const binding = componentBinding(), current = componentVersion(binding);
  $("component-bindings").replaceChildren(...(binding ? [node("li", "正在使用 " + binding.version)] : []));
  const versions = $("component-versions"), select = $("component-version"), prior = select.value;
  versions.replaceChildren(); select.replaceChildren();
  for (const version of catalog.versions.filter(row => row.component === current?.component)) {
    versions.append(node("li", version.version + " · " + (componentLabels[version.status] || version.status) + (binding.version === version.version ? " · 当前使用" : "")));
    if (version.status === "approved") { const option = node("option", version.version); option.value = version.version; select.append(option); }
  }
  if ([...select.options].some(option => option.value === prior)) select.value = prior;
  if (!componentDraftContext) componentPrepareTrial(); componentControls();
}
async function refreshComponents() {
  componentCatalog = await request("/v1/components");
  renderComponentCatalog();
  if (!componentLastResult && componentBinding()) showComponentResult({...componentBinding(), status:"active"});
  return componentCatalog;
}
window.refreshComponents = refreshComponents;
window.resetComponents = () => {
  componentCatalog = {bindings:[], versions:[], rules:[]}; componentCurrentSlot = ""; componentLastResult = null; componentSetMigration(null);
  componentDraftContext = null; $("component-output").hidden = true; renderComponentCatalog();
};
async function componentJob(path, body) {
  const job = await request(path, {method:"POST", body:JSON.stringify(body)});
  const current = ++selection; clearTimeout(timer); selectedJob = job.job_id;
  $("result").hidden = true; $("draft-report").hidden = true;
  const deadline = Date.now() + 15 * 60 * 1000;
  while (Date.now() < deadline) {
    const status = await request("/v1/jobs/" + job.job_id);
    if (current === selection) renderJob(status);
    if (status.status === "failed") throw new Error(status.error || "组件操作失败");
    if (status.status === "succeeded") return status.result;
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error("任务仍在执行，可在最近任务中查看：" + job.job_id);
}
async function componentOperation(action) {
  if (componentBusy || !connected) return;
  componentBusy = true; notice(""); componentControls();
  try {
    const result = await action();
    // A refresh failure must not erase an already completed operation.
    if (result) showComponentResult(result);
    try { await refreshComponents(); await refreshHistory(); }
    catch (error) { notice("操作已结束，但目录刷新失败：" + error.message); }
    if (result && result.output === undefined) { showComponentResult(result); componentReveal("component-output"); }
  } catch (error) {
    notice(error.message);
    if (!$("component-output").hidden) $("component-progress").textContent = "操作未完成，已有组件保持原样。";
  } finally { componentBusy = false; componentControls(); }
}
function componentDemand() {
  const description = $("component-description").value.trim();
  if (description.length < 5) throw new Error("请至少输入 5 个字符的需求描述。");
  const name = $("component-name").value.trim();
  return {description, component:name || "default", ...(name ? {slot:name} : {}), action:$("component-action").value,
    parameters:componentJSON("component-parameters"), allow_contract_change:false};
}
$("component-form").addEventListener("submit", event => { event.preventDefault(); componentOperation(() => componentJob("/v1/components/ensure", componentDemand())); });
$("component-plan").addEventListener("click", () => componentOperation(() => componentJob("/v1/components/plan", componentDemand())));
$("component-refresh").addEventListener("click", () => componentOperation(() => refreshComponents().then(() => null)));
const componentExamples = {
  numbers:{description:"帮我生成一个文本信息提取组件，提取文本中的所有数字", parameters:{}},
  fields:{description:"生成字段完整性检查组件，保留必填字段顺序并正确处理空值、0 和 false。", parameters:{required_fields:["name", "email"]}},
  email:{description:"在字段完整性组件上增加邮箱格式检查，保留原有完整性行为。", parameters:{required_fields:["name", "email"], field_formats:{email:"email"}}},
  retrieval:{description:"生成个人记忆检索组件，输入查询和记忆列表，返回相关记忆的 ID 与分数。", parameters:{top_k:5}},
  classification:{description:"生成文本记忆分类组件，根据训练样本对技术笔记分类，返回与文本顺序一致的标签。", parameters:{}},
  references:{description:"生成来源引用检查组件，检查来源 ID、文本块、Unicode 字符范围与引用原文是否精确一致。", parameters:{}}
};
$("component-example").addEventListener("change", () => {
  const example = componentExamples[$("component-example").value]; if (!example) return;
  $("component-description").value = example.description; $("component-parameters").value = JSON.stringify(example.parameters, null, 2);
  $("component-name").value = ""; $("component-action").value = "auto";
});
$("component-open").addEventListener("click", () => { if (componentBinding()) { showComponentResult({...componentBinding(), status:"active"}); componentReveal("component-output"); } });
$("component-target").addEventListener("change", () => { componentCurrentSlot = $("component-target").value; renderComponentCatalog(); if (componentBinding()) showComponentResult({...componentBinding(), status:"active"}); });
$("component-json-options").addEventListener("toggle", () => {
  if ($("component-json-options").open && !$("component-use-json").checked) {
    try { $("component-input").value = JSON.stringify(componentTrialInput(), null, 2); } catch { /* Keep editable JSON when the simple form is incomplete. */ }
  }
});
$("component-invoke").addEventListener("click", () => componentOperation(() => {
  if (componentDraftContext) return componentJob("/v1/components/drafts/" + componentDraftContext.run_id + "/invoke", {input:componentTrialInput()});
  const binding = componentBinding(), version = componentVersion(binding);
  return componentJob("/v1/components/slots/" + componentSlot() + "/invoke", {input:componentTrialInput(), expected_revision:binding.revision, contract_version:version.contract_version});
}));
$("component-confirm-migration").addEventListener("click", () => componentOperation(async () => {
  const pending = componentPendingMigration; if (!pending) return null;
  const {slot, ...payload} = pending;
  const result = await componentJob("/v1/components/slots/" + encodeURIComponent(slot) + "/activate", payload);
  return {...result, status:"active"};
}));
$("component-activate").addEventListener("click", () => componentOperation(async () => {
  const binding = componentBinding(), version = componentCatalog.versions.find(row => row.version === $("component-version").value);
  const payload = {version:version.version, expected_revision:binding.revision, parameters:componentJSON("component-parameters"), allow_contract_change:false};
  if (componentVersion(binding).contract_version !== version.contract_version) {
    componentSetMigration({...payload, slot:binding.slot, allow_contract_change:true}); componentReveal("component-output"); return null;
  }
  const result = await componentJob("/v1/components/slots/" + componentSlot() + "/activate", payload);
  return {...result, status:"active"};
}));
$("component-rollback").addEventListener("click", () => componentOperation(async () => {
  const result = await componentJob("/v1/components/slots/" + componentSlot() + "/rollback", {expected_revision:componentBinding().revision});
  return {...result, status:"active"};
}));
$("component-import").addEventListener("click", () => componentOperation(() => componentJob("/v1/components/import", {run_id:$("component-import-run").value.trim(), ...($("component-name").value.trim() ? {component:$("component-name").value.trim()} : {})})));
$("component-feedback").addEventListener("click", () => componentOperation(() => componentJob("/v1/components/slots/" + componentSlot() + "/feedback", {kind:$("component-feedback-kind").value, summary:$("component-feedback-text").value})));
$("component-history").addEventListener("click", () => componentOperation(async () => {
  const result = await request("/v1/components/history?slot=" + componentSlot());
  showComponentResult(result); $("component-result-details").open = true; return null;
}));
// Refresh in the background; no separate refresh step is needed in the ordinary path.
setInterval(async () => {
  if (!connected || componentBusy || document.visibilityState !== "visible") return;
  try { await refreshComponents(); await refreshHistory(); } catch { /* Explicit actions report connection errors. */ }
}, 15000);
componentControls();

function componentPrepareDraftTrial(value) {
  componentDraftContext = value; componentTrialKey = "";
  $("component-trial-fields").replaceChildren(); $("component-trial-summary").textContent = ""; $("component-trial-details").hidden = true;
  $("component-trial-caption").textContent = "候选试用 · 基础检查通过，独立业务验证待补充";
  $("component-invoke").hidden = false; $("component-json-options").hidden = false; $("component-use-json").checked = false;
  const payload = value.proposal?.examples?.[0]?.input || {};
  $("component-input").value = JSON.stringify(payload, null, 2);
  for (const [key, item] of Object.entries(payload)) {
    const id = "draft-input-" + $("component-trial-fields").children.length;
    componentAddInput(id, value.proposal?.input_schema?.properties?.[key]?.title || key, typeof item === "string" ? item : JSON.stringify(item), typeof item === "string");
    $(id).dataset.key = key; $(id).dataset.jsonType = typeof item === "string" ? "string" : "json";
  }
  $("component-trial-fields").hidden = false; componentControls();
}
function renderDraftReport(report) {
  $("result").hidden = true;
  const box = $("draft-report"); box.hidden = false; box.replaceChildren();
  const outcome = factoryDraftOutcome(report);
  box.append(node("h2", "候选代码与检查记录"), node("p", outcome.label + "。" + outcome.note), node("p", report.run_id + " · " + report.provider + " / " + report.model + " · " + report.runner, "muted"));
  if (report.error) box.append(node("p", report.error));
  if (report.spec_reviews?.length) {
    const reviews = node("details"); reviews.append(node("summary", "生成前的规格一致性审查"), node("pre", JSON.stringify(report.spec_reviews, null, 2))); box.append(reviews);
  }
  // Old sealed reports have observations but no explicit difference rows.
  const failures = report.checks.example_failures || (report.proposal?.examples || []).map((example, index) => ({
    ...example, case_id:"example_" + (index + 1), actual:report.checks.observations?.[index]?.result,
    matched:report.checks.example_matches?.[index]
  })).filter(example => example.matched === false);
  if (failures.length) {
    box.append(node("h3", "未通过的示例"));
    for (const example of failures) {
      const detail = node("div", undefined, "check-failure");
      detail.append(node("h4", "示例 " + example.case_id.replace("example_", "")),
        node("p", "输入：" + JSON.stringify(example.input)),
        node("p", "期望：" + JSON.stringify(example.expected)),
        node("p", "实际：" + (example.actual === undefined ? "没有返回结果" : JSON.stringify(example.actual))));
      if (example.first_difference) detail.append(node("p", "差异位置：" + example.first_difference.path));
      if (example.execution_error) detail.append(node("p", example.execution_error));
      box.append(detail);
    }
  }
  if (report.proposal) {
    box.append(node("h3", "本次采用的假设"));
    const list = node("ul"); for (const item of report.proposal.assumptions) list.append(node("li", item)); box.append(list);
    const limits = node("details"); limits.append(node("summary", "已知限制与建议接口"), node("pre", JSON.stringify(report.proposal, null, 2))); box.append(limits);
  }
  box.append(node("h3", "来源检索"), node("p", "GitHub 检索状态：" + ({searched:"已搜索", failed:"搜索失败，已保留原因", skipped_offline:"离线演示未联网"}[report.search.status] || report.search.status) + "；组件库参考 " + report.sources.local_components.length + " 条，源码快照 " + report.sources.repositories.length + " 个。"));
  for (const source of report.sources.repositories) box.append(node("p", source.repository + " · " + source.license + " · 提交 " + source.commit));
  if (report.search.error) box.append(node("p", report.search.error));
  const checks = node("details"); checks.append(node("summary", "基础检查与修复记录"), node("pre", JSON.stringify(report.attempts, null, 2))); box.append(checks);
  box.append(node("p", (report.provider === "mock" ? "离线演示样例" : "模型提出的示例期望") + "不能替代独立金标或真实业务验收。", "muted"));
  const links = node("div", undefined, "downloads");
  if (report.status === "draft") {
    const trial = node("button", "试用此候选"); trial.type = "button";
    trial.addEventListener("click", () => { const value = {status:"draft", run_id:report.run_id, proposal:report.proposal, has_code:true}; showComponentResult(value); componentPrepareDraftTrial(value); componentReveal("component-trial"); }); links.append(trial);
  }
  if (report.code_hash) links.append(downloadButton("下载候选代码", "/v1/runs/" + report.run_id + "/artifacts/candidate.zip", report.run_id + "-candidate.zip"));
  links.append(downloadButton("下载报告", "/v1/runs/" + report.run_id + "/artifacts/report.json", "report.json")); box.append(links);
}

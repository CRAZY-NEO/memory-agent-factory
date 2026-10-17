"use strict";
const $ = (id) => document.getElementById(id);
const names = { queued:"排队中", running:"执行中", succeeded:"已完成", failed:"失败", passed:"通过", failed_validation:"验证未通过", failed_generation:"代码生成中断或响应不合格", failed_test:"最终测试未通过", prepare:"准备材料", understand:"理解需求", retrieve:"检索能力知识", plan:"规划候选", generate:"生成 / 修复代码", validate:"自动验证", review:"诊断问题", advance:"切换候选", select:"选择并冻结版本", test:"独立最终测试", persist:"报告与知识回写" };
Object.assign(names, { draft_plan:"规划候选接口", component_library_search:"检索组件库", draft_generate:"生成候选代码", draft_validate:"基础执行与示例检查", draft_repair:"修复候选代码", draft_persist:"保存候选与报告", route:"判断能力与执行路径", source_search:"理解新能力与搜索规划", github_search:"搜索 GitHub", pick_repository:"选择来源仓库", download_repository:"下载固定提交源码", extract_repository:"AST 抽取源码", propose_algorithm:"提议新算法能力", approve_algorithm:"验证后登记算法" });
Object.assign(names, {draft:"候选待业务验证", draft_failed:"候选检查未通过", draft_spec_review:"规格一致性审查"});
const categories = { direct:"词面基线", paraphrase:"同义改写", hard_negative:"相近负例", multi_relevance:"多相关文档", lexical:"词面基线", near_boundary:"相近类别" };
const metricNames = { recall_at_k:"Recall@k", mrr_at_k:"MRR@k", precision_at_k:"Precision@k", p95_ms:"P95 ms", accuracy:"Accuracy", macro_f1:"Macro F1", classification_p95_ms:"P95 ms" };
const taskNames = {python_draft:"新能力候选", memory_retrieval:"记忆检索", memory_classification:"文本记忆分类", document_field_completeness:"字段完整性", document_source_references:"来源引用检查" };
// The opening ticket is consumed once; remove it before any authenticated request.
function takeOpeningTicket() {
  const ticket = new URLSearchParams(location.hash.slice(1)).get("login");
  if (ticket) history.replaceState(null, "", location.pathname + location.search);
  return ticket;
}
const openingTicket = takeOpeningTicket();
let connected = false;
let accessToken = "";
let timer = null;
let selection = 0;
let selectedJob = null;
function node(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function notice(text) { $("message").textContent = text; $("message").hidden = !text; }
function badge(id, status) { $(id).textContent = names[status] || status; $(id).classList.toggle("failed", status.startsWith("failed")); $(id).classList.remove("warning"); }
function format(value) { return typeof value === "number" ? value.toFixed(4) : "—"; }
function disconnect() { connected = false; $("access-panel").classList.remove("is-connected"); if (window.resetComponents) window.resetComponents(); $("mode").disabled = true; $("token").hidden = false; document.querySelector('label[for="token"]').hidden = false; $("connect").hidden = false; accessToken = ""; $("token").value = ""; clearTimeout(timer); selection++; selectedJob = null; $("submit").disabled = true; $("refresh").disabled = true; $("disconnect").hidden = true; $("connect").disabled = false; $("connection").textContent = "未连接"; $("result").hidden = true; $("draft-report").hidden = true; $("jobs").replaceChildren(node("li", "连接后查看。", "muted")); $("runs").replaceChildren(node("li", "连接后查看。", "muted")); $("events").replaceChildren(node("li", "提交需求后查看执行阶段。", "empty")); $("job-caption").textContent = "提交需求后，查看实际执行阶段与修复轮次。"; badge("job-status", "等待提交"); }
async function request(path, options = {}, blob = false) {
  if (!connected) throw new Error("请先连接工作空间。");
  if (!path.startsWith("/v1/")) throw new Error("无效的接口路径。");
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, { ...options, signal:controller.signal, headers:{ ...(accessToken ? { "Authorization":"Bearer " + accessToken } : {}), ...(options.body ? { "Content-Type":"application/json" } : {}) } });
    if (!response.ok) {
      let detail = "请求失败（" + response.status + "）";
      try { const data = await response.json(); detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail); } catch { /* Keep HTTP status for non-JSON responses. */ }
      if (response.status === 401) { disconnect(); throw new Error("访问密钥无效，请重新连接。"); }
      throw new Error(detail);
    }
    return blob ? await response.blob() : await response.json();
  } catch (error) { if (error.name === "AbortError") throw new Error("请求超时，请检查服务连接后刷新。"); throw error; }
  finally { clearTimeout(timeout); }
}
async function download(path, filename, method = "GET") {
  try { const data = await request(path, { method }, true); const url = URL.createObjectURL(data); const link = node("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  catch (error) { notice(error.message); }
}
function downloadButton(label, path, filename, small = false, method = "GET") { const button = node("button", label, small ? "text-button" : "secondary"); button.type = "button"; button.addEventListener("click", async () => { button.disabled = true; try { await download(path, filename, method); } finally { button.disabled = false; } }); return button; }
async function refreshHistory() {
  const [jobs, runs] = await Promise.all([request("/v1/jobs?limit=12"), request("/v1/runs?limit=20")]);
  $("jobs").replaceChildren();
  for (const job of jobs.jobs) { const item = node("li"); const kind = { factory:"算法生成", discovery:"发现新算法", ingest:"能力抽取", search:"检索试用", component_plan:"需求计划", component_ensure:"组件生成", component_invoke:"组件试用", component_draft_invoke:"候选试用", component_activate:"切换版本", component_rollback:"版本回退", component_import:"登记组件", component_feedback:"组件反馈" }[job.kind] || job.kind; const outcome = factoryJobOutcome(job); const button = node("button", `${outcome.label} · ${kind} · ${job.id.slice(0, 8)}`, "history-button"); button.dataset.outcome = outcome.state; button.type = "button"; button.addEventListener("click", () => selectJob(job.id)); item.append(button); $("jobs").append(item); }
  if (!jobs.jobs.length) $("jobs").append(node("li", "还没有任务。", "muted"));
  $("runs").replaceChildren();
  for (const run of runs.runs) { const item = node("li"); const button = node("button", `${names[run.status] || run.status} · ${taskNames[run.task] || "记忆检索"} · ${run.algorithm || "未发布"}`, "history-button"); button.type = "button"; button.append(node("small", `${run.run_id} · ${run.provider} / ${run.model}`)); button.addEventListener("click", () => selectRun(run.run_id)); item.append(button); $("runs").append(item); }
  if (!runs.runs.length) $("runs").append(node("li", "还没有完成的运行。", "muted"));
}
function renderJob(job) {
  const outcome = factoryJobOutcome(job);
  $("job-status").textContent = outcome.label; $("job-status").classList.toggle("failed", outcome.tone === "danger"); $("job-status").classList.toggle("warning", outcome.tone === "warning");
  const stage = job.progress?.stage;
  $("job-caption").textContent = `任务 ${job.id.slice(0, 8)} · ${["queued", "running"].includes(job.status) ? names[stage] || stage || outcome.label : outcome.label}${job.run_id ? " · " + job.run_id : ""}`;
  const list = $("events"); list.replaceChildren();
  for (const event of job.progress?.events || []) { const item = node("li", names[event.node] || event.node); const candidate = event.candidate_index === null ? "" : `候选 ${event.candidate_index + 1} · r${event.revision ?? 0} · `; item.append(node("small", candidate + new Date(event.time).toLocaleTimeString())); list.append(item); }
  if (!list.children.length) list.append(node("li", outcome.note, "empty"));
  list.scrollTop = list.scrollHeight;
}
async function pollJob(id, current, retries = 0) {
  if (current !== selection || !connected) return;
  try {
    const job = await request(`/v1/jobs/${id}`); if (current !== selection) return;
    renderJob(job);
    if (["succeeded", "failed"].includes(job.status)) {
      clearTimeout(timer);
      if (job.error) notice(job.error);
      const runId = job.run_id || job.result?.run_id;
      if (job.kind.startsWith("component_") && job.result && window.showComponentResult) { await window.refreshComponents(); if (current === selection) window.showComponentResult(job.result); }
      else if (runId && ["factory", "discovery"].includes(job.kind)) { try { const report = await request(`/v1/runs/${runId}/report`); if (current === selection) renderReport(report); } catch (error) { if (current === selection) notice(`${job.error || "任务结束"}；${error.message}`); } }
      else if (job.result?.hits) { $("job-caption").textContent += " · 检索任务已完成"; }
      if (current === selection && connected) await refreshHistory();
    } else { timer = setTimeout(() => pollJob(id, current), 3000); }
  } catch (error) { if (current !== selection) return; notice(error.message); if (connected && retries < 3) timer = setTimeout(() => pollJob(id, current, retries + 1), 5000); }
}
function selectJob(id) { selection++; clearTimeout(timer); selectedJob = id; $("result").hidden = true; $("draft-report").hidden = true; notice(""); pollJob(id, selection); }
async function selectRun(id) {
  const current = ++selection;
  selectedJob = null;
  clearTimeout(timer);
  $("result").hidden = true; $("draft-report").hidden = true;
  notice("");
  try {
    const report = await request(`/v1/runs/${id}/report`);
    if (current !== selection) return;
    renderReport(report);
    badge("job-status", "历史记录");
    $("job-caption").textContent = "正在查看已完成的运行 " + id;
    $("events").replaceChildren(...(report.workflow_events || []).map((stage) => node("li", names[stage] || stage)));
    $("events").scrollTop = $("events").scrollHeight;
  } catch (error) { if (current === selection) notice(error.message); }
}
function reportMetrics(report) {
  const catalog = report.plugin_catalog?.metrics || [];
  const keys = Object.keys(report.validation_config?.metrics || {});
  const enabled = keys.length ? keys : [...new Set([...Object.keys(report.test?.metrics || {}), ...Object.keys(report.selected?.validation?.metrics || {})])];
  return enabled.map((id) => ({ id, name:catalog.find((metric) => metric.id === id)?.name || metricNames[id] || id }));
}
function categoryTable(title, evaluation, metrics, classification) {
  if (!evaluation?.by_category) return null;
  const box = node("div", undefined, "table-scroll"); const table = node("table"); table.append(node("caption", title)); const head = node("thead"); const header = node("tr");
  const review = Array.isArray(evaluation.per_case);
  if (review) { const header = node("tr"); for (const label of ["检查类型", "样例数", "精确通过数"]) header.append(node("th", label)); const head = node("thead"); head.append(header); const body = node("tbody"); for (const [category, group] of Object.entries(evaluation.by_category)) { const row = node("tr"); for (const value of [category, group.case_count, group.correct]) row.append(node("td", value)); body.append(row); } table.replaceChildren(node("caption", title), head, body); box.append(table); return box; }
  const columns = [classification ? "样本类型" : "查询类型", "数量", ...metrics.map((metric) => metric.name), "失败样本", ...(classification ? [] : ["负例优先"])];
  for (const label of columns) header.append(node("th", label)); head.append(header); table.append(head); const body = node("tbody");
  for (const [category, group] of Object.entries(evaluation.by_category)) { const row = node("tr"); for (const value of [categories[category] || category, group.example_count ?? group.query_count ?? group.count ?? "—", ...metrics.map((metric) => format(group.metrics?.[metric.id])), group.failure_count ?? "—", ...(classification ? [] : [group.hard_negative_confusion_count ?? "—"])]) row.append(node("td", value)); body.append(row); }
  table.append(body); box.append(table); return box;
}
function generationEvidence(generation = {}, mode) {
  if (generation.backend === "mock_template_baseline") return "产物来源：离线模板基线（非模型生成）";
  if (generation.backend === "deterministic_source_adapter") return "产物来源：来源代码确定性复用（未调用模型生成）";
  if (generation.backend === "historical_replay") return "产物来源：历史代码重放（非本轮生成）";
  if (generation.reference_provided === true) return "模型输入：算法规格与完整参考实现";
  if (generation.reference_provided === false) return "模型输入：算法规格（未提供完整参考实现）";
  const strategy = { spec:"按规格生成", reference:"参考复刻", source_adaptation:"来源代码适配" }[mode];
  return strategy ? `输入策略：${strategy}；实际参考记录未保存` : "实际参考输入：旧记录未保存";
}
function renderReport(report) {
  if (report.kind === "component_draft") { renderDraftReport(report); return; }
  $("draft-report").hidden = true;
  const policy = report.validation_config || {};
  $("result").hidden = false; badge("run-status", report.status);
  const selected = report.selected;
  const classification = report.request?.task === "memory_classification";
  const metrics = reportMetrics(report);
  $("run-caption").textContent = `${report.run_id} · ${taskNames[report.request?.task] || "记忆检索"} · ${report.provider} / ${report.model} · ${report.runner} · ${selected ? "选定 " + selected.algorithm + " r" + selected.revision : "无可发布候选"}` + ` · ${generationEvidence(selected?.generation || { backend:report.generation_backend }, report.generation_mode || policy.generation_mode)} · 历史经验：${policy.history_mode || "旧版"} · 扩展要求：${(policy.requirements || []).join(", ") || "无"}`;
  $("design").textContent = report.plan?.design_explanation || "";
  const routing = $("routing-evidence"); routing.replaceChildren(); routing.hidden = !report.routing;
  if (report.routing) {
    const labels = { reuse:"使用已有能力", discover:"发现新方法", compare_after_discovery:"发现后比较候选" };
    routing.append(node("p", `执行路径：${labels[report.routing.action] || report.routing.action} · ${report.routing.reason}`));
    if (report.routing.discovery_run_id) {
      const button = node("button", "查看发现与登记报告", "text-button"); button.type = "button";
      button.addEventListener("click", () => selectRun(report.routing.discovery_run_id)); routing.append(button);
    }
  }
  const evidence = $("discovery-evidence"); evidence.replaceChildren(); evidence.hidden = !report.discovery;
  if (report.discovery) {
    const d = report.discovery; const source = d.source;
    evidence.append(node("p", `来源：${source.repository} · ${source.license} · 提交 ${source.commit}`),
      node("p", d.release ? `已登记 ${d.release.id}；${d.release.enabled ? "已加入后续候选" : "可在配置中启用"}` : "本次没有登记新算法。"));
    for (const file of ["search_results.json", "proposal.json", "source_context.json", "source_contract.json", "extracted_repository.json"])
      evidence.append(downloadButton("下载 " + file, `/v1/runs/${report.run_id}/artifacts/${file}`, file, true));
  }
  $("metric-cards").replaceChildren();
  for (const metric of metrics) { const card = node("div", undefined, "metric"); card.append(node("span", "最终测试 · " + metric.name), node("strong", format(report.test?.metrics?.[metric.id]))); $("metric-cards").append(card); }
  const header = node("tr"); for (const label of ["算法 / 版本", "通过", ...metrics.map((metric) => metric.name), "问题与代码"]) header.append(node("th", label)); $("candidates").querySelector("thead").replaceChildren(header);
  const body = $("candidates").querySelector("tbody"); body.replaceChildren();
  for (const attempt of report.attempts) { const row = node("tr"); const values = attempt.validation.metrics;
    for (const value of [`${attempt.algorithm} r${attempt.revision}`, attempt.validation.passed ? "通过" : "未通过", ...metrics.map((metric) => format(values?.[metric.id]))]) row.append(node("td", value));
    const info = node("td"); info.append(node("p", generationEvidence(attempt.generation, report.generation_mode || policy.generation_mode), "muted")); if (attempt.validation.errors.length) { const details = node("details"); details.append(node("summary", `${attempt.validation.errors.length} 条问题`), node("pre", attempt.validation.errors.join("\n"))); info.append(details); }
    info.append(downloadButton("下载该版本代码", `/v1/runs/${report.run_id}/code/${attempt.algorithm}/${attempt.revision}`, `${attempt.algorithm}-r${attempt.revision}.py`, true)); row.append(info); body.append(row);
  }
  $("category-analysis").replaceChildren();
  for (const [title, evaluation] of [["选定候选 · 验证集分组表现", selected?.validation], ["选定版本 · 独立最终测试分组表现", report.test]]) {
    const table = categoryTable(title, evaluation, metrics, classification); if (table) $("category-analysis").append(table);
    if (classification && evaluation?.confusion_matrix) { const details = node("details"); details.append(node("summary", title + " · 混淆矩阵（行：真实，列：预测）"), node("pre", JSON.stringify(evaluation.confusion_matrix, null, 2))); $("category-analysis").append(details); }
  }
  if (classification) $("category-analysis").append(node("p", "分类延迟包含整批训练与预测，P95 来自 3 次调用测量。", "muted"));
  $("failures").replaceChildren(); let failureCount = 0;
  for (const [title, evaluation] of [["验证", selected?.validation], ["最终测试", report.test]]) {
    for (const row of evaluation?.per_query || []) { if (!row.failure_types?.length) continue; failureCount++; const item = node("div", undefined, "failure-item"); item.append(node("p", `${title} / ${row.query_id} · ${categories[row.category] || row.category} · ${row.query}`), node("pre", `类型：${row.failure_types.join(", ")}\n相关：${row.relevant_ids.join(", ")}\n漏召回：${row.missed_relevant_ids.join(", ")}\n负例优先：${JSON.stringify(row.hard_negative_confusions)}\n返回顺序：${row.predictions.map((hit) => hit.id).join(" → ")}`), node("p", row.annotation, "muted")); $("failures").append(item); }
    for (const row of evaluation?.per_case || []) { if (row.correct) continue; failureCount++; const item = node("div", undefined, "failure-item"); item.append(node("p", `${title} / ${row.id} · ${row.category}`), node("p", "未通过接口、功能或稳定性检查；具体错误见候选报告。")); $("failures").append(item); }
    for (const row of evaluation?.per_example || []) { if (row.label === row.prediction && !row.failure_types?.length) continue; failureCount++; const item = node("div", undefined, "failure-item"); item.append(node("p", `${title} / ${row.id} · ${categories[row.category] || row.category || "分类样本"} · ${row.text}`), node("pre", `真实标签：${row.label}\n预测标签：${row.prediction ?? "无预测"}`)); if (row.annotation) item.append(node("p", row.annotation, "muted")); $("failures").append(item); }
  }
  if (!failureCount) $("failures").append(node("p", selected ? "此报告未记录逐样本失败；旧版报告可能没有此分析字段。" : "所有候选均未通过，详情见候选问题及完整报告。", "muted"));
  $("downloads").replaceChildren();
  const files = report.status === "passed" ? [report.artifacts?.selected_code || (classification ? "selected_classification.py" : "selected_retrieval.py"), "plugin.json", "report.md", "report.json"] : ["report.md", "report.json"];
  if (report.capability_contract) files.push("capability_contract.json");
  const labels = { "capability_contract.json":"下载能力契约", "selected_retrieval.py":"下载选定算法", "selected_classification.py":"下载选定算法", "plugin.json":"下载插件清单", "report.md":"下载 Markdown 报告", "report.json":"下载 JSON 报告" };
  if (report.status === "passed") $("downloads").append(downloadButton("一键导出可运行 ZIP", `/v1/runs/${report.run_id}/export`, `${report.run_id}-deployment.zip`, false, "POST"));
  for (const file of files) $("downloads").append(downloadButton(labels[file] || "下载选定组件", `/v1/runs/${report.run_id}/artifacts/${file}`, file));
  $("export-note").hidden = report.status !== "passed";
  $("export-note").textContent = classification ? "分类包包含训练样本（文本及标签），不包含验证集或最终测试集。解压后按 README 安装依赖即可运行，无需连接模型。" : "部署包包含选定代码、运行入口、依赖与验证摘要。解压后按 README 安装依赖即可运行，无需连接模型。";
}
async function connectWorkspace(token = "") {
  $("connect").disabled = true; notice(""); accessToken = token; connected = true;
  try {
    const settings = await request("/v1/settings"); const d = settings.dataset;
    $("mode").disabled = false;
    $("mode").querySelector('option[value="discover"]').disabled = !settings.discovery_available;
    if (!settings.discovery_available && $("mode").value === "discover") $("mode").value = "auto";
    const supported = (settings.tasks || ["memory_retrieval"]).map((task) => taskNames[task] || task).join("、");
    $("routing-hint").textContent = `支持${supported}，自动使用对应的验证规则。` + (settings.discovery_available ? "仅检索任务支持发现新方法。" : "当前为离线基线演示；真实生成可使用已配置的模型接口。");
    const classificationData = settings.classification_dataset;
    const retrievalSummary = d ? `检索 v${d.version}：${d.memory_count} 条记忆，${d.splits.validation.query_count} 验证查询，${d.splits.test.query_count} 最终测试查询。` : "检索：未提供数据版本说明。";
    const classificationSummary = classificationData ? `分类：${classificationData.training_count ?? classificationData.splits?.train?.example_count ?? "—"} 条训练，${classificationData.validation_count ?? classificationData.splits?.validation?.example_count ?? "—"} 条验证，${classificationData.test_count ?? classificationData.splits?.test?.example_count ?? "—"} 条最终测试。` : "分类：使用独立的训练、验证及最终测试集。";
    $("dataset").textContent = retrievalSummary + " " + classificationSummary + " 文档检查使用独立的合成样例，数量和检查类型见运行报告。";
    $("connection").textContent = "已连接"; $("connection").title = supported;
    $("token").value = ""; $("token").hidden = true; document.querySelector('label[for="token"]').hidden = true; $("connect").hidden = true;
    $("access-panel").classList.add("is-connected"); $("disconnect").hidden = false; $("submit").disabled = false; $("refresh").disabled = false;
    await refreshHistory();
    if (window.refreshComponents) await window.refreshComponents();
  } catch (error) { disconnect(); notice(error.message); }
  finally { $("connect").disabled = false; }
}
$("access-form").addEventListener("submit", (event) => { event.preventDefault(); connectWorkspace($("token").value.trim()); });
$("disconnect").addEventListener("click", async () => {
  try { await fetch("/v1/session", { method:"DELETE" }); } finally { disconnect(); }
});
async function restoreSession(ticket = null) {
  try {
    if (ticket) {
      const response = await fetch("/v1/session/bootstrap", { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ ticket }) });
      if (!response.ok) throw new Error("自动登录链接已失效，请重新启动网页或使用备用访问密钥连接。");
      await connectWorkspace();
    } else {
      const response = await fetch("/v1/session");
      if (response.ok) await connectWorkspace();
    }
  } catch (error) { disconnect(); notice(error.message); }
}
window.addEventListener("hashchange", () => { const ticket = takeOpeningTicket(); if (ticket) restoreSession(ticket); });
restoreSession(openingTicket);
$("factory-form").addEventListener("submit", async (event) => { event.preventDefault(); const description = $("description").value.trim(); if (description.length < 5) { notice("请至少输入 5 个字符的能力描述。"); return; } $("submit").disabled = true; notice(""); try { const job = await request("/v1/factory/runs", { method:"POST", body:JSON.stringify({ description, mode:$("mode").value,
  // The ordinary UI always generates from specifications; full-reference runs remain an advanced API/CLI control.
  generation_mode:"spec", history_mode:$("history-mode").value,
  requirements:$("requirement-profile").value === "retrieval" ? ["retrieval_exclude_archived", "retrieval_deduplicate_text"] : $("requirement-profile").value === "classification" ? ["classification_ignore_disabled", "classification_deduplicate_training"] : [] }) }); selectJob(job.job_id); await refreshHistory(); } catch (error) { notice(error.message); } finally { $("submit").disabled = !connected; } });
$("description").addEventListener("input", () => { $("character-count").textContent = `${$("description").value.length} / 6000`; });
$("example").addEventListener("click", () => { $("description").value = "为个人技术记忆库分别复刻 TF-IDF、BM25、Jaccard 三种检索算法并比较。输入 query、memories、top_k，返回前 5 条相关记忆的 ID 与分数。保留中文单字/双字及英文词的算法规则，处理空输入，按配置验证 Recall、MRR、Precision 和稳定性；修复失败版本，选择通过验证的候选并回写知识库。"; if ($("mode").value === "discover") $("description").value = "为中文与英文混合技术笔记检索寻找当前能力目录尚未登记的 BM25Plus 方法。请自动搜索 GitHub 中的算法源码，保留其评分公式与默认参数，改造成 retrieve(query, memories, top_k=5)，在 Docker 中验证，合格后登记供以后使用。"; $("description").dispatchEvent(new Event("input")); });
$("refresh").addEventListener("click", async () => { notice(""); try { await refreshHistory(); if (selectedJob) selectJob(selectedJob); } catch (error) { notice(error.message); } });

$("new-example").addEventListener("click", () => { $("description").value = "为中文与英文混合技术笔记复刻 BM25Plus 检索方法，输入 query、memories、top_k=5，返回相关记忆 ID 和得分，通过验证后交付可复用代码。"; $("description").dispatchEvent(new Event("input")); });
$("classification-example").addEventListener("click", () => { $("mode").value = "auto"; $("description").value = "生成文本记忆分类算法，根据训练样本将技术笔记分类，输入 texts 与 training_examples，返回与文本顺序一致的类别标签。比较配置中的分类候选，检查 Accuracy、Macro F1、运行稳定性和接口，有限修复后用独立最终测试集验证并沉淀为可复用插件。"; $("description").dispatchEvent(new Event("input")); });

for (const [id, description] of [
  ["fields-example", "生成字段完整性检查组件：根据 required_fields 检查 records 中各条记录的 fields；缺少键、null、空白字符串、空列表和空字典算缺失，0 和 false 有效。保持顺序并返回逐记录和整体检查结果。"],
  ["references-example", "生成来源引用检查组件：根据文档 sources 和提取字段 fields 的 references，核对 source_id、segment_id、Unicode 字符范围与原文 quote。按规范返回各引用、各字段和整体状态；不进行语义事实核查。"]
]) $(id)?.addEventListener("click", () => { $("mode").value = "auto"; $("requirement-profile").value = ""; $("description").value = description; $("description").dispatchEvent(new Event("input")); });

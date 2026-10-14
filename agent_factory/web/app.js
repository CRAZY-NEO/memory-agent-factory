"use strict";
const $ = (id) => document.getElementById(id);
const names = { queued:"排队中", running:"执行中", succeeded:"已完成", failed:"失败", passed:"通过", failed_validation:"验证未通过", failed_test:"最终测试未通过", prepare:"准备材料", understand:"理解需求", retrieve:"检索能力知识", plan:"规划候选", generate:"生成 / 修复代码", validate:"自动验证", review:"诊断问题", advance:"切换候选", select:"选择并冻结版本", test:"独立最终测试", persist:"报告与知识回写" };
Object.assign(names, { route:"判断能力与执行路径", source_search:"理解新能力与搜索规划", github_search:"搜索 GitHub", pick_repository:"选择来源仓库", download_repository:"下载固定提交源码", extract_repository:"AST 抽取源码", propose_algorithm:"提议新算法能力", approve_algorithm:"验证后登记算法" });
const categories = { direct:"词面基线", paraphrase:"同义改写", hard_negative:"相近负例", multi_relevance:"多相关文档" };
const metricNames = { recall_at_k:"Recall@k", mrr_at_k:"MRR@k", precision_at_k:"Precision@k", p95_ms:"P95 ms" };
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
function badge(id, status) { $(id).textContent = names[status] || status; $(id).classList.toggle("failed", status.startsWith("failed")); }
function format(value) { return typeof value === "number" ? value.toFixed(4) : "—"; }
function disconnect() { connected = false; $("mode").disabled = true; $("token").hidden = false; document.querySelector('label[for="token"]').hidden = false; $("connect").hidden = false; accessToken = ""; $("token").value = ""; clearTimeout(timer); selection++; selectedJob = null; $("submit").disabled = true; $("refresh").disabled = true; $("disconnect").hidden = true; $("connect").disabled = false; $("connection").textContent = "未连接"; $("result").hidden = true; $("jobs").replaceChildren(node("li", "连接后查看。", "muted")); $("runs").replaceChildren(node("li", "连接后查看。", "muted")); $("events").replaceChildren(node("li", "提交需求后查看执行阶段。", "empty")); $("job-caption").textContent = "提交需求后，查看实际执行阶段与修复轮次。"; badge("job-status", "等待提交"); }
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
async function download(path, filename) {
  try { const data = await request(path, {}, true); const url = URL.createObjectURL(data); const link = node("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  catch (error) { notice(error.message); }
}
function downloadButton(label, path, filename, small = false) { const button = node("button", label, small ? "text-button" : "secondary"); button.type = "button"; button.addEventListener("click", () => download(path, filename)); return button; }
async function refreshHistory() {
  const [jobs, runs] = await Promise.all([request("/v1/jobs?limit=12"), request("/v1/runs?limit=20")]);
  $("jobs").replaceChildren();
  for (const job of jobs.jobs) { const item = node("li"); const kind = { factory:"算法生成", discovery:"发现新算法", ingest:"能力抽取", search:"检索试用" }[job.kind] || job.kind; const button = node("button", `${names[job.status] || job.status} · ${kind} · ${job.id.slice(0, 8)}`, "history-button"); button.type = "button"; button.addEventListener("click", () => selectJob(job.id)); item.append(button); $("jobs").append(item); }
  if (!jobs.jobs.length) $("jobs").append(node("li", "还没有任务。", "muted"));
  $("runs").replaceChildren();
  for (const run of runs.runs) { const item = node("li"); const button = node("button", `${names[run.status] || run.status} · ${run.algorithm || "未发布"}`, "history-button"); button.type = "button"; button.append(node("small", `${run.run_id} · ${run.provider} / ${run.model}`)); button.addEventListener("click", () => selectRun(run.run_id)); item.append(button); $("runs").append(item); }
  if (!runs.runs.length) $("runs").append(node("li", "还没有完成的运行。", "muted"));
}
function renderJob(job) {
  badge("job-status", job.status);
  const stage = job.progress?.stage;
  $("job-caption").textContent = `任务 ${job.id.slice(0, 8)} · ${names[stage] || stage || names[job.status]}${job.run_id ? " · " + job.run_id : ""}`;
  const list = $("events"); list.replaceChildren();
  for (const event of job.progress?.events || []) { const item = node("li", names[event.node] || event.node); const candidate = event.candidate_index === null ? "" : `候选 ${event.candidate_index + 1} · r${event.revision ?? 0} · `; item.append(node("small", candidate + new Date(event.time).toLocaleTimeString())); list.append(item); }
  if (!list.children.length) list.append(node("li", job.status === "queued" ? "已提交，等待执行。" : "正在准备能力材料。", "empty"));
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
      if (runId && ["factory", "discovery"].includes(job.kind)) { try { const report = await request(`/v1/runs/${runId}/report`); if (current === selection) renderReport(report); } catch (error) { if (current === selection) notice(`${job.error || "任务结束"}；${error.message}`); } }
      else if (job.result?.hits) { $("job-caption").textContent += " · 检索任务已完成"; }
      if (current === selection && connected) await refreshHistory();
    } else { timer = setTimeout(() => pollJob(id, current), 3000); }
  } catch (error) { if (current !== selection) return; notice(error.message); if (connected && retries < 3) timer = setTimeout(() => pollJob(id, current, retries + 1), 5000); }
}
function selectJob(id) { selection++; clearTimeout(timer); selectedJob = id; $("result").hidden = true; notice(""); pollJob(id, selection); }
async function selectRun(id) {
  const current = ++selection;
  selectedJob = null;
  clearTimeout(timer);
  $("result").hidden = true;
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
function categoryTable(title, evaluation) {
  if (!evaluation?.by_category) return null;
  const box = node("div", undefined, "table-scroll"); const table = node("table"); table.append(node("caption", title)); const head = node("thead"); const header = node("tr"); for (const label of ["查询类型", "数量", "Recall@k", "MRR@k", "Precision@k", "失败查询", "负例优先"]) header.append(node("th", label)); head.append(header); table.append(head); const body = node("tbody");
  for (const [category, group] of Object.entries(evaluation.by_category)) { const row = node("tr"); for (const value of [categories[category] || category, group.query_count, format(group.metrics.recall_at_k), format(group.metrics.mrr_at_k), format(group.metrics.precision_at_k), group.failure_count, group.hard_negative_confusion_count]) row.append(node("td", value)); body.append(row); }
  table.append(body); box.append(table); return box;
}
function renderReport(report) {
  $("result").hidden = false; badge("run-status", report.status);
  const selected = report.selected;
  $("run-caption").textContent = `${report.run_id} · ${report.provider} / ${report.model} · ${report.runner} · ${selected ? "选定 " + selected.algorithm + " r" + selected.revision : "无可发布候选"}`;
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
  for (const [key, label] of Object.entries(metricNames)) { const card = node("div", undefined, "metric"); card.append(node("span", "最终测试 · " + label), node("strong", format(report.test?.metrics?.[key]))); $("metric-cards").append(card); }
  const body = $("candidates").querySelector("tbody"); body.replaceChildren();
  for (const attempt of report.attempts) { const row = node("tr"); const metrics = attempt.validation.metrics;
    for (const value of [`${attempt.algorithm} r${attempt.revision}`, attempt.validation.passed ? "通过" : "未通过", ...Object.keys(metricNames).map((key) => format(metrics[key]))]) row.append(node("td", value));
    const info = node("td"); if (attempt.validation.errors.length) { const details = node("details"); details.append(node("summary", `${attempt.validation.errors.length} 条问题`), node("pre", attempt.validation.errors.join("\n"))); info.append(details); }
    info.append(downloadButton("下载该版本代码", `/v1/runs/${report.run_id}/code/${attempt.algorithm}/${attempt.revision}`, `${attempt.algorithm}-r${attempt.revision}.py`, true)); row.append(info); body.append(row);
  }
  $("category-analysis").replaceChildren();
  for (const [title, evaluation] of [["选定候选 · 验证集分类表现", selected?.validation], ["选定版本 · 独立最终测试分类表现", report.test]]) { const table = categoryTable(title, evaluation); if (table) $("category-analysis").append(table); }
  $("failures").replaceChildren(); let failureCount = 0;
  for (const [title, evaluation] of [["验证", selected?.validation], ["最终测试", report.test]]) {
    for (const row of evaluation?.per_query || []) { if (!row.failure_types?.length) continue; failureCount++; const item = node("div", undefined, "failure-item"); item.append(node("p", `${title} / ${row.query_id} · ${categories[row.category] || row.category} · ${row.query}`), node("pre", `类型：${row.failure_types.join(", ")}\n相关：${row.relevant_ids.join(", ")}\n漏召回：${row.missed_relevant_ids.join(", ")}\n负例优先：${JSON.stringify(row.hard_negative_confusions)}\n返回顺序：${row.predictions.map((hit) => hit.id).join(" → ")}`), node("p", row.annotation, "muted")); $("failures").append(item); }
  }
  if (!failureCount) $("failures").append(node("p", selected ? "此报告未记录逐查询失败；旧版报告可能没有此分析字段。" : "所有候选均未通过，详情见候选问题及完整报告。", "muted"));
  $("downloads").replaceChildren();
  const files = report.status === "passed" ? ["selected_retrieval.py", "plugin.json", "report.md", "report.json"] : ["report.md", "report.json"];
  const labels = { "selected_retrieval.py":"下载选定算法", "plugin.json":"下载插件清单", "report.md":"下载 Markdown 报告", "report.json":"下载 JSON 报告" };
  for (const file of files) $("downloads").append(downloadButton(labels[file], `/v1/runs/${report.run_id}/artifacts/${file}`, file));
}
async function connectWorkspace(token = "") {
  $("connect").disabled = true; notice(""); accessToken = token; connected = true;
  try {
    const settings = await request("/v1/settings"); const d = settings.dataset;
    $("mode").disabled = false;
    $("mode").querySelector('option[value="discover"]').disabled = !settings.discovery_available;
    if (!settings.discovery_available && $("mode").value === "discover") $("mode").value = "auto";
    $("routing-hint").textContent = settings.discovery_available ? "自动判断：已有能力优先；未登记的方法会自动搜索 GitHub，验证合格后登记。" : "自动判断已启用；当前为离线演示，发现未登记的方法时会提示需要 Ollama。";
    $("dataset").textContent = d ? `v${d.version} · ${d.memory_count} 条记忆 · ${d.splits.validation.query_count} 验证查询 · ${d.splits.test.query_count} 最终测试查询` : "当前工作空间未提供数据版本说明。";
    $("connection").textContent = "已连接 · " + settings.algorithms.join(" / ");
    $("token").value = ""; $("token").hidden = true; document.querySelector('label[for="token"]').hidden = true; $("connect").hidden = true;
    $("disconnect").hidden = false; $("submit").disabled = false; $("refresh").disabled = false;
    await refreshHistory();
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
$("factory-form").addEventListener("submit", async (event) => { event.preventDefault(); const description = $("description").value.trim(); if (description.length < 5) { notice("请至少输入 5 个字符的能力描述。"); return; } $("submit").disabled = true; notice(""); try { const job = await request("/v1/factory/runs", { method:"POST", body:JSON.stringify({ description, mode:$("mode").value }) }); selectJob(job.job_id); await refreshHistory(); } catch (error) { notice(error.message); } finally { $("submit").disabled = !connected; } });
$("description").addEventListener("input", () => { $("character-count").textContent = `${$("description").value.length} / 6000`; });
$("example").addEventListener("click", () => { $("description").value = "为个人技术记忆库分别复刻 TF-IDF、BM25、Jaccard 三种检索算法并比较。输入 query、memories、top_k，返回前 5 条相关记忆的 ID 与分数。保留中文单字/双字及英文词的算法规则，处理空输入，按配置验证 Recall、MRR、Precision 和稳定性；修复失败版本，选择通过验证的候选并回写知识库。"; if ($("mode").value === "discover") $("description").value = "为中文与英文混合技术笔记检索寻找当前能力目录尚未登记的 BM25Plus 方法。请自动搜索 GitHub 中的算法源码，保留其评分公式与默认参数，改造成 retrieve(query, memories, top_k=5)，在 Docker 中验证，合格后登记供以后使用。"; $("description").dispatchEvent(new Event("input")); });
$("refresh").addEventListener("click", async () => { notice(""); try { await refreshHistory(); if (selectedJob) selectJob(selectedJob); } catch (error) { notice(error.message); } });

$("new-example").addEventListener("click", () => { $("description").value = "为中文与英文混合技术笔记复刻 BM25Plus 检索方法，输入 query、memories、top_k=5，返回相关记忆 ID 和得分，通过验证后交付可复用代码。"; $("description").dispatchEvent(new Event("input")); });

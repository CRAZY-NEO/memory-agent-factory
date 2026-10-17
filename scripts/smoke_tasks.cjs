/* Actual Chrome submission, task-specific report rendering, ZIP download and standalone execution.
 * Default acceptance uses the mock provider and real Docker; no HTTP routes are intercepted.
 * FACTORY_API_TOKEN_FILE is required. Optional EXPECTED_PROVIDER / EXPECTED_RUNNER make the
 * evidence explicit when reusing this script against a real-model gateway.
 */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const baseURL = process.argv[2] || process.env.FACTORY_WEB_URL || "http://127.0.0.1:8014";
  const output = path.resolve(process.argv[3] || path.join(root, "examples/classification_web_validation.json"));
  const evidence = path.resolve(process.env.FACTORY_WEB_ARTIFACTS || path.join(root, "state/web/classification"));
  assert(process.env.FACTORY_API_TOKEN_FILE, "Set FACTORY_API_TOKEN_FILE; never put the key in command-line arguments");
  const token = (await fs.readFile(process.env.FACTORY_API_TOKEN_FILE, "utf8")).trim();
  const headers = { Authorization: "Bearer " + token };
  await fs.mkdir(evidence, { recursive: true });
  await fs.mkdir(path.dirname(output), { recursive: true });
  const record = { status: "running", started_at: new Date().toISOString(), browser: "Chrome", checks: [] };
  const save = () => fs.writeFile(output, JSON.stringify(record, null, 2) + "\n");
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 960 }, acceptDownloads: true });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    async function json(url) {
      const response = await page.request.get(url, { headers });
      assert.equal(response.status(), 200, url);
      return response.json();
    }
    async function login() {
      await page.locator("#token").fill(token);
      await page.locator("#connect").click();
      await page.locator("#component-ensure:enabled").waitFor();
      if (await page.locator("#research-tools").getAttribute("open") === null) await page.locator("#research-tools > summary").click();
    }
    await page.goto("/");
    assert.equal((await page.request.get("/v1/settings")).status(), 401);
    await login();
    const settings = await json("/v1/settings");
    assert(settings.tasks.includes("memory_classification") && settings.tasks.includes("memory_retrieval"));
    assert(settings.classification_dataset);
    await page.locator("#classification-example").click();
    assert.equal(await page.locator("#mode").inputValue(), "auto");
    const description = await page.locator("#description").inputValue();
    assert(description.includes("分类") && description.includes("training_examples"));
    const queued = page.waitForResponse((response) => response.url().endsWith("/v1/factory/runs") && response.status() === 202);
    await page.locator("#submit").click();
    const submission = await (await queued).json();
    record.job_id = submission.job_id;
    record.submitted_description = description;
    await save();
    let completed = null;
    let previous = "";
    const deadline = Date.now() + 45 * 60 * 1000;
    while (Date.now() < deadline) {
      const job = await json(submission.status_url);
      const progress = `${job.status} / ${job.progress?.stage || ""}`;
      if (progress !== previous) { console.log(progress); previous = progress; }
      if (["succeeded", "failed"].includes(job.status)) { completed = job; break; }
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    assert(completed, "Classification job timed out; inspect server history");
    assert.equal(completed.status, "succeeded", completed.error || JSON.stringify(completed.result));
    record.run_id = completed.run_id;
    const report = await json(completed.result.report_url);
    assert.equal(report.request.task, "memory_classification");
    assert.equal(report.status, "passed");
    assert.equal(report.provider, process.env.EXPECTED_PROVIDER || "mock");
    assert.equal(report.runner, process.env.EXPECTED_RUNNER || "docker");
    assert(report.test.passed && report.test.split === "test" && report.workflow_events.includes("persist"));
    assert(report.test.per_example.length && report.test.confusion_matrix.labels.length > 1);
    assert.deepEqual(new Set(report.attempts.map((row) => row.algorithm)),
      new Set(report.validation_config.algorithms));
    await page.waitForFunction((id) => !document.getElementById("result").hidden
      && document.getElementById("run-caption").textContent.includes(id), report.run_id, { timeout: 20000 });
    const metricKeys = Object.keys(report.validation_config.metrics);
    assert.equal(await page.locator("#metric-cards .metric").count(), metricKeys.length);
    const metricText = await page.locator("#metric-cards").textContent();
    const headerText = await page.locator("#candidates thead").textContent();
    for (const key of metricKeys) {
      const name = report.plugin_catalog.metrics.find((metric) => metric.id === key).name;
      assert(metricText.includes(name) && headerText.includes(name));
    }
    assert(!metricText.includes("Recall@"));
    assert((await page.locator("#category-analysis").textContent()).includes("混淆矩阵"));
    assert((await page.locator("#export-note").textContent()).includes("分类包包含训练样本"));
    assert.equal((await page.request.get(`/v1/runs/${report.run_id}/artifacts/training_examples.json`, { headers })).status(), 404);
    const codeDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载选定算法", exact: true }).click();
    const codeFile = path.join(evidence, "selected_classification.py");
    await (await codeDownload).saveAs(codeFile);
    assert.equal(crypto.createHash("sha256").update(await fs.readFile(codeFile)).digest("hex"), report.selected.code_hash);
    const zipDownload = page.waitForEvent("download");
    const exportResponse = page.waitForResponse((response) => response.url().endsWith(`/v1/runs/${report.run_id}/export`));
    await page.getByRole("button", { name: "一键导出可运行 ZIP", exact: true }).click();
    const response = await exportResponse;
    assert.equal(response.request().method(), "POST");
    assert.equal(response.status(), 200);
    const downloaded = await zipDownload;
    assert(downloaded.suggestedFilename().endsWith("-deployment.zip"));
    const zipFile = path.join(evidence, `${report.run_id}-deployment.zip`);
    await downloaded.saveAs(zipFile);
    const deployment = path.join(evidence, report.run_id + "-unpacked");
    const check = spawnSync(process.env.FACTORY_PYTHON || path.join(root, ".venv/bin/python"), ["-I", "-c", `
import hashlib, json, subprocess, sys
from pathlib import Path
from zipfile import ZipFile
archive, target, expected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
with ZipFile(archive) as bundle:
    names = bundle.namelist()
    assert all(not Path(name).is_absolute() and '..' not in Path(name).parts for name in names)
    assert not {'test.json', 'validation.json', 'access-token.txt'} & set(names)
    assert {'algorithm.py', 'run.py', 'plugin.json', 'training_examples.json', 'requirements.txt', 'Dockerfile'} <= set(names)
    manifest = json.loads(bundle.read('plugin.json'))
    assert manifest['task'] == 'memory_classification' and manifest['includes_training_text']
    assert hashlib.sha256(bundle.read('algorithm.py')).hexdigest() == expected == manifest['code_hash']
    assert hashlib.sha256(bundle.read('training_examples.json')).hexdigest() == manifest['training_data_hash']
    assert '分类包包含' in bundle.read('README.md').decode()
    bundle.extractall(target)
result = subprocess.run([sys.executable, '-I', str(target / 'run.py')], cwd=target.parent,
                        text=True, capture_output=True, timeout=60, check=True)
labels = json.loads(result.stdout)
assert len(labels) == len(json.loads((target / 'example_input.json').read_text())['texts'])
print(json.dumps({'files': names, 'standalone_output': labels, 'code_hash': expected}))
`, zipFile, deployment, report.selected.code_hash], { encoding: "utf8", timeout: 90000 });
    assert.equal(check.status, 0, check.stderr || check.error?.message);
    record.bundle = JSON.parse(check.stdout);
    record.provider = report.provider;
    record.model = report.model;
    record.runner = report.runner;
    record.task = report.request.task;
    record.selected_algorithm = report.selected.algorithm;
    record.test_metrics = report.test.metrics;
    record.llm_calls = report.llm_calls;
    await page.screenshot({ path: path.join(evidence, "classification-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(evidence, "classification-mobile.png"), fullPage: true });
    await page.reload();
    await login();
    await page.locator("#runs button").filter({ hasText: report.run_id }).click();
    await page.locator("#result:not([hidden])").waitFor();
    assert((await page.locator("#run-caption").textContent()).includes("文本记忆分类"));
    assert.equal(errors.length, 0, errors.join("\n"));
    record.status = "passed";
    record.completed_at = new Date().toISOString();
    record.scope = "Actual Chrome submits a classification factory job, inspects task-specific metrics and confusion matrices, downloads a ZIP by authenticated POST, and executes its standalone runner outside the factory. Provider and execution mode are recorded explicitly; no network responses are mocked.";
    record.checks = ["authenticated API", "classification example with automatic routing", "actual Web submission and polling",
      "classification candidates and held-out final test", "dynamic metric cards and candidate columns", "confusion matrix",
      "training data export disclosure", "selected code digest", "actual POST ZIP download", "ZIP code and training hashes",
      "standalone package execution", "mobile layout", "history after reload", "no JavaScript errors"];
    await save();
    console.log(JSON.stringify(record, null, 2));
  } catch (error) {
    record.status = "failed";
    record.error = error.message;
    await save();
    throw error;
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });

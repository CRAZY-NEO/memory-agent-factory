/* Actual browser submissions to an Ollama + Docker worker; no model or network fixtures.
 * Run in a fresh workspace: BM25Plus must not already be registered.
 */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const [baseURL, outputFile] = process.argv.slice(2);
  assert(baseURL && outputFile, "usage: node scripts/smoke_automatic.cjs URL OUTPUT_JSON");
  const token = (await fs.readFile(process.env.FACTORY_API_TOKEN_FILE, "utf8")).trim();
  const headers = { Authorization: "Bearer " + token };
  const screenshots = path.resolve(__dirname, "../state/web/automatic");
  await fs.mkdir(screenshots, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  const record = { status: "running", started_at: new Date().toISOString(), jobs: [] };
  const save = () => fs.writeFile(outputFile, JSON.stringify(record, null, 2) + "\n");
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
    assert.equal(await page.locator("#mode").inputValue(), "auto");
    assert.equal(await page.locator("#discover").count(), 0);
    assert(!await page.locator(".mode-options").evaluate((element) => element.open));
    const settings = await json("/v1/settings");
    assert(settings.discovery_available, "Requires a real Ollama gateway");
    assert(!settings.algorithms.some((id) => /bm25plus/.test(id)), "Use a fresh workspace");

    async function submit(description) {
      await page.locator("#description").fill(description);
      const queued = page.waitForResponse((response) => response.url().endsWith("/v1/factory/runs") && response.status() === 202);
      await page.locator("#submit").click();
      const submission = await (await queued).json();
      const item = { job_id: submission.job_id, description, submitted_mode: "auto" };
      record.jobs.push(item);
      await save();
      const deadline = Date.now() + 45 * 60 * 1000;
      let previous = "";
      while (Date.now() < deadline) {
        const job = await json(submission.status_url);
        const progress = `${job.status} / ${job.progress?.stage || ""} / ${job.run_id || ""}`;
        if (progress !== previous) { console.log(progress); previous = progress; }
        if (["succeeded", "failed"].includes(job.status)) {
          item.status = job.status;
          item.run_id = job.run_id;
          item.result = job.result;
          await save();
          assert.equal(job.status, "succeeded", job.error || JSON.stringify(job.result));
          const report = await json(job.result.report_url);
          assert.equal(report.status, "passed");
          assert.equal(report.provider, "ollama");
          assert.equal(report.runner, "docker");
          assert(report.test.passed && report.workflow_events.includes("persist"));
          item.test_metrics = report.test.metrics;
          await page.waitForFunction((id) => !document.getElementById("result").hidden
            && document.getElementById("run-caption").textContent.includes(id), report.run_id, { timeout: 15000 });
          return report;
        }
        await new Promise((resolve) => setTimeout(resolve, 5000));
      }
      throw new Error("Timed out waiting for the submitted job; it remains visible in server history.");
    }
    const comparison = await submit("比较 TF-IDF 与 BM25Plus，用于中文与英文混合技术记忆检索。输入 query、memories、top_k=5，返回相关记忆 ID 和分数；保留各方法的评分规则与默认参数，验证接口、稳定性及检索指标，通过后沉淀为可复用插件。");
    assert.equal(comparison.routing.action, "compare_after_discovery");
    const sourceRun = comparison.routing.discovery_run_id;
    const discoveredId = comparison.routing.discovered_algorithm;
    assert.deepEqual(new Set(comparison.attempts.map((row) => row.algorithm)), new Set(["tfidf", discoveredId]));
    assert((await page.locator("#routing-evidence").textContent()).includes("发现后比较候选"));
    await page.getByRole("button", { name: "查看发现与登记报告", exact: true }).click();
    await page.waitForFunction((id) => document.getElementById("run-caption").textContent.includes(id), sourceRun);
    const discovery = await json(`/v1/runs/${sourceRun}/report`);
    assert.equal(discovery.routing.action, "discover");
    assert.equal(discovery.workflow_events[0], "route");
    assert(discovery.discovery.release && discovery.test.passed);
    const evidence = await page.locator("#discovery-evidence").textContent();
    assert(evidence.includes(discovery.discovery.source.repository) && evidence.includes(discovery.discovery.source.commit));
    const formula = await json(`/v1/runs/${sourceRun}/artifacts/source_contract.json`);
    assert.equal(formula.status, "derived_scalar_formula_cases");
    assert.equal((await page.request.get(`/v1/runs/${sourceRun}/artifacts/extracted_repository.json`, { headers })).status(), 200);
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载选定算法", exact: true }).click();
    const codePath = path.join(screenshots, "discovered_retrieval.py");
    await (await downloaded).saveAs(codePath);
    assert.equal(crypto.createHash("sha256").update(await fs.readFile(codePath)).digest("hex"), discovery.selected.code_hash);
    record.discovery = { run_id: sourceRun, algorithm: discoveredId, source: discovery.discovery.source,
      test_metrics: discovery.test.metrics, code_hash: discovery.selected.code_hash };
    await page.screenshot({ path: path.join(screenshots, "discovery-desktop.png"), fullPage: true });
    await save();

    await page.locator("#new-example").click();
    const reuse = await submit(await page.locator("#description").inputValue());
    assert.equal(reuse.routing.action, "reuse");
    assert.deepEqual(reuse.routing.registered_methods, [discoveredId]);
    assert(!reuse.workflow_events.includes("github_search"));
    assert(reuse.attempts.every((row) => row.algorithm === discoveredId));
    assert((await page.locator("#routing-evidence").textContent()).includes("使用已有能力"));
    await page.screenshot({ path: path.join(screenshots, "reuse-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(screenshots, "reuse-mobile.png"), fullPage: true });
    await page.reload();
    await login();
    await page.locator("#runs button").filter({ hasText: reuse.run_id }).click();
    await page.locator("#result:not([hidden])").waitFor();
    assert.equal(errors.length, 0, errors.join("\n"));
    record.status = "passed";
    record.completed_at = new Date().toISOString();
    record.provider = reuse.provider;
    record.model = reuse.model;
    record.runner = reuse.runner;
    record.scope = "Actual Chrome submits two auto-mode jobs; real Ollama generates, repairs if needed, and Docker validates discovery, comparison and subsequent reuse. No SSH connection is created by this check.";
    record.checks = ["default automatic mode without checkbox", "actual Web submission and polling", "new method discovery and registration",
      "source formula checks", "requested existing and new method comparison", "linked source report", "AST artifact download",
      "downloaded code SHA-256", "subsequent registered method reuse without GitHub search", "mobile layout", "history after reload", "no JavaScript errors"];
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

/* Actual browser regression against an already running gateway and worker.
 * Install Playwright in a separate dev directory; NODE_PATH can point to it.
 * This script doesn't start a worker or choose the gateway's LLM provider.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const baseURL = process.argv[2] || "http://127.0.0.1:8011";
  const token = process.env.FACTORY_API_TOKEN || (process.env.FACTORY_API_TOKEN_FILE
    ? (await fs.readFile(process.env.FACTORY_API_TOKEN_FILE, "utf8")).trim() : "");
  assert(token, "FACTORY_API_TOKEN is required");
  const root = path.resolve(__dirname, "..");
  const screenshots = path.join(root, "state", "web", "screenshots");
  await fs.mkdir(screenshots, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 }, baseURL, acceptDownloads: true });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    assert(await page.locator("#submit").isDisabled());
    assert.equal((await page.request.get("/v1/settings")).status(), 401);
    await page.locator("#token").fill("incorrect-key");
    await page.locator("#connect").click();
    await page.getByText("访问密钥无效，请重新连接。", { exact: true }).waitFor();
    await page.locator("#token").fill(token);
    await page.locator("#connect").click();
    await page.locator("#submit:enabled").waitFor();
    assert.equal(await page.locator("#token").inputValue(), "");
    assert(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0));
    await page.locator("#example").click();
    const queued = page.waitForResponse((response) => response.url().endsWith("/v1/factory/runs") && response.status() === 202);
    await page.locator("#submit").click();
    const jobId = (await (await queued).json()).job_id;
    await page.locator("#result:not([hidden])").waitFor({ timeout: 12 * 60 * 1000 });
    const headers = { Authorization: "Bearer " + token };
    const job = await (await page.request.get(`/v1/jobs/${jobId}`, { headers })).json();
    const report = await (await page.request.get(job.result.report_url, { headers })).json();
    assert.equal(job.status, "succeeded");
    assert.equal(report.status, "passed");
    assert(report.test.passed);
    assert(report.test.by_category.paraphrase);
    assert(await page.locator("#events li").count() > 5);
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载选定算法", exact: true }).click();
    const download = await downloaded;
    assert.equal(download.suggestedFilename(), "selected_retrieval.py");
    const saved = path.join(screenshots, "downloaded_retrieval.py");
    await download.saveAs(saved);
    const crypto = require("node:crypto");
    assert.equal(crypto.createHash("sha256").update(await fs.readFile(saved)).digest("hex"), report.selected.code_hash);
    // Model-produced text is displayed as text, without executing HTML or script.
    const malicious = '<img src=x onerror="window.factoryXSS=true">';
    await page.route("**/v1/runs/*/report", async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.plan.design_explanation = malicious;
      await route.fulfill({ response, json: data });
    });
    await page.locator("#runs button").first().click();
    await page.getByText(malicious, { exact: true }).waitFor();
    assert.equal(await page.locator("#design img").count(), 0);
    assert(!await page.evaluate(() => window.factoryXSS));
    await page.unroute("**/v1/runs/*/report");
    await page.locator("#runs button").first().click();
    await page.waitForFunction((text) => document.getElementById("design").textContent !== text, malicious);
    await page.screenshot({ path: path.join(screenshots, "desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(screenshots, "mobile.png"), fullPage: true });
    await page.reload();
    assert(await page.locator("#submit").isDisabled());
    await page.locator("#token").fill(token);
    await page.locator("#connect").click();
    await page.locator("#runs button").first().waitFor();
    await page.locator("#runs button").first().click();
    await page.locator("#result:not([hidden])").waitFor();
    assert.equal(errors.length, 0, errors.join("\n"));
    const result = { status: "passed", job_id: jobId, run_id: report.run_id, provider: report.provider,
      model: report.model, runner: report.runner, test_metrics: report.test.metrics,
      checks: ["unauthorized rejected", "submit and poll real HTTP task", "stage events", "candidate and category report", "download SHA-256", "untrusted HTML rendered as text", "mobile no page overflow", "session token not persisted", "history after reload", "no page JavaScript errors"] };
    await fs.writeFile(path.join(root, "examples", "web_validation.json"), JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });

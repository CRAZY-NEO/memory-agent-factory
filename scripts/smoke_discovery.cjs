/* Browser/HTTP checks for an actual completed discovery run; no model/network stubs. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const [baseURL, runId] = process.argv.slice(2);
  assert(baseURL && runId, "usage: node scripts/smoke_discovery.cjs URL RUN_ID");
  const token = (await fs.readFile(process.env.FACTORY_API_TOKEN_FILE, "utf8")).trim();
  const root = path.resolve(__dirname, "..");
  const output = path.join(root, "state", "web", "discovery");
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 960 }, acceptDownloads: true });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    assert.equal((await page.request.get("/v1/settings")).status(), 401);
    await page.locator("#token").fill(token);
    await page.locator("#connect").click();
    await page.locator("#component-ensure:enabled").waitFor();
    if (await page.locator("#research-tools").getAttribute("open") === null) await page.locator("#research-tools > summary").click();
    assert.equal(await page.locator("#mode").inputValue(), "auto");
    await page.locator("#new-example").click();
    assert((await page.locator("#description").inputValue()).includes("BM25Plus"));
    const headers = { Authorization: "Bearer " + token };
    const report = await (await page.request.get(`/v1/runs/${runId}/report`, { headers })).json();
    assert.equal(report.provider, "ollama");
    assert.equal(report.runner, "docker");
    assert.equal(report.status, "passed");
    assert(report.discovery.release);
    await page.locator("#runs button").filter({ hasText: runId }).click();
    await page.locator("#result:not([hidden])").waitFor();
    assert((await page.locator("#discovery-evidence").textContent()).includes(report.discovery.source.repository));
    assert((await page.locator("#discovery-evidence").textContent()).includes(report.discovery.source.commit));
    const downloaded = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载选定算法", exact: true }).click();
    const codePath = path.join(output, "selected_retrieval.py");
    await (await downloaded).saveAs(codePath);
    assert.equal(crypto.createHash("sha256").update(await fs.readFile(codePath)).digest("hex"), report.selected.code_hash);
    const extracted = await page.request.get(`/v1/runs/${runId}/artifacts/extracted_repository.json`, { headers });
    assert.equal(extracted.status(), 200);
    const formula = await page.request.get(`/v1/runs/${runId}/artifacts/source_contract.json`, { headers });
    assert.equal(formula.status(), 200);
    assert.equal((await formula.json()).status, 'derived_scalar_formula_cases');
    await page.screenshot({ path: path.join(output, "desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
    assert.equal(errors.length, 0, errors.join("\n"));
    const result = { status: "passed", run_id: runId, provider: report.provider, runner: report.runner,
      test_metrics: report.test.metrics, source: report.discovery.source, registered_algorithm: report.discovery.release.id,
      scope: "Browser views a completed actual CLI discovery; no new discovery job is submitted by this smoke script.",
      checks: ["unauthorized report access rejected", "discovery enabled in real mode", "example selection", "actual report and source provenance", "downloaded code SHA-256", "AST JSON download", "source formula contract download", "mobile layout", "no JavaScript errors"] };
    await fs.writeFile(path.join(root, "examples", "github_discovery_web_validation.json"), JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify(result, null, 2));
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });

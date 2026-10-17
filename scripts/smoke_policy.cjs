/* Real Chrome checks for generation/history/business-contract controls.
 * Runs against an independently started mock + Docker server. No HTTP mocking.
 * Output and screenshots MUST be explicitly located outside the repository.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const root = path.resolve(__dirname, "..");
  const baseURL = process.argv[2] || "http://127.0.0.1:8014";
  assert(process.argv[3], "Provide an external JSON record path");
  const output = path.resolve(process.argv[3]);
  assert(!output.startsWith(root + path.sep), "Policy acceptance artifacts belong outside the repository");
  const artifacts = path.join(path.dirname(output), "policy-screenshots");
  assert(process.env.FACTORY_API_TOKEN_FILE, "Set FACTORY_API_TOKEN_FILE, never print the token");
  const token = (await fs.readFile(process.env.FACTORY_API_TOKEN_FILE, "utf8")).trim();
  const headers = { Authorization: "Bearer " + token };
  await fs.mkdir(artifacts, { recursive: true });
  const record = { status: "running", started_at: new Date().toISOString(), browser: "Chrome",
    provider: "mock", runner: "docker", cases: [], screenshots: [], checks: [] };
  const save = () => fs.writeFile(output, JSON.stringify(record, null, 2) + "\n");
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  try {
    const page = await browser.newPage({ baseURL, viewport: { width: 1280, height: 960 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("/");
    await page.locator("#token").fill(token);
    await page.locator("#connect").click();
    await page.locator("#component-ensure:enabled").waitFor();
    if (await page.locator("#research-tools").getAttribute("open") === null) await page.locator("#research-tools > summary").click();
    await page.locator(".mode-options summary").click();
    assert.equal(await page.locator("#generation-mode").inputValue(), "spec");
    assert.equal(await page.locator("#history-mode").inputValue(), "on");
    const required = ["classification_ignore_disabled", "classification_deduplicate_training"];
    for (const scenario of [
      { name: "reference-base", mode: "reference", profile: "", status: "passed" },
      { name: "spec-business", mode: "spec", profile: "classification", status: "failed_validation" },
      { name: "reference-business", mode: "reference", profile: "classification", status: "failed_validation" },
    ]) {
      await page.locator("#classification-example").click();
      await page.locator("#generation-mode").selectOption(scenario.mode);
      await page.locator("#history-mode").selectOption("off");
      await page.locator("#requirement-profile").selectOption(scenario.profile);
      const queued = page.waitForResponse(response => response.url().endsWith("/v1/factory/runs")
        && response.request().method() === "POST");
      await page.locator("#submit").click();
      const response = await queued;
      assert.equal(response.status(), 202);
      const payload = response.request().postDataJSON();
      assert.equal(payload.generation_mode, scenario.mode);
      assert.equal(payload.history_mode, "off");
      assert.deepEqual(payload.requirements, scenario.profile ? required : []);
      const submission = await response.json();
      let job;
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        const status = await page.request.get(submission.status_url, { headers });
        assert.equal(status.status(), 200);
        job = await status.json();
        if (["succeeded", "failed"].includes(job.status)) break;
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
      assert(job && ["succeeded", "failed"].includes(job.status), "Timed out waiting for actual worker");
      assert.equal(job.status, scenario.profile ? "failed" : "succeeded", job.error || "worker error");
      const reportResponse = await page.request.get(job.result.report_url, { headers });
      assert.equal(reportResponse.status(), 200);
      const report = await reportResponse.json();
      assert.equal(report.provider, "mock");
      assert.equal(report.runner, "docker");
      assert.equal(report.status, scenario.status);
      assert.equal(report.generation_mode, scenario.mode);
      assert.equal(report.history_mode, "off");
      assert.deepEqual(report.requirements, payload.requirements);
      assert.deepEqual(report.history_lesson_ids, []);
      assert(report.generation_backend.includes("mock"), "Do not label a mock template as independent LLM code");
      await page.waitForFunction(id => !document.getElementById("result").hidden
        && document.getElementById("run-caption").textContent.includes(id), report.run_id, { timeout: 20000 });
      const caption = await page.locator("#run-caption").textContent();
      assert(caption.includes(`生成：${scenario.mode}`) && caption.includes("历史经验：off"));
      const item = { scenario: scenario.name, submitted: payload, job_id: submission.job_id,
        run_id: report.run_id, job_status: job.status, report_status: report.status,
        generation_backend: report.generation_backend, attempts: report.attempts.length,
        failed_requirement_checks: [], selected_algorithm: report.selected?.algorithm || null };
      if (scenario.profile) {
        assert.equal(report.selected, null);
        assert.equal(report.test, null);
        for (const attempt of report.attempts) {
          assert.equal(attempt.validation.passed, false);
          assert.equal(attempt.validation.requirement_checks.passed, false);
          for (const requirement of required) {
            assert(attempt.validation.requirement_checks.errors.some(error => error.startsWith(requirement + ":")));
          }
          item.failed_requirement_checks.push(...attempt.validation.requirement_checks.errors);
        }
        assert(caption.includes("无可发布候选") && caption.includes(required[0]));
        assert.equal(await page.getByRole("button", { name: "一键导出可运行 ZIP", exact: true }).count(), 0);
        assert.equal(await page.getByRole("button", { name: "下载选定算法", exact: true }).count(), 0);
        assert.equal((await page.request.post(`/v1/runs/${report.run_id}/export`, { headers })).status(), 409);
        assert.equal((await page.request.get(`/v1/runs/${report.run_id}/artifacts/plugin.json`, { headers })).status(), 404);
      } else {
        assert(report.selected && report.test.passed);
        assert.equal(await page.getByRole("button", { name: "一键导出可运行 ZIP", exact: true }).count(), 1);
      }
      for (const viewport of [{ width: 1280, height: 960 }, { width: 390, height: 844 }]) {
        // A fresh mobile page avoids Chrome compositor tiling artifacts after a
        // desktop-to-mobile resize during a full-height screenshot.
        const capture = viewport.width === 390 ? await browser.newPage({ baseURL, viewport }) : page;
        if (capture !== page) {
          capture.on("pageerror", error => errors.push(error.message));
          await capture.goto("/");
          await capture.locator("#token").fill(token);
          await capture.locator("#connect").click();
          await capture.locator("#component-ensure:enabled").waitFor();
          if (await capture.locator("#research-tools").getAttribute("open") === null) await capture.locator("#research-tools > summary").click();
          await capture.locator("#classification-example").click();
          await capture.locator(".mode-options summary").click();
          await capture.locator("#generation-mode").selectOption(scenario.mode);
          await capture.locator("#history-mode").selectOption("off");
          await capture.locator("#requirement-profile").selectOption(scenario.profile);
          await capture.locator("#runs button").filter({ hasText: report.run_id }).click();
          await capture.locator("#result:not([hidden])").waitFor();
        }
        await capture.evaluate(() => document.fonts.ready);
        assert(await capture.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Horizontal page overflow");
        const file = path.join(artifacts, `${scenario.name}-${viewport.width}.png`);
        await capture.screenshot({ path: file, fullPage: true });
        record.screenshots.push(file);
        if (capture !== page) await capture.close();
      }
      record.cases.push(item);
      await save();
      console.log(scenario.name + ": " + report.status);
    }
    assert.deepEqual(errors, []);
    record.status = "passed";
    record.completed_at = new Date().toISOString();
    record.checks = ["real advanced select controls", "actual POST payload", "spec/reference policies persisted",
      "history off has no selected lessons", "mock backend explicitly labelled", "untouched templates fail new requirements",
      "failed algorithms remain unpublished", "failed ZIP export refused", "desktop/mobile no horizontal overflow",
      "no JavaScript errors", "no HTTP mocking"];
    record.scope = "Actual Chrome, mock provider and real Docker. Successful job execution is distinguished from algorithm validation failure. No claim about LLM generation quality.";
    await save();
    console.log(JSON.stringify({ status: record.status, output, runs: record.cases.map(item => item.run_id) }, null, 2));
  } catch (error) {
    record.status = "failed";
    record.error = error.message;
    await save();
    throw error;
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });

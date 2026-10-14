/* Actual browser check of a launcher's captured opening link; no SSH is started here. */
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const { chromium } = require("playwright");

async function main() {
  const [linkFile, outputFile] = process.argv.slice(2);
  assert(linkFile && outputFile, "Provide a private opening-link file and output JSON path");
  const link = (await fs.readFile(linkFile, "utf8")).trim();
  const url = new URL(link);
  assert.equal(url.hostname, "127.0.0.1");
  assert(new URLSearchParams(url.hash.slice(1)).get("login"));
  const browser = await chromium.launch({ headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const unauthenticated = await browser.newContext();
    assert.equal((await unauthenticated.request.get(url.origin + "/v1/settings")).status(), 401);
    await page.goto(link);
    await page.locator("#submit:enabled").waitFor();
    assert.equal(new URL(page.url()).hash, "");
    assert(await page.locator("#token").isHidden());
    assert.equal(await page.locator("#token").inputValue(), "");
    assert(await page.evaluate(() => localStorage.length === 0 && sessionStorage.length === 0));
    const cookie = (await page.context().cookies()).find((item) => item.name === "factory_demo_session");
    assert(cookie.httpOnly && cookie.sameSite === "Strict" && cookie.expires === -1);
    await page.reload();
    await page.locator("#submit:enabled").waitFor();
    // A cookie-authenticated write is exercised by a real factory submission.
    await page.locator("#example").click();
    const queued = page.waitForResponse((response) => response.url().endsWith("/v1/factory/runs") && response.status() === 202);
    await page.locator("#submit").click();
    const jobId = (await (await queued).json()).job_id;
    await page.locator("#result:not([hidden])").waitFor({ timeout:180000 });
    const job = await (await page.request.get(url.origin + `/v1/jobs/${jobId}`)).json();
    assert.equal(job.status, "succeeded");
    const report = await (await page.request.get(url.origin + job.result.report_url)).json();
    assert.equal(report.status, "passed");
    assert.equal(report.provider, "mock");
    assert.equal(report.runner, "docker");
    assert(report.test.passed && report.workflow_events.includes("persist"));
    await page.locator("#disconnect").click();
    await page.locator("#connect:not([hidden])").waitFor();
    assert.equal((await page.request.get(url.origin + "/v1/settings")).status(), 401);
    await page.reload();
    assert(await page.locator("#submit").isDisabled());
    await page.goto(link);
    await page.getByText("自动登录链接已失效，请重新启动网页或使用备用访问密钥连接。", { exact:true }).waitFor();
    assert(await page.locator("#submit").isDisabled());
    assert.equal(errors.length, 0, errors.join("\n"));
    const result = { status:"passed", provider:report.provider, runner:report.runner, run_id:report.run_id,
      test_metrics:report.test.metrics,
      checks:["automatic browser login", "opening ticket removed from URL", "HttpOnly session cookie",
        "reload remains connected", "no bearer key in input or browser storage", "unauthorized requests rejected",
        "cookie-authenticated Docker factory workflow", "logout revokes session", "opening link cannot be replayed",
        "no browser JavaScript errors"],
      note:"The launcher browser callback was captured to a private file for headless Chrome; no SSH connection was created." };
    await fs.writeFile(outputFile, JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify(result, null, 2));
    await unauthenticated.close();
  } finally { await browser.close(); }
}
main().catch((error) => { console.error(error); process.exitCode = 1; });

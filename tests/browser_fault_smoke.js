"use strict";

// Optional Playwright smoke test. All device APIs are simulated or blocked.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {spawn} = require("node:child_process");
const {once} = require("node:events");
const {chromium} = require("playwright");
const root = path.resolve(__dirname, "..");
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "mctivity-browser-"));
const screenshots = process.env.MCTIVITY_SCREENSHOT_DIR;
const fixture = [
  "import mctivity_hmi as app",
  "def forbidden(*args, **kwargs):",
  "    raise RuntimeError('Hardware access is forbidden in the browser fixture')",
  "app.motiond_command = forbidden",
  "app._write_sdo = forbidden",
  "app._read_sdo = forbidden",
  "app.feature_dispatch_axis_command = forbidden",
  "server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)",
  "print(server.server_address[1], flush=True)",
  "server.serve_forever()"
].join("\n");
const server = spawn(process.env.PYTHON || "python3", ["-u", "-c", fixture], {
  cwd: path.join(root, "mctivity_hmi"),
  env: {...process.env, PYTHONDONTWRITEBYTECODE: "1", MCTIVITY_PROFILE: "full",
    MCTIVITY_API_TOKEN: "", MCTIVITY_WEB_HOST: "127.0.0.1",
    MCTIVITY_UI_STATE_PATH: path.join(temporary, "state.json")},
  stdio: ["ignore", "pipe", "pipe"]
});
let serverOutput = "";
let serverError = "";
server.stdout.on("data", data => { serverOutput += data; });
server.stderr.on("data", data => { serverError += data; });
async function ready() {
  for (let attempt = 0; attempt < 100; attempt++) {
    const match = serverOutput.match(/^(\d+)$/m);
    if (match) return "http://127.0.0.1:" + match[1];
    if (server.exitCode !== null) throw Error(serverError || "Fixture exited");
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error("Fixture startup timed out");
}
let browser;
(async () => {
  const base = await ready();
  browser = await chromium.launch({
    headless: true,
    ...(process.env.MCTIVITY_BROWSER_CHANNEL ? {channel: process.env.MCTIVITY_BROWSER_CHANNEL} : {})
  });
  for (const viewport of [{width: 1920, height: 1080}, {width: 390, height: 844}]) {
    const context = await browser.newContext({viewport});
    const page = await context.newPage();
    const errors = [];
    const failedResources = [];
    const commands = [];
    const unexpected = [];
    let statusTemplate;
    let fault = true;
    let raw = 0xffff;
    page.on("pageerror", error => errors.push(error.message));
    page.on("response", response => {
      if (response.status() >= 400) failedResources.push(response.url());
    });
    page.on("requestfailed", request => failedResources.push(request.url()));
    function status(device) {
      const result = {...statusTemplate, device, fault, err: raw, mock_fault: false};
      if (device === "aux_encoder") {
        Object.assign(result, {position_raw: 0, counts_per_rev: 65536,
          alarm_status: fault ? raw : 0, warning_status: 0, operational: true, wc_complete: true});
      }
      return {ok: true, status: result};
    }
    await page.route("**/api/**", async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === "/api/capabilities" || url.pathname === "/api/health/modular") {
        return route.continue();
      }
      let body;
      if (url.pathname === "/api/ui_state") body = {ok: true, state: {}};
      else if (url.pathname === "/api/status") body = status(url.searchParams.get("device") || "mctivity");
      else if (url.pathname === "/api/command") {
        const payload = request.postDataJSON();
        if (payload.cmd !== "status") commands.push(payload);
        body = status(payload.device);
      } else {
        unexpected.push(url.pathname);
        body = {ok: false, error: "unexpected_test_endpoint"};
      }
      return route.fulfill({contentType: "application/json", body: JSON.stringify(body)});
    });
    await page.goto(base + "/?mock_fault=0xFFFF", {waitUntil: "networkidle"});
    await page.waitForFunction(() => capabilityState.loaded && currentStatus() && currentStatus().fault);
    assert.equal(await page.locator("#faultCodeText").innerText(), "0xFFFF");
    assert.equal(await page.locator("#faultDetailButton").count(), 0);
    assert.equal(await page.locator('[class*="servo-diagnostic"]').count(), 0);
    assert(!await page.locator("#diagModal").evaluate(element => element.classList.contains("open")));
    assert(await page.locator("#faultIndicator").evaluate(element => element.classList.contains("fault-on")));
    await page.evaluate(async () => {
      await api({cmd: "enable"});
      await api({cmd: "fault_reset"});
      await apiForDevice("fv3", {cmd: "enable"});
    });
    assert.equal(commands.length, 0, "Mock mode sent a control command");
    statusTemplate = await page.evaluate(() => mockStatus("mctivity").status);
    await page.evaluate(() => { mockFaultCode = null; refreshMockFaultPanel(); });

    for (const [device, button] of [["mctivity", "#tabMonitorBtn"], ["fv3", "#tabConfigBtn"], ["aux_encoder", "#tabEncoderBtn"]]) {
      await page.locator(button).click();
      await page.waitForFunction(expected => activeDevice === expected, device);
      await page.evaluate(() => api({cmd: "status"}));
      assert.equal(await page.locator("#faultCodeText").innerText(), "0xFFFF");
      const reset = page.locator("#faultIndicatorButton");
      if (device === "aux_encoder") {
        assert(await reset.isDisabled());
      } else {
        assert(await reset.isEnabled());
        const before = commands.length;
        await reset.click();
        await page.waitForFunction(() => currentStatus() !== null);
        await new Promise(resolve => setTimeout(resolve, 100));
        assert.equal(commands.length, before + 1, "Manual reset was not sent exactly once");
        assert.equal(commands.at(-1).cmd, "fault_reset");
        assert.equal(commands.at(-1).device, device);
      }
    }
    assert.equal(commands.length, 2, "Unexpected command after device switching");
    await page.locator("#tabMonitorBtn").click();
    for (const code of [1, 0x1234, 0]) {
      raw = code;
      fault = true;
      await page.evaluate(() => api({cmd: "status"}));
      assert.equal(await page.locator("#faultCodeText").innerText(), "0x" + code.toString(16).toUpperCase().padStart(4, "0"));
      assert(await page.locator("#faultIndicator").evaluate(element => element.classList.contains("fault-on")));
    }
    if (screenshots) {
      fs.mkdirSync(screenshots, {recursive: true});
      await page.locator("#faultIndicator").scrollIntoViewIfNeeded();
      await page.screenshot({path: path.join(screenshots, "fault-" + viewport.width + ".png"), fullPage: true});
    }
    const bounds = await page.evaluate(() => {
      const panel = document.getElementById("faultIndicator").getBoundingClientRect();
      const code = document.getElementById("faultCodeText").getBoundingClientRect();
      const reset = document.getElementById("faultIndicatorButton").getBoundingClientRect();
      return {inside: code.left >= panel.left && code.right <= panel.right && code.top >= panel.top && code.bottom <= panel.bottom,
        separated: code.right <= reset.left || code.bottom <= reset.top};
    });
    assert(bounds.inside && bounds.separated, "Raw code overlaps or escapes the status panel");
    raw = 0;
    fault = false;
    await page.evaluate(() => api({cmd: "status"}));
    assert(await page.locator("#faultIndicatorButton").isDisabled());
    assert(!await page.locator("#faultIndicator").evaluate(element => element.classList.contains("fault-on")));
    assert.equal(commands.length, 2);
    await page.evaluate(() => showApiError({ok: false, error: "unauthorized"}));
    assert(await page.locator("#diagModal").evaluate(element => element.classList.contains("open")));
    await page.evaluate(() => closeDiagModal());
    assert.deepEqual(errors, []);
    assert.deepEqual(failedResources, []);
    assert.deepEqual(unexpected, []);
    console.log("browser raw status/reset smoke passed at " + viewport.width + "x" + viewport.height);
    await context.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
}).finally(async () => {
  if (browser) await browser.close();
  if (server.exitCode === null) {
    const exited = once(server, "exit");
    server.kill("SIGTERM");
    await exited;
  }
  fs.rmSync(temporary, {recursive: true, force: true});
});

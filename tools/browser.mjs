// A tiny headless-Chrome driver over the DevTools protocol: no packages to install.
// Needs Node 26 and Chrome or Chromium (set CHROME_BIN if it isn't in a usual place).
//
//   const page = await launch("http://127.0.0.1:8765/");
//   await page.eval(`document.title`);          // run script in the page, get the value back
//   await page.waitFor(`!!document.querySelector("#board polygon")`);
//   await page.screenshot("/tmp/board.png");
//   page.errors                                  // uncaught exceptions and console.error lines
//   await page.close();

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CANDIDATES = [
  process.env.CHROME_BIN,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
];

export function findChrome() {
  return CANDIDATES.find((p) => p && existsSync(p)) || null;
}

export async function launch(url, { width = 1440, height = 900, mobile = false } = {}) {
  const bin = findChrome();
  if (!bin) throw new Error("Chrome not found (set CHROME_BIN)");
  const profile = mkdtempSync(join(tmpdir(), "hexfield-chrome-"));
  const proc = spawn(bin, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
    `--window-size=${width},${height}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = "";
    const timer = setTimeout(() => reject(new Error("Chrome did not start")), 20000);
    proc.stderr.on("data", (d) => {
      buf += d;
      const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
      if (m) { clearTimeout(timer); resolve(m[1]); }
    });
    proc.on("exit", () => reject(new Error("Chrome exited: " + buf.slice(-400))));
  });
  const ws = new WebSocket(wsUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let next = 1;
  const waiting = new Map();
  const errors = [];
  const logs = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && waiting.has(msg.id)) {
      const { resolve, reject } = waiting.get(msg.id);
      waiting.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
    } else if (msg.method === "Runtime.exceptionThrown") {
      const d = msg.params.exceptionDetails;
      errors.push(`${d.exception?.description || d.text} @${d.url || ""}:${d.lineNumber}`);
    } else if (msg.method === "Runtime.consoleAPICalled") {
      const text = msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ");
      (msg.params.type === "error" ? errors : logs).push(text);
    } else if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
      errors.push(`${msg.params.entry.text} ${msg.params.entry.url || ""}`);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = next++;
    waiting.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  const cdp = (method, params) => send(method, params, sessionId);
  await cdp("Page.enable");
  await cdp("Runtime.enable");
  await cdp("Log.enable");
  await cdp("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 2 : 1, mobile });
  if (mobile) await cdp("Emulation.setTouchEmulationEnabled", { enabled: true });
  const page = {
    errors, logs, cdp,
    async goto(u) { await cdp("Page.navigate", { url: u }); await page.waitFor(`document.readyState === "complete"`); },
    async eval(expression) {
      const r = await cdp("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
      if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
      return r.result.value;
    },
    async waitFor(expression, ms = 15000, what = expression) {
      const t0 = Date.now();
      for (;;) {
        let ok = false;
        try { ok = await page.eval(expression); } catch { /* page still loading */ }
        if (ok) return ok;
        if (Date.now() - t0 > ms) throw new Error(`timed out waiting for: ${what}\nerrors: ${errors.join(" | ")}`);
        await new Promise((r) => setTimeout(r, 60));
      }
    },
    async key(key, { shift = false } = {}) {
      const code = /^[a-z]$/i.test(key) ? "Key" + key.toUpperCase() : /^\d$/.test(key) ? "Digit" + key : key === " " ? "Space" : key;
      const base = { key, code, modifiers: shift ? 8 : 0, windowsVirtualKeyCode: key.length === 1 ? key.toUpperCase().charCodeAt(0) : { Enter: 13, Escape: 27, Backspace: 8 }[key] || 0 };
      await cdp("Input.dispatchKeyEvent", { type: "keyDown", ...base, text: key.length === 1 ? key : undefined });
      await cdp("Input.dispatchKeyEvent", { type: "keyUp", ...base });
    },
    async screenshot(path) {
      const { data } = await cdp("Page.captureScreenshot", { format: "png" });
      writeFileSync(path, Buffer.from(data, "base64"));
    },
    async close() {
      try { ws.close(); } catch { /* already gone */ }
      proc.kill();
      await new Promise((r) => setTimeout(r, 150));
      try { rmSync(profile, { recursive: true, force: true }); } catch { /* chrome still letting go */ }
    },
  };
  if (url) await page.goto(url);
  return page;
}

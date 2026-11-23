// A tiny headless-Chrome driver over the DevTools protocol: no packages to install.
// Needs Node 26 and Chrome or Chromium (set CHROME_BIN if it isn't in a usual place).
//
//   const page = await launch("http://127.0.0.1:8765/");
//   await page.eval(`document.title`);          // run script in the page, get the value back
//   await page.waitFor(`!!document.querySelector("#board polygon")`);
//   await page.screenshot("/tmp/board.png");
//   page.errors                                  // uncaught exceptions and console.error lines
//   await page.close();

import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
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

// Browsers still running, stopped when the process exits or a signal ends it.
const live = new Set();

function stop(browser) {
  live.delete(browser);
  browser.proc.kill("SIGKILL");
  try { rmSync(browser.profile, { recursive: true, force: true, maxRetries: 3 }); } catch { /* temp dir */ }
}

process.on("exit", () => live.forEach(stop));
const OURS = Symbol.for("hexfield.browser"); // global, so copies of this module don't defer to each other
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  const onSignal = () => {
    // A script that handles the signal itself decides when to stop.
    if (process.listeners(sig).some((l) => !l[OURS])) return;
    live.forEach(stop);
    process.removeListener(sig, onSignal);
    process.kill(process.pid, sig); // die of it, as without a handler
  };
  onSignal[OURS] = true;
  process.prependListener(sig, onSignal); // runs before a script's once() handler removes itself
}

// Linux: the kernel kills Chrome even if this process dies by SIGKILL. util-linux < 2.33 lacks it.
// setpriv execs Chrome, so proc is still Chrome.
let setpriv;
function findSetpriv() {
  const works = (p) => existsSync(p) && spawnSync(p, ["--pdeathsig", "KILL", "--version"]).status === 0;
  if (setpriv === undefined) setpriv = process.platform === "linux" && ["/usr/bin/setpriv", "/bin/setpriv"].find(works) || null;
  return setpriv;
}

export async function launch(url, { width = 1440, height = 900, mobile = false } = {}) {
  const bin = findChrome();
  if (!bin) throw new Error("Chrome not found (set CHROME_BIN)");
  const profile = mkdtempSync(join(tmpdir(), "hexfield-chrome-"));
  const args = ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
    `--window-size=${width},${height}`, "about:blank"];
  const sp = findSetpriv();
  const [cmd, ...argv] = sp ? [sp, "--pdeathsig", "KILL", bin, ...args] : [bin, ...args];
  const proc = spawn(cmd, argv, { stdio: ["ignore", "ignore", "pipe"] });
  const browser = { proc, profile };
  live.add(browser);
  proc.on("exit", () => stop(browser));
  try {
    return await connect(browser, url, { width, height, mobile });
  } catch (e) {
    stop(browser);
    throw e;
  }
}

async function connect(browser, url, { width, height, mobile }) {
  const { proc } = browser;
  let timer;
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = "";
    timer = setTimeout(() => reject(new Error("Chrome did not start")), 20000);
    const onData = (d) => {
      buf += d;
      const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
      if (!m) return;
      proc.stderr.off("data", onData);
      resolve(m[1]);
    };
    proc.stderr.on("data", onData);
    proc.on("error", reject);
    proc.on("exit", () => reject(new Error("Chrome exited: " + buf.slice(-400))));
  }).finally(() => clearTimeout(timer));
  const ws = new WebSocket(wsUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let next = 1;
  const waiting = new Map();
  const closed = () => new Error("Chrome closed the DevTools connection");
  ws.onclose = () => {
    for (const { reject } of waiting.values()) reject(closed());
    waiting.clear();
  };
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
    if (ws.readyState !== WebSocket.OPEN) return reject(closed()); // a closed socket drops sends silently
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
        try { ok = await page.eval(expression); } catch (e) { if (ws.readyState !== WebSocket.OPEN) throw e; }
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
      ws.close();
      const exited = proc.exitCode === null && proc.signalCode === null && once(proc, "exit");
      stop(browser);
      await exited;
    },
  };
  if (url) await page.goto(url);
  return page;
}

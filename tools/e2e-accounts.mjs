// End-to-end check of accounts in headless Chrome, with its virtual passkey authenticator.
//
//   HEXFIELD_HOME=$(mktemp -d) python3 -m hexfield.web --accounts --host-key <key> --no-browser &
//   node tools/e2e-accounts.mjs http://localhost:8765/ <key>
//
// Ada signs up and can't host until the admin allows it; then she starts an online game,
// Bob signs up from her invite link and joins under his own name, signs out and back in.
// The address must be localhost (a passkey can't belong to an IP address).
// HEXFIELD_BROWSER_TESTS=1 python3 -m unittest tests.test_js runs it for you.

import { launch } from "./browser.mjs";

const URL = process.argv[2] || "http://localhost:8765/";
const KEY = process.argv[3];
let step = "start";
const pages = [];

async function browser() {
  const p = await launch(URL);
  pages.push(p);
  await p.cdp("WebAuthn.enable", { enableUI: false });
  await p.cdp("WebAuthn.addVirtualAuthenticator", { options: { protocol: "ctap2", transport: "internal",
    hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  await p.goto(URL);
  return p;
}
const me = (p) => p.eval(`fetch("/api/me").then((r) => r.json())`);
const title = (p, text) => p.waitFor(`document.querySelector("#dlg-account").open && document.querySelector("#account-title").textContent === ${JSON.stringify(text)}`, 10000, `the account dialog: ${text}`);
async function signUp(p, name) {
  await title(p, "Sign in to play");
  await p.eval(`(() => { const f = document.querySelector("#acct-signup"); f.elements.name.value = ${JSON.stringify(name)}; f.requestSubmit(); })()`);
}

try {
  const ada = await browser();
  step = "sign up";
  await signUp(ada, "Ada");
  await title(ada, "Hi, Ada");
  if (!(await ada.eval(`document.querySelector("#btn-new").hidden`))) throw new Error("Ada sees New game before she may host");

  step = "the admin lets Ada host";
  const admin = await browser();
  await admin.goto(URL + "#host=" + encodeURIComponent(KEY));
  await admin.waitFor(`document.querySelector("#btn-account span").textContent === "Admin"`, 10000, "the admin button");
  await admin.eval(`document.querySelector("#dlg-new").close(); document.querySelector("#btn-account").click()`);
  await admin.waitFor(`document.querySelector('#acct-users input[type=checkbox]')`, 8000, "the accounts list");
  await admin.eval(`document.querySelector('#acct-users input[type=checkbox]').click()`);
  await admin.waitFor(`document.querySelector('#acct-users input[type=checkbox]')?.checked`, 8000, "Ada may host");

  step = "Ada hosts";
  await ada.goto(URL);
  await ada.waitFor(`document.querySelector("#dlg-new").open`, 8000, "the new game dialog");
  await ada.eval(`(() => {
    const f = document.querySelector("#form-new");
    for (const [k, v] of [["mode", "online"], ["humans", "2"], ["opponents", "1"]]) f.querySelector('[name="' + k + '"][value="' + v + '"]').click();
    f.querySelector('button[value="start"]').click();
  })()`);
  await ada.waitFor(`!document.querySelector("#lobby").hidden`, 8000, "the lobby");
  const link = await ada.eval(`document.querySelector("#invite-link").value`);

  step = "Bob joins from the invite";
  const bob = await browser();
  await bob.goto(link);
  await signUp(bob, "Bob");
  await bob.waitFor(`document.querySelector("#dlg-join").open && document.querySelector("#join-ok").textContent.startsWith("Join as Bob")`, 10000, "the join dialog");
  await bob.eval(`document.querySelector("#join-ok").click()`);
  await bob.waitFor(`document.querySelectorAll("#board .hex-tile").length === 19`, 8000, "Bob's board");
  await ada.waitFor(`document.querySelector("#lobby").hidden && document.querySelector("#players").innerText.includes("Bob")`, 8000, "Ada sees Bob arrive");

  step = "Bob signs out and back in";
  await bob.eval(`document.querySelector("#btn-account").click()`);
  await bob.waitFor(`document.querySelector("#acct-signout")`, 8000, "the sign out button");
  await bob.eval(`document.querySelector("#acct-signout").click()`);
  await bob.waitFor(`document.querySelector("#btn-account span").textContent === "Sign in"`, 10000, "signed out");
  await bob.waitFor(`document.querySelectorAll("#board .hex-tile").length === 19`, 8000, "still in the game, by its seat key");
  await bob.eval(`document.querySelector("#btn-account").click()`);
  await bob.waitFor(`document.querySelector("#acct-signin")`, 8000, "the sign in button");
  await bob.eval(`document.querySelector("#acct-signin").click()`);
  await bob.waitFor(`document.querySelector("#btn-account span").textContent === "Bob"`, 10000, "signed in again");
  if ((await me(bob)).user?.name !== "Bob") throw new Error("the server doesn't know Bob");

  step = "page errors";
  for (const p of pages) if (p.errors.length) throw new Error(p.errors.join(" | "));
  console.log("ok: sign up, approval, hosting, joining by invite, sign out and in");
} catch (e) {
  console.error(`FAILED ${step}: ${e.message}`);
  for (const p of pages) {
    console.error(await p.eval(`JSON.stringify({ at: location.href, open: [...document.querySelectorAll("dialog[open]")].map((d) => d.id),
      account: document.querySelector("#account-title").textContent, join: document.querySelector("#join-ok").textContent })`).catch(() => "?"), p.errors, p.logs);
  }
  process.exitCode = 1;
} finally {
  for (const p of pages) await p.close();
}

// End-to-end check of the browser client in headless Chrome or Chromium.
//
//   python3 -m hexfield.web --port 8765 --no-browser &
//   node tools/e2e.mjs http://127.0.0.1:8765/ [screenshot-folder]
//
// Plays through the screens a person uses: a solo game by keyboard, the hint panel,
// saved games, a replay, pass-and-play, an online game with a second browser, and a
// six-player board. Exits non-zero on the first failure or on any script error in the
// page. HEXFIELD_BROWSER_TESTS=1 python3 -m unittest tests.test_js runs it for you.

import { launch } from "./browser.mjs";

const URL = process.argv[2] || "http://127.0.0.1:8765/";
const SHOTS = process.argv[3];
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
let step = "start";
const check = (ok, what) => { if (!ok) throw new Error(`${step}: ${what}`); };

// wait for a condition on the client's state (S) instead of sleeping and hoping
const until = (p, cond, what, ms = 8000) => p.waitFor(`(async () => {
  const m = await import("/js/state.js"); const S = m.S; return !!(${cond});
})()`, ms, what);

const state = (p) => p.eval(`(async () => {
  const m = await import("/js/state.js"); const S = m.S;
  return S && { phase: S.phase, mine: m.myDecision(), turn: S.turn, human: S.human, lobby: S.lobby, n: S.n,
    gid: S.gid, replay: S.replay && S.replay.k, reviews: S.reviews.length,
    dlg: [...document.querySelectorAll("dialog[open]")].map((d) => d.id) };
})()`);

async function newGame(p, picks) {
  await p.eval(`document.querySelector("#dlg-new").open || document.querySelector("#btn-new").click()`);
  await p.waitFor(`document.querySelector("#dlg-new").open`, 8000, "the new game dialog");
  await p.eval(`(() => {
    const f = document.querySelector("#form-new");
    for (const [name, value] of ${JSON.stringify(Object.entries(picks))}) {
      const radio = f.querySelector('[name="' + name + '"][value="' + value + '"]');
      if (radio) radio.click(); else f.elements[name].value = value;
    }
    f.querySelector('button[value="start"]').click();
  })()`);
  await p.waitFor(`!document.querySelector("#dlg-new").open && document.querySelectorAll("#board .hex-tile").length > 0`, 8000, "the board");
}

// press the obvious key until `done(state)`; dialogs get Space too (the recommended answer)
async function playUntil(p, done, moves = 400) {
  for (let i = 0; i < moves; i++) {
    const s = await state(p);
    if (done(s)) return s;
    if (s.dlg.includes("dlg-pass")) await p.eval(`document.querySelector("#pass-ok").click()`);
    // a double click on an offer must send one answer (a second one would get a 400)
    else if (s.dlg.includes("dlg-botoffer")) await p.eval(`(b => { b.click(); b.click(); })(document.querySelector("#bo-yes"))`);
    else if (s.dlg.length) await p.key(" ");
    else if (s.mine) await p.key(s.phase === "main" || s.phase === "special_build" ? "e" : " ");
    await wait(90);
  }
  throw new Error(`${step}: the game did not get there`);
}

const page = await launch(URL);
let guest = null, phone = null;
try {
  await page.eval(`localStorage.clear(); localStorage.setItem("hexfield.speed", '"fast"')`);
  await page.goto(URL);
  await page.waitFor(`document.querySelector("#dlg-new").open`, 8000, "the new game dialog on first visit");

  step = "solo game";
  await newGame(page, { mode: "solo", opponents: "2", level: "easy", seed: "11" });
  await page.waitFor(`document.querySelectorAll("#board .hex-tile").length === 19`, 8000, "19 hexes");
  await page.waitFor(`document.querySelectorAll("#board [role=button]").length > 20`, 8000, "board spots are buttons");
  await page.waitFor(`document.querySelectorAll("#players .player").length === 3`, 8000, "three player panels");
  await playUntil(page, (s) => s.turn >= 1 && s.mine && s.phase === "main");

  step = "hint";
  await page.key("h");
  await page.waitFor(`document.querySelectorAll("#coach-out .opt").length > 0`, 15000, "hint options");
  await page.key("Escape");

  // U undoes on every screen, the trade dialog included (it has its own Undo button too)
  step = "undo in the trade dialog";
  await page.key("t");
  await page.waitFor(`document.querySelector("#dlg-trade").open`, 6000, "the trade dialog");
  check(await page.eval(`!!document.querySelector("#dlg-trade .dlg-undo:not([hidden])")`), "the trade dialog has no Undo button");
  const evs0 = await page.eval(`(async () => (await import("/js/state.js")).S.ev_total)()`);
  await page.key("u");
  await until(page, `S.ev_total < ${evs0}`, "U in the trade dialog takes the last decision back");
  await page.waitFor(`!document.querySelector("#dlg-trade").open`, 6000, "back before the roll, the trade dialog closes");
  await playUntil(page, (s) => s.turn >= 6);
  await until(page, `S.reviews.length > 0`, "decisions are reviewed", 30000);   // the coach works in the background
  if (SHOTS) await page.screenshot(`${SHOTS}/solo.png`);

  step = "reload resumes";
  const before = await state(page);
  await page.goto(URL);
  await page.waitFor(`document.querySelectorAll("#board .hex-tile").length === 19`, 8000, "the board after a reload");
  await until(page, `S && S.gid === ${JSON.stringify(before.gid)}`, "the same game");

  step = "saved games";
  await page.eval(`document.querySelector("#btn-games").click()`);
  await page.waitFor(`document.querySelector('#games-body [data-do="replay"]')`, 6000, "the saved games list");
  await page.eval(`document.querySelector("#tab-progress").click()`);
  await page.waitFor(`document.querySelector("#games-body").innerText.length > 20`, 6000, "the progress tab");
  await page.eval(`document.querySelector("#tab-saved").click()`);
  await page.waitFor(`document.querySelector('#games-body [data-do="replay"]:not(:disabled)')`, 6000, "a game to replay");
  if (SHOTS) await page.screenshot(`${SHOTS}/games.png`);

  step = "replay";
  await page.eval(`document.querySelector('#games-body [data-do="replay"]:not(:disabled)').click()`);
  await page.waitFor(`!document.querySelector("#replay-bar").hidden`, 6000, "the replay bar");
  for (let i = 1; i <= 5; i++) {
    await page.key("ArrowRight");
    await until(page, `S.replay && S.replay.k === ${i}`, `move ${i} of the replay`);
  }
  await page.key("ArrowLeft");
  await until(page, `S.replay && S.replay.k === 4`, "one move back");
  await page.key("]");
  await page.waitFor(`!!document.querySelector("#coach-out .rp-decision")`, 8000, "the decision at this point is shown");
  await page.waitFor(`document.querySelectorAll("#board [role=button]").length === 0`, 8000, "a replay has nothing to click");
  if (SHOTS) await page.screenshot(`${SHOTS}/replay.png`);
  await page.eval(`document.querySelector("#rp-branch").click()`);
  await page.waitFor(`document.querySelector("#replay-bar").hidden`, 6000, "playing on from the replay");
  await until(page, `S && S.gid !== ${JSON.stringify(before.gid)}`, "playing on makes a new game");

  step = "pass and play";
  await newGame(page, { mode: "hotseat", humans: "2", opponents: "1", seed: "5" });
  await page.waitFor(`document.querySelector("#dlg-pass").open || document.querySelectorAll("#board [role=button]").length > 0`, 8000, "the first move");
  let seats = new Set();
  for (let i = 0; i < 200 && seats.size < 2; i++) {
    const s = await state(page);
    if (s.dlg.includes("dlg-pass")) {
      check(await page.eval(`getComputedStyle(document.querySelector(".app")).visibility`) === "hidden", "the table is hidden while the screen changes hands");
      if (SHOTS && !seats.size) await page.screenshot(`${SHOTS}/pass.png`);
      await page.eval(`document.querySelector("#pass-ok").click()`);
      await wait(350);
    } else if (s.mine) { seats.add(s.human); await page.key(" "); }
    await wait(100);
  }
  check(seats.size === 2, "both players got the screen");
  // around one screen you may take back your own last decision: it asks first
  await page.waitFor(`!document.querySelector("#btn-undo").hidden && !document.querySelector("#btn-undo").disabled`, 8000, "a take-back is offered after your own decision");
  const placed = await page.eval(`(async () => (await import("/js/state.js")).S.ev_total)()`);
  await page.key("u");
  await page.waitFor(`document.querySelector("#dlg-takeback").open`, 6000, "the take-back asks the table first");
  if (SHOTS) await page.screenshot(`${SHOTS}/takeback.png`);
  await page.eval(`document.querySelector("#tb-yes").click()`);
  await until(page, `S.ev_total < ${placed} && !document.querySelector("#dlg-takeback").open`, "the decision is taken back");

  step = "online";
  await newGame(page, { mode: "online", humans: "2", opponents: "1", seed: "6" });
  await page.waitFor(`!document.querySelector("#lobby").hidden`, 6000, "the lobby");
  if (SHOTS) await page.screenshot(`${SHOTS}/lobby.png`);
  const link = await page.eval(`document.querySelector("#invite-link").value`);
  guest = await launch(link);
  await guest.waitFor(`document.querySelector("#dlg-join").open`, 8000, "the join dialog");
  await guest.eval(`document.querySelector('#form-join [name="name"]').value = "Guest"; document.querySelector("#join-ok").click()`);
  await guest.waitFor(`document.querySelectorAll("#board .hex-tile").length === 19`, 8000, "the guest's board");
  await page.waitFor(`document.querySelector("#lobby").hidden`, 8000, "the host sees the guest arrive");
  // whoever the game is waiting for is marked on both screens: their card, the status line of
  // anyone not owed a move, and the tab title of anyone who is
  const due = (p) => p.eval(`(async () => { const S = (await import("/js/state.js")).S; const on = S.waiting_on;
    const cards = [...document.querySelectorAll("#players .player")].map((el, i) => (el.classList.contains("awaited") ? i : "")).join("");
    const me = on.includes(S.human), status = document.querySelector("#status").innerText;
    return { on: on.join(""), me, shown: cards === on.join("") && document.title.includes("Your move") === me
      && (me || on.every((q) => status.includes(S.players[q].name)) && status.startsWith("Waiting for")) }; })()`);
  let marked = false;
  for (let i = 0; i < 120; i++) {
    const a = await state(page), b = await state(guest);
    if (a.turn >= 2 && b.turn >= 2 && marked) break;
    if (!marked) {
      const [x, y] = [await due(page), await due(guest)];
      marked = !!x.on && x.on === y.on && x.shown && y.shown && x.me !== y.me; // one to move, the other watching
    }
    if (a.mine) await page.key(a.phase === "main" ? "e" : " ");
    if (b.mine) await guest.key(b.phase === "main" ? "e" : " ");
    await wait(180);
    check(i < 119, "two browsers could not play the opening");
  }
  const [a, b] = [await state(page), await state(guest)];
  check(a.human !== b.human && a.gid === b.gid, "two seats in one game");
  check(marked, "the screens never showed who the game was waiting for");
  // online, a take-back needs the other player: whoever decided last asks, the other allows
  const [asker, other] = (await page.eval(`(async () => (await import("/js/state.js")).S.can_undo)()`)) ? [page, guest] : [guest, page];
  await asker.waitFor(`!document.querySelector("#btn-undo").hidden`, 8000, "the last to decide may ask to take it back");
  const evs = await asker.eval(`(async () => (await import("/js/state.js")).S.ev_total)()`);
  await asker.key("u");
  await other.waitFor(`document.querySelector("#dlg-takeback").open`, 12000, "the other player is asked");
  if (SHOTS) await other.screenshot(`${SHOTS}/takeback-online.png`);
  // the asker can always call it off: the Withdraw button is really there to press, at any width
  await asker.waitFor(`!!document.querySelector("#tb-withdraw")`, 6000, "the asker gets a way to withdraw");
  check(await asker.eval(`(() => { const r = document.querySelector("#tb-withdraw").getBoundingClientRect();
    return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)?.id === "tb-withdraw"; })()`), "Withdraw is covered or clipped away");
  if (SHOTS) await asker.screenshot(`${SHOTS}/takeback-asker.png`);
  // Space is "the obvious move" everywhere else; it must not allow a request that just popped up
  await other.key(" ");
  await wait(400);
  check(await other.eval(`(async () => !!(await import("/js/state.js")).S.takeback)()`) && await other.eval(`document.querySelector("#dlg-takeback").open`), "Space allowed someone else's take-back");
  check(await page.eval(`(async () => (await import("/js/util.js")).store("newgame", {}).name)()`) !== "Host", "the host's name for online games replaced the name they typed");
  await other.eval(`document.querySelector("#tb-yes").click()`);
  await until(asker, `!S.takeback && S.ev_total < ${evs}`, "allowed: the move is taken back", 12000);
  await until(other, `!S.takeback && S.ev_total < ${evs}`, "and on the other screen too", 12000);
  check(await other.eval(`document.querySelectorAll("#log li").length > 0`), "the other screen has its log redrawn");
  check(guest.errors.length === 0, "guest page errors: " + guest.errors.join(" | "));

  step = "no bot offers";
  await page.eval(`document.querySelector("#mute-offers").click()`);
  await newGame(page, { mode: "solo", opponents: "3", level: "easy", seed: "8" });
  await until(page, `S.mute_offers === true`, "the preference reaches the new game");
  await playUntil(page, (s) => { check(!s.dlg.includes("dlg-botoffer"), "a bot offered a trade although offers are muted"); return s.turn >= 12; });
  await page.eval(`document.querySelector("#mute-offers").click()`);

  step = "six players";
  await newGame(page, { mode: "solo", opponents: "5", level: "easy", seed: "7" });
  // the dialog closes before the new board replaces the old one: wait for it, don't sample it
  await page.waitFor(`document.querySelectorAll("#board .hex-tile").length === 30`, 8000, "30 hexes");
  await page.waitFor(`document.querySelectorAll("#players .player").length === 6`, 8000, "six player panels");
  await playUntil(page, (s) => s.turn >= 8);
  if (SHOTS) await page.screenshot(`${SHOTS}/six.png`);

  // a phone: the whole game on one screen (bar, the others, the board, your cards, the big button)
  step = "phone";
  phone = await launch(URL, { width: 390, height: 760, mobile: true });
  await phone.waitFor(`document.querySelectorAll("#board .hex-tile").length === 30`, 8000, "the board on a phone");
  await wait(600);
  const lay = await phone.eval(`(() => { const r = (s) => { const b = document.querySelector(s).getBoundingClientRect(); return { top: b.top, bottom: b.bottom, width: b.width }; };
    return { players: r("#players"), board: r("#board"), tray: r(".tray"), status: r("#status"), w: innerWidth, h: innerHeight,
      wide: document.documentElement.scrollWidth, chips: [...document.querySelectorAll("#players .player")].map((p) => p.getBoundingClientRect().top) }; })()`);
  if (SHOTS) await phone.screenshot(`${SHOTS}/phone.png`);
  if (process.env.E2E_DEBUG) console.log(JSON.stringify(lay));
  check(lay.wide <= lay.w, "the page scrolls sideways on a phone");
  check(lay.status.bottom <= lay.players.top + 1 && lay.players.bottom <= lay.board.top + 1 && lay.board.bottom <= lay.tray.top + 1, "bar, players, board and tray are not in that order");
  check(lay.board.width >= lay.w - 1, "the board does not run from edge to edge");
  check(lay.tray.bottom <= lay.h, "the tray does not fit on the first screen");
  check(new Set(lay.chips.map(Math.round)).size === 1, "the players are not one strip");
  if (SHOTS) await phone.screenshot(`${SHOTS}/phone.png`);
  await phone.eval(`document.querySelector("#players").click()`);
  check(await phone.eval(`getComputedStyle(document.querySelector("#players .p-race")).display !== "none"`), "a tap does not open the player cards");
  check(phone.errors.length === 0, "phone page errors: " + phone.errors.join(" | "));

  step = "page errors";
  check(page.errors.length === 0, page.errors.join(" | "));
  console.log("ok: solo, hint, reload, saved games, replay, pass and play, take-backs, online, no bot offers, six players, phone");
} catch (e) {
  console.error("FAILED " + e.message);
  if (page.errors.length) console.error("page errors: " + page.errors.join(" | "));
  if (SHOTS) await page.screenshot(`${SHOTS}/failed.png`).catch(() => {});
  process.exitCode = 1;
} finally {
  if (guest) await guest.close();
  if (phone) await phone.close();
  await page.close();
}

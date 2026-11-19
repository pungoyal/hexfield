// Hexfield — browser client. Talks to hexfield/web/server.py; no dependencies.
// This module drives the game: boot, applying server views, the bot loop, wiring.

import { installSprites } from "./art.js";
import { buildBoard, clearGlow, glowFor, onBoardClick, onBoardHover, onBoardKey, renderPieces, renderTargets } from "./board.js";
import {
  closeCoach, decisionKey, glowSteps, newReviews, optSel, optSteps, playHint, renderReviews,
  selectOpt, showEval, showFullReview, showHint,
} from "./coach.js";
import {
  newGameOptions, onTradeClick, openBotOffer, openDiscard, openNewGame, openPick, openTrade, renderTrade,
  showGameOver, showHelp, showStats, syncNewGame,
} from "./dialogs.js";
import { effects, handGains, recapTurn, toast } from "./effects.js";
import { leaveReplay, openJoin, openReplay, passDevice, renderLobby, renderReplay, wireGames } from "./games.js";
import { decorateKeys, onKey, renderKeys } from "./keys.js";
import {
  appendLog, pinLog, renderActions, renderBank, renderDice, renderEcon, renderHand, renderPlayers,
  renderStatus, wireLog,
} from "./panels.js";
import { B, G, S, api, building, can, els, localSeats, myDecision, prefs, session, setB, setS, setSession } from "./state.js";
import { human } from "./text.js";
import { $, $$, DEVS, SPEEDS, save, sleep } from "./util.js";

export let pollTimer = null;
export async function act(action) {
  if (G.busy) return false;
  G.busy = true;
  G.holdAutoRoll = false;
  let ok = false;
  try {
    const st = await api("act", { action });
    apply(st);
    ok = true;
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  } finally {
    G.busy = false;
    render();
  }
  pump();
  return ok;
}

// ------------------------------------------------------------------ boot
export async function boot() {
  installSprites();
  wireUI();
  const want = new URLSearchParams(location.search).get("g");
  if (want && want !== session.gid) setSession(want, {}, null); // an invite link to another game
  try {
    const st = await api("board");
    if (st.nogame) return openNewGame(false);
    if (st.join) return openJoin(st);
    enter(st);
    if (S.phase === "game_over") G.overShown = S.gid;
    pump();
  } catch {
    setSession(null, {}, null);
    openNewGame(false);
  }
}

// Show a game from scratch: a new one, a resumed one, another seat's view, or a replay.
export function enter(st) {
  if (st.tokens) setSession(st.gid, { ...(st.gid === session.gid ? session.tokens : {}), ...st.tokens }, null);
  else if (!session.replay && st.gid !== session.gid) setSession(st.gid, {}, null);
  if (!session.replay) history.replaceState(null, "", `?g=${st.gid}`);
  setS(null);
  setB(st.board);
  G.evCount = 0;
  G.prev = null;
  G.mode = null;
  G.reviewsSeen = st.reviews.length;
  G.overShown = null;
  G.hintKey = null;
  G.lastRoller = null;
  $("#log").replaceChildren();
  G.logStick = true;
  $("#coach-out").replaceChildren();
  $("#toasts").replaceChildren(); // verdicts and news of the game you just left
  $("#recap").classList.remove("show");
  buildBoard();
  apply(st, true);
}

export async function newGame(opts) {
  leaveReplay();
  const st = await api("new", opts);
  enter(st);
  G.reviewsSeen = 0;
  pump();
}

// ------------------------------------------------------------------ apply
export function apply(st, initial = false) {
  // a response from a game that has since been replaced (e.g. a slow /step racing /new)
  if (!initial && S && st.gid && S.gid && st.gid !== S.gid) return;
  if (!initial && S && st.human !== S.human) return; // an answer for the seat that just handed the screen over
  if (st.reset_log || st.ev_total < G.evCount) {
    $("#log").replaceChildren();
    G.evCount = 0;
    G.logStick = true;
    initial = true; // don't replay effects after an undo
  }
  const fresh = st.events.filter((e) => e.i >= G.evCount);
  for (const e of fresh) if (e.kind === "roll") G.lastRoller = e.p;
  G.prev = S;
  setS(st);
  G.evCount = st.ev_total;
  appendLog(fresh);
  if (!initial) { effects(fresh); handGains(fresh); }
  recapTurn(fresh, initial);
  if (G.mode && !(S.legal[G.mode] || []).length) G.mode = null;
  if (G.hintKey && G.hintKey !== decisionKey()) closeCoach();
  render();
  pinLog();
  pollReviews();
  newReviews();
  renderLobby();
  renderReplay();
  if (S.phase === "game_over" && G.overShown !== S.gid && !session.replay) {
    G.overShown = S.gid;
    setTimeout(showGameOver, initial ? 0 : 900);
  }
}


// ------------------------------------------------------------------ the game loop
// Runs whenever it isn't this screen's move: steps the bots, waits for players on other
// devices, and in pass-and-play hands the screen to the next person.
export async function pump() {
  if (G.pumping || !S || session.replay) return;
  G.pumping = true;
  try {
    while (S && !session.replay && !G.stopPump && S.phase !== "game_over" && !$("#dlg-new").open) {
      const waiting = S.waiting_on || [];
      if (waiting.includes(S.human)) break;
      const local = waiting.find((q) => localSeats().includes(q));
      if (local !== undefined) { await passDevice(local); continue; }
      if (S.lobby || waiting.length) { // someone on another device
        setThinking(waiting[0] ?? null);
        try { apply(await api("wait", undefined, { v: S.version })); } catch { await sleep(1500); }
        continue;
      }
      setThinking(S.actor);
      let st;
      try {
        st = await api("step", { v: S.version });
      } catch (e) {
        toast("Server error", e.message, "var(--worst)");
        break;
      }
      const before = S;
      apply(st);
      let wait = SPEEDS[prefs.speed] ?? 380;
      if (st.events.some((e) => e.kind === "roll")) wait += prefs.speed === "fast" ? 150 : 450;
      if (st.current !== before.current) wait += prefs.speed === "fast" ? 50 : 250;
      await sleep(wait);
    }
  } finally {
    G.pumping = false;
    setThinking(null);
    render();
  }
  if (S && !session.replay) openBotOffer(); // opens, refreshes or closes with the offer
  if (S && S.discard_owed && myDecision()) openDiscard();
  maybeAutoRoll();
}

// While it is your move the screen still needs news: reviews being worked out, and in
// a shared game the other players' answers to your offer.
export function pollReviews() {
  if (pollTimer || !S || session.replay || !(S.reviewing || (S.mode !== "solo" && S.phase !== "game_over"))) return;
  pollTimer = setTimeout(async () => {
    pollTimer = null;
    if (G.busy || G.pumping || $("#dlg-pass").open) return pollReviews();
    try {
      const st = await api("state");
      if (st.gid === S?.gid) { apply(st); pump(); }
    } catch { /* next action will refresh */ }
  }, S.reviewing ? 900 : 1500);
}

export function maybeAutoRoll() {
  if (G.holdAutoRoll || !prefs.autoroll || !myDecision() || S.phase !== "pre_roll") return;
  const kinds = S.legal.kinds || [];
  // only a ranger is worth playing before the roll; other dev cards can wait until after it
  if (kinds.includes("roll") && !kinds.includes("play_ranger")) {
    const turn = S.turn;
    setTimeout(() => { if (S.turn === turn && S.phase === "pre_roll" && myDecision()) act(["roll"]); }, 350);
  }
}

export function setThinking(p) {
  if (!S) return;
  $$(".player").forEach((el, i) => el.classList.toggle("thinking", i === p));
  renderStatus(p);
}

// ------------------------------------------------------------------ render
export function render() {
  if (!S || !B) return;
  // forced decisions: keep their dialog open even if it was dismissed
  const owe = myDecision() && S.phase === "discard" && S.discard_owed > 0;
  $("#btn-discard").hidden = !owe;
  $("#btn-answer").hidden = !S.pending_offer;
  if (!G.busy && !G.pumping) {
    if (owe && !$("#dlg-discard").open) queueMicrotask(openDiscard);
    if (!session.replay && (S.pending_offer || $("#dlg-botoffer").open)) queueMicrotask(openBotOffer);
  }
  if ($("#dlg-trade").open) renderTrade();
  renderPieces();
  renderTargets();
  renderKeys();
  renderPlayers();
  renderHand();
  renderActions();
  renderStatus(G.pumping ? S.actor : null);
  renderDice(false);
  renderBank();
  renderEcon();
  renderReviews();
  $("#seed").textContent = `seed ${S.seed}`;
  $("#btn-undo").hidden = S.mode !== "solo" || !!session.replay;
  document.body.classList.toggle("many", S.n > 4);
  document.body.classList.toggle("my-turn", !!myDecision() || !!S.pending_offer);
  $("#btn-undo").disabled = !S.can_undo;
  for (const b of $$(".dlg-undo")) b.hidden = !S.can_undo;
  $("#review-on").checked = S.review_on;
  $("#reviewing").hidden = !S.reviewing;
}

// ------------------------------------------------------------------ wiring
export function wireUI() {
  const board = $("#board");
  board.addEventListener("click", onBoardClick);
  board.addEventListener("keydown", onBoardKey);
  board.addEventListener("pointermove", onBoardHover);
  board.addEventListener("pointerleave", () => $("#tip").classList.remove("show"));

  $("#btn-roll").onclick = () => act(["roll"]);
  $("#btn-end").onclick = () => { G.mode = null; act(["end"]); };
  decorateKeys();
  $("#btn-dev").onclick = () => act(["buy_dev"]);
  $("#btn-trade").onclick = () => openTrade();
  $("#btn-discard").onclick = () => openDiscard();
  $("#btn-answer").onclick = () => openBotOffer();
  for (const b of $$("[data-mode]")) b.onclick = () => toggleMode(b.dataset.mode);
  $("#btn-hint").onclick = showHint;
  $("#btn-eval").onclick = showEval;
  $("#btn-review").onclick = showFullReview;
  $("#btn-undo").onclick = undo;
  for (const b of $$(".dlg-undo")) b.onclick = undo;
  $("#btn-stats").onclick = showStats;
  $("#btn-help").onclick = showHelp;
  $("#btn-new").onclick = () => openNewGame(true);
  $("#status").addEventListener("click", (e) => { if (e.target.closest("#show-over")) showGameOver(); });
  $("#seed").onclick = () => {
    if (!S) return;
    navigator.clipboard?.writeText(String(S.seed)).then(() => toast("Seed copied", `Start a new game with seed ${S.seed} to replay this board and dice.`, "var(--muted)", 2600), () => {});
  };

  $("#devhand").addEventListener("click", (e) => {
    const c = e.target.closest(".dcard.playable");
    if (!c || G.busy) return;
    const k = Number(c.dataset.dev);
    if (k === 3) openPick("hoard");
    else if (k === 4) openPick("windfall");
    else act([DEVS[k].play]);
  });

  const co = $("#coach-out");
  co.addEventListener("pointerover", (e) => {
    const li = e.target.closest(".opt");
    if (li && e.pointerType === "mouse") glowSteps(optSteps(Number(li.dataset.i)));
  });
  co.addEventListener("pointerleave", () => (optSel !== null ? glowSteps(optSteps(optSel)) : clearGlow()));
  co.addEventListener("focusin", (e) => { const li = e.target.closest(".opt"); if (li) selectOpt(li, false); });
  co.addEventListener("click", (e) => {
    if (e.target.closest(".co-x")) return closeCoach();
    const b = e.target.closest(".do");
    if (b) return playHint(Number(b.dataset.i));
    const li = e.target.closest(".opt");
    if (li) selectOpt(li, true);
  });

  $("#log").addEventListener("pointerover", (e) => {
    const li = e.target.closest("li");
    if (!li) return clearGlow();
    if (li.dataset.v) glowFor(["settle", Number(li.dataset.v)]);
    else if (li.dataset.e) glowFor(["road", Number(li.dataset.e)]);
    else if (li.dataset.h) glowFor(["bandit", Number(li.dataset.h)]);
    else clearGlow();
  });
  $("#log").addEventListener("pointerleave", clearGlow);
  $("#reviews").addEventListener("pointerover", (e) => {
    const li = e.target.closest(".rev");
    clearGlow();
    if (!li) return;
    if (li.dataset.b) glowFor(JSON.parse(li.dataset.b), true);
    if (li.dataset.a && li.dataset.a !== li.dataset.b) {
      const before = els.glow.children.length;
      glowFor(JSON.parse(li.dataset.a), true);
      [...els.glow.children].slice(before).forEach((m) => m.classList.add("mine"));
    }
  });
  $("#reviews").addEventListener("pointerleave", clearGlow);

  $("#review-on").onchange = async (e) => { try { apply(await api("settings", { review: e.target.checked })); } catch { /* ignore */ } };
  const reasons = $("#reason-on");
  reasons.checked = prefs.reasons;
  $("#log").classList.toggle("no-why", !prefs.reasons);
  reasons.onchange = () => { prefs.reasons = reasons.checked; save("reasons", prefs.reasons); $("#log").classList.toggle("no-why", !prefs.reasons); };

  $("#speed").value = prefs.speed;
  $("#speed").onchange = (e) => { prefs.speed = e.target.value; save("speed", prefs.speed); };
  $("#autoroll").checked = prefs.autoroll;
  $("#autoroll").onchange = (e) => { prefs.autoroll = e.target.checked; save("autoroll", prefs.autoroll); maybeAutoRoll(); };

  $("#dlg-trade").addEventListener("click", onTradeClick);

  // new game dialog
  const f = $("#form-new");
  f.addEventListener("change", syncNewGame);
  $("#dlg-new").addEventListener("close", async () => {
    const dlg = $("#dlg-new");
    if (dlg.returnValue === "saved") return;
    if (dlg.returnValue !== "start") { if (!S) openNewGame(false); else pump(); return; }
    dlg.returnValue = "";
    try { await newGame(newGameOptions()); } catch (e) { toast("Couldn't start", e.message, "var(--worst)"); openNewGame(!!S); }
  });
  $("#over-new").onclick = () => { $("#dlg-over").close(); openNewGame(true); };
  $("#over-body").addEventListener("click", (e) => { if (e.target.id === "over-all") { $("#dlg-over").close(); showFullReview(); } });
  $("#over-watch").onclick = () => { $("#dlg-over").close(); openReplay(S.gid); };
  $("#over-replay").onclick = async () => {
    $("#dlg-over").close();
    const bots = S.players.filter((pl) => pl.bot).length;
    const opts = { mode: S.mode, opponents: bots, level: S.level, seed: S.seed, dice: S.options.dice, layout: S.options.layout };
    if (S.mode === "solo") Object.assign(opts, { seat: S.human + 1, name: S.players[S.human].name });
    else Object.assign(opts, { humans: S.n - bots, names: S.players.filter((pl) => !pl.bot).map((pl) => pl.name) });
    try { await newGame(opts); } catch (e) { toast("Couldn't start", e.message, "var(--worst)"); }
  };

  // forced decisions can't be escaped; they reopen if dismissed
  for (const id of ["#dlg-discard", "#dlg-botoffer"]) {
    $(id).addEventListener("cancel", (e) => e.preventDefault());
  }

  // colour-blind friendly marks: a safer palette and seat numbers on the pieces
  const marks = $("#marks");
  marks.checked = prefs.marks;
  document.body.classList.toggle("marks", prefs.marks);
  marks.onchange = () => { prefs.marks = marks.checked; save("marks", prefs.marks); document.body.classList.toggle("marks", prefs.marks); };

  document.addEventListener("keydown", onKey);
  wireLog();
  wireGames();
}

export function toggleMode(m) {
  if (!building() || !can(m)) return;
  G.mode = G.mode === m ? null : m;
  render();
}

export let undoing = false;
export async function undo() {
  if (!S?.can_undo || undoing) return;
  undoing = true;
  // undo is always allowed: let the bots finish their current step and any request land first
  G.stopPump = true;
  while (G.busy || G.pumping) await sleep(40);
  G.stopPump = false;
  // forced dialogs (discard, a bot's offer) and the game-over screen give way to the undo
  for (const d of $$("dialog[open]")) if (d.id !== "dlg-new") d.close();
  G.busy = true;
  try {
    G.mode = null;
    G.holdAutoRoll = true;
    const st = await api("undo", {});
    apply(st);
    G.reviewsSeen = S.reviews.length;
    G.overShown = null; // undoing out of a finished game: show the result again if it ends again
    toast(`Undid: ${st.undone ? human(st.undone) : "your last decision"}`, "Same dice, different choices.", "var(--muted)", 2600);
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  }
  G.busy = false;
  undoing = false;
  render();
  pump();
}

boot();

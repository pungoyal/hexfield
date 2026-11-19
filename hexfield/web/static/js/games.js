// Beyond one live game: saved games, your progress, the replay viewer, online lobbies
// and pass-and-play.

import { apply, enter, pump } from "./app.js";
import { clearGlow, glowFor } from "./board.js";
import { openNewGame } from "./dialogs.js";
import { modal, toast } from "./effects.js";
import { fmtWhen, replayStep, sparkPoints } from "./logic.js";
import { G, S, api, els, session, setSession } from "./state.js";
import { human, plain, short, symbol } from "./text.js";
import { $, $$, esc, pc, setHTML, sleep } from "./util.js";

// ------------------------------------------------------------------ saved games
let tab = "saved";

export async function openGames(which = tab) {
  tab = which;
  const dlg = $("#dlg-games");
  if (!dlg.open) modal(dlg);
  for (const b of $$(".tab", dlg)) b.setAttribute("aria-selected", String(b.id === `tab-${tab}`));
  const body = $("#games-body");
  body.innerHTML = `<p class="coach-note">Loading…</p>`;
  try {
    body.innerHTML = tab === "saved" ? savedHTML((await api("games")).games) : progressHTML(await api("stats"));
  } catch (e) {
    body.innerHTML = `<p class="coach-note">${esc(e.message)}</p>`;
  }
}

function savedHTML(games) {
  if (!games.length) return `<p class="coach-note">No saved games yet. Every game you play is saved as you go, so you can close the window and pick it up later.</p>`;
  const rows = games.map((g) => {
    const res = g.result;
    const who = g.players.map((p, i) => `<span class="${res && res.winner === i ? "won" : ""}" style="color:${pc(i)}">${esc(p.name)}</span>`).join(", ");
    const state = res ? `${esc(g.players[res.winner].name)} won · ${res.rounds} rounds`
      : `<b>in play</b> · round ${g.round || 0}`;
    const acc = g.coach ? `${Math.round(g.coach.accuracy)}%` : "";
    const kind = { solo: "", hotseat: "pass and play", online: "online" }[g.mode] || "";
    return `<tr data-id="${g.id}">
      <td class="muted nowrap">${fmtWhen(g.updated)}</td>
      <td>${who}${kind ? ` <span class="p-tag">${kind}</span>` : ""}${g.players.length > 4 ? ` <span class="p-tag">large board</span>` : ""}</td>
      <td>${state}</td><td class="num" title="Your accuracy in this game">${acc}</td>
      <td class="row-actions">
        ${res ? "" : `<button class="btn primary" data-do="resume">Resume</button>`}
        <button class="btn" data-do="replay" ${g.moves ? "" : "disabled"}>Replay</button>
        <a class="btn ghost" href="/api/export?id=${g.id}" download="hexfield-${g.id}.json" title="Save this game as a file">Export</a>
        <button class="btn ghost danger" data-do="delete" aria-label="Delete this game">Delete</button>
      </td></tr>`;
  }).join("");
  return `<div class="table-scroll"><table class="info-table games-table"><thead><tr><th>Last played</th><th>Players</th><th>State</th><th>Accuracy</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// accuracy game by game, and where the points go
function progressHTML(st) {
  const games = st.games;
  if (!games.length) return `<p class="coach-note">Finish a game and your progress shows up here: accuracy over time, and which kinds of decision cost you the most.</p>`;
  const rated = games.filter((g) => g.accuracy !== null);
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const recent = rated.slice(-5), before = rated.slice(-10, -5);
  const trend = recent.length && before.length ? mean(recent.map((g) => g.accuracy)) - mean(before.map((g) => g.accuracy)) : null;
  const W = 640, H = 150;
  const AX = 38; // room for the axis labels on the left
  const pts = sparkPoints(rated.map((g) => g.accuracy), W - AX, H, 40, 100).map(([x, y]) => [x + AX, y]);
  const chart = rated.length < 2 ? "" : `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="Accuracy in each of your last ${rated.length} games: ${rated.map((g) => Math.round(g.accuracy) + "%").join(", ")}">
      ${[50, 75, 100].map((v) => { const y = sparkPoints([v], W, H, 40, 100)[0][1]; return `<line class="grid" x1="${AX}" x2="${W}" y1="${y}" y2="${y}"/><text class="axis" x="4" y="${y + 3}">${v}%</text>`; }).join("")}
      <polyline class="line" points="${pts.map((p) => p.join(",")).join(" ")}"/>
      ${pts.map(([x, y], i) => `<circle class="${rated[i].won ? "won" : ""}" cx="${x}" cy="${y}" r="4"><title>${fmtWhen(rated[i].when)}: ${Math.round(rated[i].accuracy)}% accuracy, ${rated[i].won ? "won" : "lost"}</title></circle>`).join("")}
    </svg><p class="muted spark-key">Accuracy per game, oldest to newest. Filled dots are wins.</p>`;
  const groups = Object.entries(st.groups).sort((a, b) => b[1].lost - a[1].lost);
  const top = Math.max(0.001, ...groups.map(([, t]) => t.lost));
  const rows = groups.map(([k, t]) => `<tr><td>${esc(k)}</td><td class="num">${t.n}</td><td class="num">${Math.round(t.accuracy)}%</td>
      <td class="num">${t.per.toFixed(3)}</td><td class="num"><b>${t.lost.toFixed(1)}</b></td>
      <td class="bar-cell"><span class="bar"><i style="width:${(100 * t.lost) / top}%"></i></span></td></tr>`).join("");
  return `<div class="stat-tiles">
      <div><b>${games.length}</b><span>finished game${games.length === 1 ? "" : "s"}</span></div>
      <div><b>${Math.round((100 * st.won) / games.length)}%</b><span>won (${st.won})</span></div>
      <div><b>${rated.length ? Math.round(mean(rated.map((g) => g.accuracy))) + "%" : "–"}</b><span>average accuracy</span></div>
      <div><b>${rated.length ? mean(rated.map((g) => g.lost)).toFixed(1) : "–"}</b><span>fame-eq lost per game</span></div>
      ${trend === null ? "" : `<div><b class="${trend >= 0 ? "up" : "down"}">${trend >= 0 ? "+" : "−"}${Math.abs(trend).toFixed(0)}</b><span>last 5 games against the 5 before</span></div>`}
    </div>${chart}
    ${rows ? `<h3>Where the points go</h3>
    <div class="table-scroll"><table class="info-table"><thead><tr><th>Kind of decision</th><th>Decisions</th><th>Accuracy</th><th>Lost each</th><th>Lost in all</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="muted">Lost = fame-equivalents given up against the engine's best line, summed over your finished solo games. The top row is where practice pays most.</p>` : ""}`;
}

async function onGamesClick(e) {
  const t = e.target.closest(".tab");
  if (t) return openGames(t.id.replace("tab-", ""));
  const b = e.target.closest("[data-do]");
  if (!b) return;
  const id = b.closest("tr").dataset.id;
  try {
    if (b.dataset.do === "resume") {
      leaveReplay();
      const st = await api("resume", { id });
      $("#dlg-games").close();
      enter(st);
      if (S.phase === "game_over") G.overShown = S.gid;
      pump();
    } else if (b.dataset.do === "replay") {
      $("#dlg-games").close();
      await openReplay(id);
    } else if (b.dataset.do === "delete") {
      if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Really delete?"; return; }
      await api("delete", { id });
      if (S?.gid === id && !session.replay) { setSession(null, {}, null); location.search = ""; return; }
      openGames("saved");
    }
  } catch (err) {
    toast("Couldn't do that", err.message, "var(--worst)");
  }
}

async function importGame(file) {
  try {
    const record = JSON.parse(await file.text());
    await api("import", { record });
    toast("Game imported", "It is in your saved games.", "var(--good)");
    openGames("saved");
  } catch (e) {
    toast("Couldn't import that file", e.message, "var(--worst)");
  }
}

// ------------------------------------------------------------------ replay
let playing = null;   // autoplay timer
let live = null;      // the session to return to when the replay closes

export async function openReplay(id, k = 0) {
  if (!session.replay) live = { gid: session.gid, tokens: session.tokens, seat: session.seat };
  stopPlaying();
  for (const d of $$("dialog[open]")) d.close();
  session.replay = { id, k, seat: null, reveal: false };
  try {
    const st = await replayFetch(k, 0);
    enter(st);
    session.replay.seat = st.human;
    // start at the first decision rather than an empty board
    renderReplay();
  } catch (e) {
    session.replay = null;
    toast("Couldn't open that replay", e.message, "var(--worst)");
  }
}

function replayFetch(k, since) {
  const r = session.replay;
  const q = { id: r.id, k, since };
  if (r.seat !== null) q.seat = r.seat;
  if (r.reveal) q.reveal = 1;
  return api("replay", undefined, q);
}

// one move forward is animated like live play; anything else redraws the position
export async function replayGo(k, { redraw = false } = {}) {
  const r = session.replay;
  if (!r || !S?.replay || r.loading) return;
  k = Math.max(0, Math.min(S.replay.total, k));
  if (k === r.k && !redraw) return;
  r.loading = true;
  try {
    const forward = !redraw && k === r.k + 1;
    const st = await replayFetch(k, forward ? G.evCount : 0);
    if (!session.replay) return;
    if (!forward) st.reset_log = true;
    r.k = st.replay.k;
    apply(st, !forward);
  } catch (e) {
    stopPlaying();
    toast("Replay", e.message, "var(--worst)");
  } finally {
    r.loading = false;
  }
}

function stopPlaying() { clearInterval(playing); playing = null; }

function togglePlay() {
  if (playing) return void (stopPlaying(), renderReplay());
  if (S.replay.k >= S.replay.total) return void replayGo(0, { redraw: true });
  playing = setInterval(() => {
    if (!session.replay || S.replay.k >= S.replay.total) { stopPlaying(); renderReplay(); return; }
    replayGo(session.replay.k + 1);
  }, 650);
  renderReplay();
}

export function leaveReplay() {
  if (!session.replay) return false;
  stopPlaying();
  session.replay = null;
  $("#replay-bar").hidden = true;
  if (live) setSession(live.gid, live.tokens, live.seat);
  live = null;
  return true;
}

async function closeReplay() {
  leaveReplay();
  try {
    const st = await api("board");
    if (st.nogame || st.join) throw new Error("no live game");
    enter(st);
    if (S.phase === "game_over") G.overShown = S.gid;
    pump();
  } catch {
    openNewGame(false);
  }
}

async function branchHere() {
  const r = session.replay;
  try {
    const st = await api("branch", { id: r.id, entry: S.replay.entry });
    leaveReplay();
    enter(st);
    toast("Playing on from here", "A new game: same board and dice, your choices. The original is untouched.", "var(--good)", 5000);
    pump();
  } catch (e) {
    toast("Couldn't start from here", e.message, "var(--worst)");
  }
}

export function renderReplay() {
  const bar = $("#replay-bar");
  const rp = session.replay && S?.replay;
  bar.hidden = !rp;
  document.body.classList.toggle("replaying", !!rp);
  if (!rp) return;
  const { k, total, marks, decision } = rp;
  const slider = $("#rp-slider");
  slider.max = total;
  if (document.activeElement !== slider) slider.value = k;
  slider.setAttribute("aria-valuetext", `move ${k} of ${total}`);
  $("#rp-pos").textContent = `move ${k} of ${total} · ${S.round ? `round ${S.round}` : "setup"}`;
  $("#rp-first").disabled = $("#rp-prev").disabled = k === 0;
  $("#rp-last").disabled = $("#rp-next").disabled = k === total;
  $("#rp-prevd").disabled = !marks.some((m) => m.k < k);
  $("#rp-nextd").disabled = !marks.some((m) => m.k > k);
  $("#rp-play").textContent = playing ? "Pause" : k >= total ? "Restart" : "Play";
  $("#rp-reveal").checked = !!session.replay.reveal;
  // "Play on" only makes sense for a seat that can still move
  $("#rp-branch").hidden = S.phase === "game_over";
  // the decision made at this point, with the coach's verdict
  const out = $("#coach-out");
  clearGlow();
  if (!decision) { if (out.dataset.replay) { out.replaceChildren(); delete out.dataset.replay; } return; }
  const rv = decision.review;
  out.dataset.replay = "1";
  const name = S.players[S.human].name;
  const who = name === "You" ? "Your" : `${name}'s`;
  const html = rv ? `<div class="rp-decision v-${rv.verdict}">
      <p class="coach-note">${esc(who)} decision here</p>
      <p><span class="vd vd-${rv.verdict}">${symbol(rv.verdict)} ${rv.verdict}</span> <b>${esc(short(rv.label))}</b>${rv.delta >= 0.05 ? ` <span class="loss-n">−${rv.delta.toFixed(2)}</span>` : ""}</p>
      ${rv.forced ? "" : rv.delta < 0.05 ? `<p class="muted">The engine agrees.</p>` : `<p class="muted">Engine: ${esc(human(rv.best))}</p>`}
      <button class="btn ghost" id="rp-hint">What else was there?</button></div>`
    : `<div class="rp-decision"><p class="coach-note">${esc(who)} decision here</p><p><b>${esc(plain(decision.label)) || "a move"}</b> <span class="muted">(not reviewed)</span></p>
      <button class="btn ghost" id="rp-hint">What would the engine do?</button></div>`;
  if (setHTML(out, html)) out.dataset.replay = "1";
  if (rv && els.glow) {
    if (rv.best_action) glowFor(rv.best_action, true);
    if (rv.action && JSON.stringify(rv.action) !== JSON.stringify(rv.best_action)) {
      const before = els.glow.children.length;
      glowFor(rv.action, true);
      [...els.glow.children].slice(before).forEach((m) => m.classList.add("mine"));
    }
  }
}

async function replayHint() {
  const r = session.replay;
  const box = $("#coach-out");
  try {
    const h = (await api("replay_hint", undefined, { id: r.id, k: r.k, seat: r.seat })).hint;
    const list = (h.options || []).map((o, i) => `<li class="opt"><span class="loss ${i === 0 ? "best" : o.loss < 0.5 ? "near" : "far"}">${i === 0 ? "best" : "−" + o.loss.toFixed(2)}</span><span>${esc(human(o.text))}</span><span></span></li>`).join("");
    box.insertAdjacentHTML("beforeend", list ? `<ol class="opts">${list}</ol>` : `<p class="coach-note">Nothing to choose between here.</p>`);
    $("#rp-hint")?.remove();
  } catch (e) {
    toast("No hint here", e.message, "var(--muted)", 2500);
  }
}

// keys while a replay is on screen; true if the key was used
export function replayKey(e) {
  if (!session.replay || !S?.replay) return false;
  const k = session.replay.k, rp = S.replay;
  const jump = (dir) => {
    const ks = rp.marks.map((m) => m.k).filter((x) => (dir > 0 ? x > k : x < k));
    if (ks.length) replayGo(dir > 0 ? Math.min(...ks) : Math.max(...ks), { redraw: true });
  };
  switch (replayStep(e)) {
    case "next": stopPlaying(); replayGo(k + 1); break;
    case "prev": stopPlaying(); replayGo(k - 1, { redraw: true }); break;
    case "first": stopPlaying(); replayGo(0, { redraw: true }); break;
    case "last": stopPlaying(); replayGo(rp.total, { redraw: true }); break;
    case "nextd": stopPlaying(); jump(1); break;
    case "prevd": stopPlaying(); jump(-1); break;
    case "play": togglePlay(); break;
    case "close": closeReplay(); break;
    default: return false;
  }
  return true;
}

// ------------------------------------------------------------------ online: joining and the lobby
export function openJoin(st) {
  const dlg = $("#dlg-join");
  const list = st.players.map((p, i) => `<li><span class="dot" style="background:${pc(i)}"></span>${p.open ? `<i class="muted">open seat</i>` : esc(p.name)}${p.bot ? ` <span class="p-tag">bot</span>` : ""}</li>`).join("");
  $("#join-body").innerHTML = st.full
    ? `<p>This game is full, or you joined it from another browser.</p><ul class="seat-list">${list}</ul>`
    : `<p class="muted">You have been invited to a game.</p><ul class="seat-list">${list}</ul>`;
  $("#form-join").elements.name.closest("label").hidden = st.full;
  $("#join-ok").textContent = st.full ? "Start my own game" : "Join";
  $("#form-join").elements.name.required = !st.full;
  dlg.dataset.full = st.full ? "1" : "";
  modal(dlg);
}

async function onJoinClose() {
  const dlg = $("#dlg-join");
  if (dlg.dataset.full) { setSession(null, {}, null); history.replaceState(null, "", location.pathname); return openNewGame(false); }
  try {
    const st = await api("join", { name: $("#form-join").elements.name.value });
    enter(st);
    pump();
  } catch (e) {
    toast("Couldn't join", e.message, "var(--worst)");
    try { openJoin(await api("board")); } catch { openNewGame(false); }
  }
}

export function renderLobby() {
  const box = $("#lobby");
  const on = !!S?.lobby && !session.replay;
  box.hidden = !on;
  if (!on) return;
  const host = S.human === S.players.findIndex((p) => !p.bot);
  const origin = S.lan && ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) ? `http://${S.lan}:${location.port}` : location.origin;
  const link = `${origin}/?g=${S.gid}`;
  const reach = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) && !S.lan;
  setHTML(box, `<h2>Waiting for players</h2>
    <ul class="seat-list">${S.players.map((p, i) => `<li><span class="dot" style="background:${pc(i)}"></span>${p.open ? `<i class="muted">open seat…</i>` : esc(p.name)}${p.bot ? ` <span class="p-tag">bot</span>` : i === S.human ? ` <span class="p-tag">you</span>` : ""}</li>`).join("")}</ul>
    <p class="muted">Send the others this link:</p>
    <div class="invite"><input readonly value="${esc(link)}" aria-label="Invite link" id="invite-link"><button class="btn" id="invite-copy">Copy</button></div>
    ${reach ? `<p class="muted warn-note">Other devices can't reach this address. Stop the server and start it with <code>python3 -m hexfield.web --lan</code>.</p>` : ""}
    ${host ? `<div class="dlg-actions"><button class="btn primary" id="lobby-start" title="Bots take the seats nobody has joined">Start now with bots in the open seats</button></div>` : `<p class="muted">The game starts when every seat is taken.</p>`}`);
}

async function onLobbyClick(e) {
  if (e.target.id === "invite-copy") {
    const link = $("#invite-link").value;
    try { await navigator.clipboard.writeText(link); toast("Link copied", "", "var(--muted)", 1800); } catch { $("#invite-link").select(); }
  } else if (e.target.id === "lobby-start") {
    try { apply(await api("start", {})); pump(); } catch (err) { toast("Couldn't start", err.message, "var(--worst)"); }
  }
}

// ------------------------------------------------------------------ pass and play
// The screen goes dark between two people: the next player confirms before their cards show.
export async function passDevice(seat) {
  const dlg = $("#dlg-pass");
  const name = S.players[seat].name;
  for (const d of $$("dialog[open]")) if (d !== dlg) d.close();
  $("#pass-title").textContent = `Pass the screen to ${name}`;
  $("#pass-sub").textContent = S.pending_offer || (S.my_offer && S.actor !== seat) ? `${name} has a trade offer to answer.`
    : S.phase === "discard" ? `${name} has to discard.` : S.phase === "special_build" ? `${name} may build.` : `It is ${name}'s move.`;
  $("#pass-ok").textContent = `I am ${name}`;
  $("#pass-ok").style.setProperty("--pc", pc(seat));
  document.body.classList.add("curtained");
  modal(dlg);
  await new Promise((resolve) => { $("#pass-ok").onclick = resolve; });
  setSession(session.gid, session.tokens, seat);
  try {
    G.evCount = 0;
    const st = await api("board");
    enter(st);
  } catch (e) {
    toast("Server error", e.message, "var(--worst)");
    await sleep(1000);
  }
  dlg.close();
  document.body.classList.remove("curtained");
}

// ------------------------------------------------------------------ wiring
export function wireGames() {
  $("#btn-games").onclick = () => openGames();
  $("#new-saved").onclick = () => { $("#dlg-new").close("saved"); openGames("saved"); };
  $("#dlg-games").addEventListener("click", onGamesClick);
  $("#games-new").onclick = () => { $("#dlg-games").close(); openNewGame(!!S); };
  $("#dlg-games").addEventListener("close", () => { if (!S && !$("#dlg-new").open && !session.replay) openNewGame(false); });
  $("#games-import").onchange = (e) => { if (e.target.files[0]) importGame(e.target.files[0]); e.target.value = ""; };
  $("#form-join").addEventListener("submit", () => setTimeout(onJoinClose));
  $("#dlg-join").addEventListener("cancel", (e) => e.preventDefault());
  $("#dlg-pass").addEventListener("cancel", (e) => e.preventDefault());
  $("#lobby").addEventListener("click", onLobbyClick);
  const go = (fn) => () => { stopPlaying(); fn(); };
  $("#rp-first").onclick = go(() => replayGo(0, { redraw: true }));
  $("#rp-last").onclick = go(() => replayGo(S.replay.total, { redraw: true }));
  $("#rp-prev").onclick = go(() => replayGo(session.replay.k - 1, { redraw: true }));
  $("#rp-next").onclick = go(() => replayGo(session.replay.k + 1));
  $("#rp-prevd").onclick = () => replayKey({ key: "[" });
  $("#rp-nextd").onclick = () => replayKey({ key: "]" });
  $("#rp-play").onclick = togglePlay;
  $("#rp-close").onclick = closeReplay;
  $("#rp-branch").onclick = branchHere;
  $("#rp-slider").oninput = (e) => { stopPlaying(); replayGo(Number(e.target.value), { redraw: true }); };
  $("#rp-reveal").onchange = (e) => { session.replay.reveal = e.target.checked; replayGo(session.replay.k, { redraw: true }); };
  $("#coach-out").addEventListener("click", (e) => { if (e.target.id === "rp-hint") replayHint(); });
}

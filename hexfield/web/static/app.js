// Hexfield — browser client. Talks to hexfield/web/server.py; no dependencies.

const RES = ["wood", "brick", "sheep", "wheat", "ore"];
const COSTS = { road: [1, 1, 0, 0, 0], settle: [1, 1, 1, 1, 0], city: [0, 0, 0, 2, 3], dev: [0, 0, 1, 1, 1] };
const DEVS = [
  { key: "knight", name: "Knight", icon: "i-knight", play: "play_knight" },
  { key: "vp", name: "Victory pt", icon: "i-vp", play: null },
  { key: "roadb", name: "Road bldg", icon: "i-roadb", play: "play_road" },
  { key: "mono", name: "Monopoly", icon: "i-mono", play: "play_mono" },
  { key: "yop", name: "Plenty", icon: "i-yop", play: "play_yop" },
];
const VERDICT_COLORS = { forced: "var(--muted)", best: "var(--good)", good: "var(--ok)", inaccuracy: "var(--warn)", mistake: "var(--bad)", blunder: "var(--worst)" };
const LOG_ICONS = { build: "⌂", roll: "⚄", produce: "+", robber: "☗", steal: "✋", dev: "★", trade: "⇄", award: "♛",
  discard: "−", offer: "?", reply: "·", info: "·", win: "♛", private: "👁", private_steal: "👁" };
const SPEEDS = { fast: 60, normal: 380, slow: 900 };
const SVGNS = "http://www.w3.org/2000/svg";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pc = (p) => `var(--p${p})`;
const icon = (id, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
const resIcon = (r, cls = "ri") => `<svg class="${cls} r-${RES[r]}" aria-hidden="true"><use href="#i-${RES[r]}"/></svg>`;

function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function store(key, fallback) {
  try { const v = localStorage.getItem("hexfield." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
function save(key, v) { try { localStorage.setItem("hexfield." + key, JSON.stringify(v)); } catch { /* private mode */ } }

// ------------------------------------------------------------------ state
let S = null;          // latest server view
let B = null;          // board geometry
let evCount = 0;       // events received so far
let prev = null;       // previous S for diffing
let mode = null;       // main-phase build mode: road | settle | city
let busy = false;
let pumping = false;
let reviewsSeen = 0;
let logStick = true;
let holdAutoRoll = false; // set by undo so it can step back past a roll
let pollTimer = null;
let overShown = null;
let hintKey = null;
const prefs = { speed: store("speed", "normal"), autoroll: store("autoroll", true), reasons: store("reasons", true) };
const els = {};        // svg element caches

// ------------------------------------------------------------------ api
async function api(route, body) {
  const q = route === "new" ? "" : `?since=${evCount}`;
  const res = await fetch(`/api/${route}${q}`, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.data = data;
    throw err;
  }
  return data;
}

async function act(action) {
  if (busy) return;
  busy = true;
  holdAutoRoll = false;
  try {
    const st = await api("act", { action });
    apply(st);
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  } finally {
    busy = false;
    render();
  }
  pump();
}

// ------------------------------------------------------------------ boot
async function boot() {
  wireUI();
  try {
    const st = await api("board");
    if (st.nogame) return openNewGame(false);
    B = st.board;
    buildBoard();
    evCount = 0;
    reviewsSeen = st.reviews.length;
    apply(st, true);
    if (S.phase === "game_over") overShown = S.seed;
    pump();
  } catch (e) {
    openNewGame(false);
  }
}

async function newGame(opts) {
  const st = await api("new", opts);
  B = st.board;
  evCount = 0;
  prev = null;
  mode = null;
  reviewsSeen = 0;
  overShown = null;
  hintKey = null;
  $("#log").replaceChildren();
  $("#coach-out").replaceChildren();
  buildBoard();
  apply(st, true);
  pump();
}

// ------------------------------------------------------------------ apply
function apply(st, initial = false) {
  // a response from a game that has since been replaced (e.g. a slow /step racing /new)
  if (!initial && S && st.gid && S.gid && st.gid !== S.gid) return;
  if (st.reset_log || st.ev_total < evCount) {
    $("#log").replaceChildren();
    evCount = 0;
    initial = true; // don't replay effects after an undo
  }
  const fresh = st.events.filter((e) => e.i >= evCount);
  prev = S;
  S = st;
  evCount = st.ev_total;
  appendLog(fresh);
  if (!initial) effects(fresh);
  if (mode && !(S.legal[mode] || []).length) mode = null;
  const key = `${S.turn}|${S.phase}|${S.actor}|${S.ev_total}`;
  if (hintKey && key !== hintKey) { $("#coach-out").replaceChildren(); hintKey = null; clearGlow(); }
  render();
  if (logStick) { const log = $("#log"); log.scrollTop = log.scrollHeight; }
  pollReviews();
  newReviews();
  if (S.phase === "game_over" && overShown !== S.seed) {
    overShown = S.seed;
    setTimeout(showGameOver, initial ? 0 : 900);
  }
}

const myDecision = () => S && S.phase !== "game_over" && S.actor === S.human && !S.pending_offer;
const legal = () => (myDecision() ? S.legal : {});
const can = (kind) => (legal().kinds || []).includes(kind);

// ------------------------------------------------------------------ bot loop
async function pump() {
  if (pumping || !S) return;
  pumping = true;
  try {
    while (S && S.phase !== "game_over" && S.actor !== S.human && !S.pending_offer && !$("#dlg-new").open) {
      setThinking(S.actor);
      let st;
      try {
        st = await api("step", {});
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
    pumping = false;
    setThinking(null);
    render();
  }
  if (S && S.pending_offer) openBotOffer();
  if (S && S.discard_owed && myDecision()) openDiscard();
  maybeAutoRoll();
}

function pollReviews() {
  if (pollTimer || !S || !S.reviewing) return;
  pollTimer = setTimeout(async () => {
    pollTimer = null;
    if (busy || pumping) return pollReviews();
    try { apply(await api("state")); } catch { /* next action will refresh */ }
  }, 900);
}

function maybeAutoRoll() {
  if (holdAutoRoll || !prefs.autoroll || !myDecision() || S.phase !== "pre_roll") return;
  const kinds = S.legal.kinds || [];
  if (kinds.length === 1 && kinds[0] === "roll") {
    const turn = S.turn;
    setTimeout(() => { if (S.turn === turn && S.phase === "pre_roll" && myDecision()) act(["roll"]); }, 350);
  }
}

function setThinking(p) {
  $$(".player").forEach((el, i) => el.classList.toggle("thinking", i === p));
  renderStatus(p);
}

// ------------------------------------------------------------------ board
function buildBoard() {
  const board = $("#board");
  board.replaceChildren();
  const R = B.radius;
  board.setAttribute("viewBox", "-395 -345 790 690");
  const defs = svg("defs", {}, board);
  defs.innerHTML = `
    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="1.6" stdDeviation="1.4" flood-color="#000" flood-opacity=".5"/></filter>
    <filter id="tileshadow" x="-10%" y="-10%" width="120%" height="120%"><feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity=".45"/></filter>
    <radialGradient id="ocean" cx="50%" cy="45%" r="60%"><stop offset="0" stop-color="#1b5e8f"/><stop offset=".7" stop-color="#11406a"/><stop offset="1" stop-color="#0a2a47"/></radialGradient>
    <pattern id="waves" width="60" height="26" patternUnits="userSpaceOnUse"><path d="M0 13 q15 -9 30 0 t30 0" fill="none" stroke="#fff" stroke-opacity=".06" stroke-width="2"/></pattern>
    ${terrainGradient("wood", "#3fa36a", "#2a7a4b", "#1c5434")}
    ${terrainGradient("brick", "#d9774f", "#b9563a", "#843a26")}
    ${terrainGradient("sheep", "#a8d86d", "#86bd4f", "#5c8c32")}
    ${terrainGradient("wheat", "#f6d164", "#e2b22f", "#b3861a")}
    ${terrainGradient("ore", "#a8b2c3", "#8591a6", "#5d677a")}
    ${terrainGradient("desert", "#eadbb3", "#d6c091", "#ad9566")}
  `;
  // ocean
  const oc = [];
  for (let i = 0; i < 6; i++) oc.push(`${(Math.cos((Math.PI / 3) * i) * 375).toFixed(1)},${(Math.sin((Math.PI / 3) * i) * 375).toFixed(1)}`);
  svg("polygon", { points: oc.join(" "), fill: "url(#ocean)", stroke: "#0a2a47", "stroke-width": 26, "stroke-linejoin": "round" }, board);
  svg("polygon", { points: oc.join(" "), fill: "url(#waves)" }, board);

  const corners = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    corners.push([Math.cos(a) * R, Math.sin(a) * R]);
  }
  const hexPts = (cx, cy, k = 1) => corners.map(([x, y]) => `${(cx + x * k).toFixed(2)},${(cy + y * k).toFixed(2)}`).join(" ");

  // shallows and beach: oversized hexes merge into a coastline
  const gCoast = svg("g", { class: "coast" }, board);
  for (const h of B.hexes) svg("polygon", { points: hexPts(h.x, h.y, 1.3), class: "shallow" }, gCoast);
  for (const h of B.hexes) svg("polygon", { points: hexPts(h.x, h.y, 1.1), class: "beach" }, gCoast);

  // ports (under the tiles so planks tuck in)
  const gPorts = svg("g", { class: "ports" }, board);
  for (const port of B.ports) {
    const [a, b] = port.v.map((v) => B.vertices[v]);
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const len = Math.hypot(mx, my);
    const bx = mx + (mx / len) * 34, by = my + (my / len) * 34;
    for (const v of [a, b]) svg("line", { class: "port-plank", x1: v.x + (bx - v.x) * 0.15, y1: v.y + (by - v.y) * 0.15, x2: bx, y2: by }, gPorts);
    const g = svg("g", { class: "port-badge", transform: `translate(${bx.toFixed(1)} ${by.toFixed(1)})` }, gPorts);
    svg("circle", { r: 17 }, g);
    if (port.kind === "any") {
      const t = svg("text", { y: 4 }, g); t.textContent = "3:1";
    } else {
      const r = RES.indexOf(port.kind);
      svg("use", { href: `#i-${port.kind}`, x: -7, y: -14, width: 14, height: 14, class: `ri r-${RES[r]}`, style: `fill:var(--${port.kind})` }, g);
      const t = svg("text", { y: 11 }, g); t.textContent = "2:1";
    }
    const title = svg("title", {}, g);
    title.textContent = port.kind === "any" ? "3:1 port — any resource" : `2:1 ${port.kind} port`;
  }

  // tiles
  const gTiles = svg("g", { class: "tiles", filter: "url(#tileshadow)" }, board);
  els.hex = B.hexes.map((h, i) => {
    const g = svg("g", { class: "hex-tile", "data-h": i }, gTiles);
    const res = h.res || "desert";
    svg("polygon", { class: "tile", points: hexPts(h.x, h.y, 0.985), fill: `url(#g-${res})` }, g);
    const art = svg("g", { class: "art" }, g);
    const spots = h.res ? [[-27, -27, 19], [27, -27, 19], [0, -44, 15]] : [[0, -6, 34]];
    for (const [dx, dy, s] of spots) {
      svg("use", { href: `#i-${res}`, x: h.x + dx - s / 2, y: h.y + dy - s / 2, width: s, height: s, fill: shade(res) }, art);
    }
    if (h.num) {
      const tk = svg("g", { class: "token" + (h.num === 6 || h.num === 8 ? " hot" : ""), transform: `translate(${h.x} ${h.y + 6})` }, g);
      svg("circle", { class: "disc", r: 19 }, tk);
      const t = svg("text", { y: -3 }, tk); t.textContent = h.num;
      for (let k = 0; k < h.pips; k++) svg("circle", { class: "pip", r: 1.7, cx: (k - (h.pips - 1) / 2) * 4.6, cy: 10 }, tk);
    }
    const lb = svg("text", { class: "hex-label", x: h.x, y: h.y + 45 }, g); lb.textContent = h.label;
    return g;
  });

  els.roads = svg("g", { class: "roads" }, board);
  els.pieces = svg("g", { class: "pieces" }, board);
  els.robber = svg("g", { class: "robber" }, board);
  els.robber.innerHTML = `<path d="M0-17a7 7 0 0 1 7 7c0 2.6-1.4 4.6-3.3 5.8C7.6-2.6 10 1.8 10 7v6c0 2-1.5 3-4 3H-6c-2.5 0-4-1-4-3V7c0-5.2 2.4-9.6 6.3-11.2A6.8 6.8 0 0 1-7-10a7 7 0 0 1 7-7z" filter="url(#shadow)"/><circle class="eye" cx="-2.4" cy="-10" r="1.2"/><circle class="eye" cx="2.4" cy="-10" r="1.2"/>`;
  els.targets = svg("g", { class: "targets" }, board);
  els.glow = svg("g", { class: "glow" }, board);
  els.fx = svg("g", { class: "fx" }, board);
  els.roadEls = {};
  els.pieceEls = {};
  els.hexPts = hexPts;
}

function terrainGradient(id, hi, mid, lo) {
  return `<radialGradient id="g-${id}" cx="38%" cy="30%" r="80%"><stop offset="0" stop-color="${hi}"/><stop offset=".55" stop-color="${mid}"/><stop offset="1" stop-color="${lo}"/></radialGradient>`;
}
function shade(res) {
  return { wood: "#174a2c", brick: "#7a3220", sheep: "#f4fbe9", wheat: "#9a6f10", ore: "#3e4757", desert: "#a08350" }[res];
}

function ripple(p, owner) {
  const c = svg("circle", { class: "ripple", cx: p.x, cy: p.y, r: 10, style: `--pc:${pc(owner)}` }, els.fx);
  setTimeout(() => c.remove(), 1300);
}

function roadEl(e, owner) {
  const [a, b] = B.edges[e].map((v) => B.vertices[v]);
  const k = 0.16;
  const x1 = a.x + (b.x - a.x) * k, y1 = a.y + (b.y - a.y) * k, x2 = b.x - (b.x - a.x) * k, y2 = b.y - (b.y - a.y) * k;
  const g = svg("g", { class: "road", style: `--pc:${pc(owner)}` });
  svg("line", { class: "under", x1, y1, x2, y2 }, g);
  svg("line", { class: "top", x1, y1, x2, y2 }, g);
  svg("line", { class: "shine", x1, y1: y1 - 1.5, x2, y2: y2 - 1.5 }, g);
  return g;
}

function pieceEl(v, owner, level) {
  const { x, y } = B.vertices[v];
  const g = svg("g", { class: "piece", style: `--pc:${pc(owner)}`, transform: `translate(${x} ${y}) scale(1.15)` });
  const inner = svg("g", { class: "pop" }, g);
  if (level === 1) {
    svg("path", { d: "M-10 9V-2L0-11l10 9V9z" }, inner);
    svg("path", { class: "roof", d: "M-10-2 0-11l10 9-2 1.6L0-8l-8 7.6z" }, inner);
  } else {
    svg("path", { d: "M-16 11V-1h8v-6l7.5-7 7.5 7V-1h9v12z" }, inner);
    svg("path", { class: "roof", d: "M-8-7l7.5-7 7.5 7-1.8 1.4L-.5-11-6.2-5.6z" }, inner);
  }
  return g;
}

// ------------------------------------------------------------------ render
function render() {
  if (!S || !B) return;
  // forced decisions: keep their dialog open even if it was dismissed
  const owe = myDecision() && S.phase === "discard" && S.discard_owed > 0;
  $("#btn-discard").hidden = !owe;
  $("#btn-answer").hidden = !S.pending_offer;
  if (!busy && !pumping) {
    if (owe && !$("#dlg-discard").open) queueMicrotask(openDiscard);
    if (S.pending_offer && !$("#dlg-botoffer").open) queueMicrotask(openBotOffer);
  }
  if ($("#dlg-trade").open) renderTrade();
  renderPieces();
  renderTargets();
  renderPlayers();
  renderHand();
  renderActions();
  renderStatus(pumping ? S.actor : null);
  renderDice(false);
  renderBank();
  renderReviews();
  $("#seed").textContent = `seed ${S.seed}`;
  $("#btn-undo").disabled = !S.can_undo || pumping || busy;
  $("#review-on").checked = S.review_on;
  $("#reviewing").hidden = !S.reviewing;
}

function renderPieces() {
  for (let e = 0; e < S.e_owner.length; e++) {
    const o = S.e_owner[e];
    const cur = els.roadEls[e];
    if (o === -1) { if (cur) { cur.remove(); delete els.roadEls[e]; } continue; }
    if (cur && cur.dataset.o === String(o)) continue;
    if (cur) cur.remove();
    const g = roadEl(e, o);
    g.dataset.o = o;
    if (prev) g.classList.add("pop");
    els.roads.appendChild(g);
    els.roadEls[e] = g;
  }
  for (let v = 0; v < S.v_owner.length; v++) {
    const o = S.v_owner[v], lv = S.v_level[v];
    const cur = els.pieceEls[v];
    const key = `${o}:${lv}`;
    if (o === -1) { if (cur) { cur.remove(); delete els.pieceEls[v]; } continue; }
    if (cur && cur.dataset.k === key) continue;
    if (cur) cur.remove();
    const g = pieceEl(v, o, lv);
    g.dataset.k = key;
    if (!prev) g.firstChild.classList.remove("pop");
    else if (o !== S.human) ripple(B.vertices[v], o);
    els.pieces.appendChild(g);
    els.pieceEls[v] = g;
  }
  const h = B.hexes[S.robber];
  els.robber.style.transform = `translate(${h.x + 30}px, ${h.y + 8}px)`;
  els.hex.forEach((g, i) => g.classList.toggle("blocked", i === S.robber));
}

// Everything you can build right now is clickable on the board; the build buttons
// only narrow the highlights down to one kind.
function targetsNow() {
  const L = legal();
  const t = { v: [], e: [], h: [], quick: false };
  if (!myDecision()) return t;
  const verts = (kind) => (L[kind] || []).map((v) => [v, kind]);
  switch (S.phase) {
    case "setup_settlement": t.v = verts("settle"); break;
    case "setup_road": case "road_building": t.e = L.road || []; break;
    case "move_robber": t.h = Object.keys(L.robber || {}).map(Number); break;
    case "main":
      t.quick = !mode;
      if (!mode || mode === "road") t.e = L.road || [];
      if (!mode || mode === "settle") t.v.push(...verts("settle"));
      if (!mode || mode === "city") t.v.push(...verts("city"));
      break;
  }
  return t;
}

function renderTargets() {
  const g = els.targets;
  g.replaceChildren();
  const t = targetsNow();
  $("#board").classList.toggle("robbing", t.h.length > 0);
  g.classList.toggle("quick", t.quick);
  for (const h of t.h) {
    const hx = B.hexes[h];
    svg("polygon", { class: "hex-target", "data-h": h, points: els.hexPts(hx.x, hx.y, 0.9) }, g);
  }
  for (const e of t.e) {
    const [a, b] = B.edges[e].map((v) => B.vertices[v]);
    const k = 0.2;
    const at = { x1: a.x + (b.x - a.x) * k, y1: a.y + (b.y - a.y) * k, x2: b.x - (b.x - a.x) * k, y2: b.y - (b.y - a.y) * k };
    const tg = svg("g", { class: "e-target", "data-e": e }, g);
    svg("line", { class: "vis", ...at }, tg);
    svg("line", { class: "hit", ...at }, tg);
  }
  for (const [v, kind] of t.v) {
    const p = B.vertices[v];
    const tg = svg("g", { class: `v-target k-${kind}`, "data-v": v, "data-kind": kind }, g);
    svg("circle", { class: "hit", cx: p.x, cy: p.y, r: 16 }, tg);
    svg("circle", { class: "ring", cx: p.x, cy: p.y, r: kind === "city" ? 16 : 8 }, tg);
  }
}

function renderPlayers() {
  const box = $("#players");
  if (box.children.length !== S.n) {
    box.replaceChildren(...S.players.map((_, p) => {
      const d = document.createElement("article");
      d.className = "player";
      d.style.setProperty("--pc", pc(p));
      return d;
    }));
  }
  S.players.forEach((pl, p) => {
    const el = box.children[p];
    el.classList.toggle("active", p === S.current && S.phase !== "game_over");
    el.classList.toggle("me", p === S.human);
    const hid = p === S.human && pl.hidden_vp ? `<span class="hid" title="includes ${pl.hidden_vp} hidden victory-point card(s)">★${pl.hidden_vp}</span>` : "";
    const badges = [];
    if (S.longest === p) badges.push(`<span class="badge">${icon("i-longest")}Longest road</span>`);
    if (S.army === p) badges.push(`<span class="badge">${icon("i-army")}Largest army</span>`);
    const owe = S.discards_pending[p] ? ` · discarding ${S.discards_pending[p]}` : "";
    const html = `
      <div class="p-head">
        <span class="p-name">${esc(pl.name)}</span>
        <span class="p-tag">${pl.bot ? esc(S.level) : "you"}</span>
        <span class="p-vp">${hid}<b>${pl.vp}</b><small>VP</small></span>
      </div>
      <div class="p-stats">
        <span title="Resource cards${pl.cards > 7 ? " — over 7, exposed to a 7" : ""}" class="${pl.cards > 7 ? "warn" : ""}">${icon("i-card")}${pl.cards}</span>
        <span title="Development cards">${icon("i-dev")}${pl.devs}</span>
        <span title="Knights played">${icon("i-knight")}${pl.knights}</span>
        <span title="Longest road length">${icon("i-longest")}${pl.road}</span>
      </div>
      ${badges.length || owe ? `<div class="p-badges">${badges.join("")}${owe ? `<span class="muted" style="font-size:12px">${owe}</span>` : ""}</div>` : ""}
      <div class="p-think"><div class="spinner" style="--pc:${pc(p)}"></div></div>`;
    if (el.dataset.html !== html) {
      el.dataset.html = html;
      el.innerHTML = html;
    }
  });
}

function renderHand() {
  const hand = $("#hand");
  if (!hand.children.length) {
    hand.replaceChildren(...RES.map((r) => {
      const d = document.createElement("div");
      d.className = `rcard r-${r}`;
      d.innerHTML = `${icon("i-" + r)}<span class="n"></span><span class="lbl">${r}</span>`;
      return d;
    }));
  }
  RES.forEach((r, i) => {
    const c = hand.children[i];
    const n = S.hand[i];
    c.querySelector(".n").textContent = n;
    c.classList.toggle("zero", n === 0);
    c.title = `${n} ${r}`;
  });
  const dh = $("#devhand");
  const cards = [];
  DEVS.forEach((d, k) => {
    const play = d.play && can(d.play);
    if (S.devs[k]) cards.push(devCard(d, k, S.devs[k], play, false));
    if (S.new_devs[k]) cards.push(devCard(d, k, S.new_devs[k], false, true));
  });
  const html = cards.join("");
  if (dh.dataset.html !== html) { dh.dataset.html = html; dh.innerHTML = html; }
}

function devCard(d, k, n, playable, isNew) {
  const tip = isNew ? `${d.name} — bought this turn, playable next turn` : playable ? `Play ${d.name}` : d.key === "vp" ? "Victory point (counts automatically)" : `${d.name}${S.dev_played ? " — one dev card per turn" : ""}`;
  return `<button class="dcard ${d.key === "vp" ? "vp" : ""} ${playable ? "playable" : ""} ${isNew ? "new" : ""}" data-dev="${k}" ${playable ? "" : "tabindex=-1"} title="${esc(tip)}">${icon(d.icon)}<span class="lbl">${d.name}</span>${n > 1 ? `<span class="n">${n}</span>` : ""}</button>`;
}

function renderActions() {
  const mine = myDecision();
  const pre = mine && S.phase === "pre_roll";
  const main = mine && S.phase === "main";
  const roll = $("#btn-roll");
  roll.disabled = !can("roll") || busy;
  roll.classList.toggle("pulse", pre);
  const set = (id, ok, m) => {
    const b = $(id);
    b.disabled = !ok || busy;
    if (m) b.setAttribute("aria-pressed", mode === m ? "true" : "false");
  };
  set("#btn-road", main && can("road"), "road");
  set("#btn-settle", main && can("settle"), "settle");
  set("#btn-city", main && can("city"), "city");
  set("#btn-dev", main && can("buy_dev"));
  const why = (kind, cost, base) => {
    if (!mine) return `${base} — not your turn`;
    if (S.phase !== "main") return `${base} — ${S.phase === "pre_roll" ? "roll first" : "finish the current step first"}`;
    const miss = cost.map((n, r) => Math.max(0, n - S.hand[r]));
    if (miss.some(Boolean)) return `${base} — need ${miss.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(", ")}`;
    if (kind && !can(kind)) return `${base} — ${kind === "buy_dev" ? "deck is empty" : kind === "city" ? "no settlement to upgrade" : "no legal spot (extend your roads)"}`;
    return base;
  };
  $("#btn-road").title = why("road", COSTS.road, "Build a road (1)");
  $("#btn-settle").title = why("settle", COSTS.settle, "Build a settlement (2)");
  $("#btn-city").title = why("city", COSTS.city, "Upgrade to a city (3)");
  $("#btn-dev").title = why("buy_dev", COSTS.dev, "Buy a development card (D)");
  $("#btn-roll").title = can("roll") ? "Roll the dice (R)" : mine && S.phase === "main" ? "Already rolled" : "Roll the dice — not now";
  set("#btn-trade", main);
  for (const [id, kind] of [["#btn-road", "road"], ["#btn-settle", "settle"], ["#btn-city", "city"], ["#btn-dev", "buy_dev"]]) {
    $(id).classList.toggle("ready", main && can(kind));
  }
  const end = $("#btn-end");
  end.disabled = !can("end") || busy;
  end.classList.toggle("pulse", main && (S.legal.kinds || []).every((k) => k === "end" || k === "bank"));
}

function renderStatus(thinker) {
  let text, p = S.current, mine = false, spin = false;
  const name = (q) => esc(S.players[q].name);
  const L = S.legal || {};
  if (S.phase === "game_over") {
    text = S.winner === S.human ? "You win! ♛" : `${name(S.winner)} wins`;
    p = S.winner;
  } else if (S.pending_offer) {
    text = `${name(S.pending_offer.from)} offers you a trade`; p = S.pending_offer.from; mine = true;
  } else if (S.actor === S.human) {
    mine = true; p = S.human;
    switch (S.phase) {
      case "setup_settlement": text = `Place your ${S.setup_idx < S.n ? "first" : "second"} settlement`; break;
      case "setup_road": text = "Place a road next to it"; break;
      case "pre_roll": text = (L.kinds || []).length > 1 ? "Roll the dice — or play a development card first" : "Roll the dice"; break;
      case "main": text = mode ? `Showing only ${mode === "settle" ? "settlements" : mode === "city" ? "cities" : "roads"} · Esc to show everything` : (["road", "settle", "city"].some(can) ? "Click a glowing spot to build — or trade, buy a card, end turn" : "Trade, buy a card, or end your turn"); break;
      case "discard": text = `Discard ${S.discard_owed} cards`; break;
      case "move_robber": text = "Move the robber — click a hex"; break;
      case "road_building": text = `Place a free road (${S.free_roads} left)`; break;
      default: text = S.phase;
    }
  } else {
    p = thinker ?? S.actor;
    spin = thinker !== null && thinker !== undefined;
    text = S.phase === "discard" ? `${name(p)} is discarding…` : S.phase.startsWith("setup") ? `${name(p)} is placing…` : `${name(p)} is thinking…`;
  }
  const html = `<div class="pill ${mine ? "mine" : ""}" style="--pc:${pc(p)}">${spin ? `<span class="spinner"></span>` : `<span class="dot"></span>`}<span>${text}</span><span class="muted" style="font-weight:500">· turn ${S.turn}</span></div>`;
  const st = $("#status");
  if (st.dataset.html !== html) { st.dataset.html = html; st.innerHTML = html; }
}

const PIP_LAYOUT = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
function renderDice(animate) {
  const box = $("#dice");
  const r = S.last_roll;
  if (!r) { box.replaceChildren(); return; }
  const [d1, d2, total] = r;
  const die = (n, red) => `<div class="die ${red ? "red" : ""}">${Array.from({ length: 9 }, (_, i) => `<i class="${(PIP_LAYOUT[n] || []).includes(i) ? "on" : ""}"></i>`).join("")}</div>`;
  const html = (d1 ? die(d1, false) + die(d2, true) : "") + `<span class="total ${total === 7 ? "seven" : ""}">${total}</span>`;
  if (box.dataset.html !== html || animate) {
    box.dataset.html = html;
    box.innerHTML = html;
    if (animate) { box.classList.remove("rolling"); void box.offsetWidth; box.classList.add("rolling"); }
  }
}

function renderBank() {
  const html = RES.map((r, i) => `<div class="bk" title="${S.bank[i]} ${r} in the bank">${resIcon(i)}${S.bank[i]}</div>`).join("") +
    `<div class="bk" title="${S.deck} development cards left">${icon("i-dev", "ri")}${S.deck}</div>`;
  const b = $("#bank");
  if (b.dataset.html !== html) { b.dataset.html = html; b.innerHTML = html; }
}

function renderCosts() {
  const row = (label, cost) => `<div class="cost-row"><span>${label}</span><span class="res-icons">${cost.flatMap((n, r) => Array(n).fill(resIcon(r))).join("")}</span></div>`;
  $("#costs").innerHTML = row("Road", COSTS.road) + row("Settlement", COSTS.settle) + row("City", COSTS.city) + row("Dev card", COSTS.dev) +
    `<div class="cost-row muted" style="font-size:12px">1 VP · 2 VP for a city · first to 10 wins</div>`;
  for (const [id, key] of [["#btn-road", "road"], ["#btn-settle", "settle"], ["#btn-city", "city"], ["#btn-dev", "dev"]]) {
    const b = $(id);
    const chips = COSTS[key].flatMap((n, r) => Array(n).fill(`<i style="background:var(--${RES[r]})"></i>`)).join("");
    b.insertAdjacentHTML("beforeend", `<span class="cost">${chips}</span>`);
  }
}

// ------------------------------------------------------------------ log
function appendLog(evs) {
  const log = $("#log");
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40 || !log.children.length;
  logStick = stick;
  const frag = document.createDocumentFragment();
  for (const e of evs) {
    const li = document.createElement("li");
    li.className = `k-${e.kind}`;
    if (e.p !== null && e.p !== undefined) li.style.setProperty("--pc", pc(e.p));
    let msg = e.msg;
    const m = msg.match(/ (?:at|upgrades) (\d+)/);
    if (e.kind === "build" && m) {
      const id = Number(m[1]);
      if (/road/.test(msg)) li.dataset.e = id; else li.dataset.v = id;
      msg = msg.replace(/ at \d+/, "").replace(/upgrades? \d+ to/, (s) => s.replace(/ \d+/, ""));
    }
    const rm = msg.match(/robber to ([A-S])$/);
    if (rm) li.dataset.h = rm[1].charCodeAt(0) - 65;
    if (e.kind === "turn") {
      li.textContent = msg.replace(/^-+ | -+$/g, "");
    } else {
      li.innerHTML = `<span class="ic">${LOG_ICONS[e.kind] || "·"}</span><span>${esc(msg)}</span>${e.why ? `<span class="why">${esc(plain(e.why))}</span>` : ""}`;
    }
    frag.appendChild(li);
  }
  log.appendChild(frag);
  while (log.children.length > 500) log.firstChild.remove();
  if (stick) log.scrollTop = log.scrollHeight;
}

// ------------------------------------------------------------------ effects
function effects(evs) {
  for (const e of evs) {
    if (e.kind === "roll" && S.last_roll) {
      renderDice(true);
      const total = S.last_roll[2];
      if (total !== 7) {
        B.hexes.forEach((h, i) => {
          if (h.num === total && i !== S.robber) flash(els.hex[i], "produce", 1300);
        });
      }
    }
    if (e.kind === "produce" || e.kind === "discard") {
      const n = [...e.msg.matchAll(/(\d+) (wood|brick|sheep|wheat|ore)/g)];
      const total = n.reduce((a, m) => a + Number(m[1]), 0);
      if (e.p !== null && total) floatDelta(e.p, e.kind === "discard" ? -total : total);
      if (e.p === S.human && e.kind === "produce") {
        for (const m of n) flash($("#hand").children[RES.indexOf(m[2])], "bump", 500);
      }
    }
    if (e.kind === "steal" || e.kind === "private_steal") {
      S.players.forEach((pl, q) => {
        if (q !== e.p && (e.msg.endsWith(pl.name) || e.msg.endsWith(pl.name + ")"))) flash($("#players").children[q], "shake", 450);
      });
    }
    if (e.kind === "award") toast(e.msg, "", pc(e.p ?? 0));
    if (e.kind === "private" && /you drew/.test(e.msg)) toast("Development card", e.msg.replace(/[()]/g, ""), "#9c88e0");
    if (e.kind === "private_steal") toast("Steal", e.msg.replace(/[()]/g, ""), "#e59cf2");
  }
}

function flash(el, cls, ms) {
  if (!el) return;
  el.classList.remove(cls);
  void el.getBoundingClientRect();
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

function floatDelta(p, n) {
  const card = $("#players").children[p];
  if (!card) return;
  const d = document.createElement("span");
  d.className = "delta" + (n < 0 ? " neg" : "");
  d.textContent = (n > 0 ? "+" : "") + n;
  card.appendChild(d);
  setTimeout(() => d.remove(), 1400);
}

// ------------------------------------------------------------------ toasts
function modal(dlg) {
  dlg.showModal();
  raiseToasts();
}
function raiseToasts() {
  const b = $("#toasts");
  try { if (b.matches(":popover-open")) b.hidePopover(); b.showPopover(); } catch { /* unsupported */ }
}
function toast(title, sub = "", color = "var(--accent)", ms = 4200) {
  const box = $("#toasts");
  if (!box.matches(":popover-open") || document.querySelector("dialog[open]")) raiseToasts();
  const t = document.createElement("div");
  t.className = "toast";
  t.style.setProperty("--tc", color);
  t.innerHTML = `<div class="t-body"><div class="t-title">${esc(title)}</div>${sub ? `<div class="t-sub">${esc(sub)}</div>` : ""}</div><button class="t-x" aria-label="Dismiss">✕</button>`;
  const kill = () => { t.classList.add("out"); setTimeout(() => t.remove(), 250); };
  t.querySelector(".t-x").onclick = kill;
  box.prepend(t);
  while (box.children.length > 3) box.lastChild.remove();
  setTimeout(kill, ms);
}

// ------------------------------------------------------------------ coach
function newReviews() {
  const revs = S.reviews;
  if (revs.length < reviewsSeen) reviewsSeen = revs.length;
  const busyDialog = document.querySelector("dialog[open]");
  for (const r of busyDialog ? [] : scored(revs.slice(reviewsSeen))) {
    const big = r.delta >= 0.2;
    toast(`${symbol(r.verdict)} ${r.verdict}${big ? `  −${r.delta.toFixed(2)}` : ""}`, big ? `You: ${plain(r.label)}\nEngine: ${plain(r.best)}` : plain(r.label), VERDICT_COLORS[r.verdict], big ? 7000 : 2600);
  }
  reviewsSeen = revs.length;
}
const plain = (s) => String(s).replace(/vertex \d+ ?/g, "").replace(/\b(road|settlement at|city at) \d+/g, "$1").replace(/settlement at \[/g, "settlement [").replace(/city at \[/g, "city [");
const symbol = (v) => ({ forced: "·", best: "✓", good: "✓", inaccuracy: "?!", mistake: "?", blunder: "??" })[v];
const engineLine = (r) => r.forced ? "no real choice" : r.delta < 0.05 ? "engine agrees" : `engine: ${plain(r.best)}`;
const scored = (revs) => revs.filter((r) => !r.forced);

function renderReviews() {
  const revs = S.reviews;
  const ol = $("#reviews");
  const key = revs.length + ":" + (revs.at(-1)?.delta ?? "");
  if (ol.dataset.key === key) return;
  ol.dataset.key = key;
  $("#btn-review").disabled = !revs.length;
  if (!revs.length) { ol.replaceChildren(); $("#accuracy").textContent = ""; return; }
  const real = scored(revs);
  const good = real.filter((r) => r.delta < 0.2).length;
  const lost = real.reduce((a, r) => a + r.delta, 0);
  $("#accuracy").innerHTML = real.length ? `Accuracy <b>${Math.round((100 * good) / real.length)}%</b> of ${real.length} decisions · lost <b>${lost.toFixed(2)}</b> VP-eq` : "";
  // forced moves only appear in the full "All moves" table
  ol.innerHTML = real.slice().reverse().slice(0, 60).map(reviewItem).join("");
}

function reviewItem(r) {
  return `<li class="rev ${r.forced ? "forced" : ""}" ${r.action ? `data-a='${JSON.stringify(r.action)}'` : ""} ${r.best_action ? `data-b='${JSON.stringify(r.best_action)}'` : ""}>
    <span class="vd vd-${r.verdict}">${symbol(r.verdict)} ${r.verdict}</span>
    <span>${esc(plain(r.label))}${r.delta >= 0.05 ? ` <span class="loss-n">−${r.delta.toFixed(2)}</span>` : ""}</span>
    <span class="best-line">${esc(engineLine(r))}</span></li>`;
}

function showFullReview() {
  const revs = S?.reviews || [];
  const real = scored(revs);
  const lost = real.reduce((a, r) => a + r.delta, 0);
  const counts = {};
  for (const r of real) counts[r.verdict] = (counts[r.verdict] || 0) + 1;
  const rows = revs.map((r) => `<tr class="${r.forced ? "forced" : ""}">
      <td class="muted">${r.turn ? r.turn : "setup"}</td>
      <td>${esc(plain(r.label))}</td>
      <td><span class="vd vd-${r.verdict}">${symbol(r.verdict)} ${r.verdict}</span></td>
      <td class="num">${r.forced ? "" : r.delta < 0.005 ? "0" : "−" + r.delta.toFixed(2)}</td>
      <td class="muted">${r.forced ? "—" : r.delta < 0.05 ? "same as yours" : esc(plain(r.best))}</td></tr>`).join("");
  openInfo("Every move, rated", `
    <p class="muted">Each of your decisions compared with the engine's best line, in VP-equivalents lost. Forced moves (only one sensible choice) are listed but not scored.</p>
    <div class="vcounts">${["best", "good", "inaccuracy", "mistake", "blunder"].map((k) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${counts[k] || 0}</span>`).join("")}</div>
    <p>Accuracy <b>${real.length ? Math.round((100 * real.filter((r) => r.delta < 0.2).length) / real.length) : 100}%</b> · total lost <b>${lost.toFixed(2)}</b> VP-eq · ${revs.length} moves</p>
    <div class="table-scroll"><table class="info-table review-table"><thead><tr><th>Turn</th><th>Your move</th><th>Verdict</th><th>Lost</th><th>Engine's best</th></tr></thead><tbody>${rows}</tbody></table></div>`, true);
}

async function showHint() {
  if (!S) return;
  if (!myDecision() && !S.pending_offer) { toast("Hints are for your decisions", "Wait for your turn.", "var(--muted)", 2200); return; }
  if (S.pending_offer) return askCoachOffer();
  const out = $("#coach-out");
  out.innerHTML = `<p class="coach-note">Thinking…</p>`;
  try {
    const st = await api("hint");
    apply(st);
    const h = st.hint;
    hintKey = `${S.turn}|${S.phase}|${S.actor}|${S.ev_total}`;
    if (!h.options.length) { out.innerHTML = `<p class="coach-note">Nothing to decide here.</p>`; return; }
    out.innerHTML = `<p class="coach-note">Engine's top options (${h.secs.toFixed(1)}s) — hover to see them on the board</p><ol class="opts">${h.options.map((o, i) => `
      <li class="opt" data-i="${i}"><span class="loss ${i === 0 ? "best" : o.loss < 0.5 ? "near" : "far"}">${i === 0 ? "best" : "−" + o.loss.toFixed(2)}</span><span>${esc(plain(o.text))}</span>${playableHint(o.action) ? `<button class="btn do" data-i="${i}" title="Play this move">Play</button>` : "<span></span>"}</li>`).join("")}</ol>`;
    out._opts = h.options;
  } catch (e) {
    out.innerHTML = `<p class="coach-note">${esc(e.message)}</p>`;
  }
}

function playableHint(a) {
  return a && !["macro", "discard", "trade", "propose"].includes(a[0]);
}

async function askCoachOffer() {
  const box = $("#bo-hint");
  box.textContent = "Coach is thinking…";
  try {
    const st = await api("hint");
    const h = st.hint;
    box.innerHTML = `Engine: <b style="color:${h.accept ? "var(--good)" : "var(--worst)"}">${h.accept ? "ACCEPT" : "DECLINE"}</b> · net value for you ${h.gain >= 0 ? "+" : ""}${h.gain.toFixed(2)} VP-eq <span class="muted">(includes what they build with it)</span>`;
  } catch (e) {
    box.textContent = e.message;
  }
}

async function showEval() {
  if (!S) return;
  const out = $("#coach-out");
  out.innerHTML = `<p class="coach-note">Evaluating…</p>`;
  try {
    const st = await api("eval");
    const rows = st.eval.rows;
    const top = Math.max(1, ...rows.map((r) => r.value));
    hintKey = `${S.turn}|${S.phase}|${S.actor}|${S.ev_total}`;
    out.innerHTML = `<p class="coach-note">Engine evaluation from your point of view (VP-equivalents). Pips = expected cards per 36 rolls.</p><div class="evals">${rows.map((r) => `
      <div class="ev-row" style="--pc:${pc(r.p)}"><b>${esc(S.players[r.p].name)}</b><span class="bar"><i style="width:${Math.max(2, (100 * r.value) / top)}%"></i></span><span>${r.value.toFixed(2)}</span>
      <span class="pips">${r.pips.map((n, i) => `<span>${resIcon(i)}${Math.round(n)}</span>`).join("")}<span>Σ${Math.round(r.pips.reduce((a, b) => a + b, 0))}</span></span></div>`).join("")}</div>`;
  } catch (e) {
    out.innerHTML = `<p class="coach-note">${esc(e.message)}</p>`;
  }
}

function glowFor(a, keep = false) {
  if (!keep) clearGlow();
  if (!a) return;
  if (a[0] === "macro") return;
  const g = els.glow;
  if ((a[0] === "settle" || a[0] === "city") && B.vertices[a[1]]) {
    const p = B.vertices[a[1]];
    svg("circle", { class: "glow-mark", cx: p.x, cy: p.y, r: 15 }, g);
  } else if (a[0] === "road" && B.edges[a[1]]) {
    const [p, q] = B.edges[a[1]].map((v) => B.vertices[v]);
    svg("line", { class: "glow-mark", x1: p.x, y1: p.y, x2: q.x, y2: q.y, "stroke-width": 9, "stroke-linecap": "round" }, g);
  } else if (a[0] === "robber") {
    const h = B.hexes[a[1]];
    svg("polygon", { class: "glow-mark", points: els.hexPts(h.x, h.y, 0.88) }, g);
  }
}
function clearGlow() { els.glow?.replaceChildren(); }

// ------------------------------------------------------------------ board input
function onBoardClick(ev) {
  if (busy || !myDecision()) return;
  const v = ev.target.closest("[data-v]");
  const e = ev.target.closest(".e-target");
  const h = ev.target.closest(".hex-target");
  if (v) {
    const id = Number(v.dataset.v);
    act([v.dataset.kind === "city" ? "city" : "settle", id]);
  } else if (e) {
    act(["road", Number(e.dataset.e)]);
  } else if (h) {
    const hex = Number(h.dataset.h);
    const vics = S.legal.robber[hex] || [];
    if (vics.length <= 1) act(["robber", hex, vics.length ? vics[0] : -1]);
    else chooseVictim(hex, vics, ev);
  } else if (mode) {
    mode = null;
    render();
  }
}

function chooseVictim(hex, vics, ev) {
  const pop = $("#victims");
  pop.innerHTML = `<h3>Steal from…</h3>` + vics.map((q) => `<button class="btn ghost" data-q="${q}" style="--pc:${pc(q)}"><span class="dot"></span>${esc(S.players[q].name)} <span class="muted">${S.players[q].cards} cards · ${S.players[q].vp} VP</span></button>`).join("");
  pop.style.left = `${Math.min(ev.clientX + 8, innerWidth - 240)}px`;
  pop.style.top = `${Math.min(ev.clientY + 8, innerHeight - 160)}px`;
  pop.onclick = (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    pop.hidePopover();
    act(["robber", hex, Number(b.dataset.q)]);
  };
  pop.showPopover();
}

function onBoardHover(ev) {
  const tip = $("#tip");
  const v = ev.target.closest("[data-v]");
  const hx = ev.target.closest("[data-h]");
  let html = null;
  if (v) {
    const vx = B.vertices[Number(v.dataset.v)];
    const what = { settle: "Build a settlement", city: "Upgrade to a city" }[v.dataset.kind];
    html = `${what ? `<b style="color:var(--accent)">${what}</b><br>` : ""}<b>${vx.desc || "coast"}</b><br><span class="muted">${vx.pips} pips total</span>`;
  } else if (ev.target.closest(".e-target")) {
    html = `<b style="color:var(--accent)">Build a road</b><br><span class="muted">wood + brick</span>`;
  } else if (hx) {
    const h = B.hexes[Number(hx.dataset.h)];
    html = h.res ? `<b>${h.label}</b> · ${h.num} ${h.res} <span class="muted">(${h.pips} pips)</span>` : `<b>${h.label}</b> · desert`;
    if (Number(hx.dataset.h) === S?.robber) html += `<br><span class="muted">robber is here</span>`;
  }
  if (!html) { tip.classList.remove("show"); return; }
  tip.innerHTML = html;
  tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - 290)}px`;
  tip.style.top = `${ev.clientY + 26}px`;
  tip.classList.add("show");
}

// ------------------------------------------------------------------ dialogs
function stepperRow(r, max, onChange, extra = "") {
  const row = document.createElement("div");
  row.className = `stepper r-${RES[r]}`;
  row.innerHTML = `${resIcon(r)}<span class="name">${RES[r]}</span>${extra}<button class="btn" data-d="-1" aria-label="fewer ${RES[r]}">−</button><span class="val">0</span><button class="btn" data-d="1" aria-label="more ${RES[r]}">+</button>`;
  if (!extra) row.style.gridTemplateColumns = "24px 1fr 30px 26px 30px";
  let val = 0;
  const upd = () => {
    val = Math.max(0, Math.min(val, max()));
    row.querySelector(".val").textContent = val;
    row.querySelector('[data-d="-1"]').disabled = val <= 0;
    row.querySelector('[data-d="1"]').disabled = val >= max();
  };
  row.addEventListener("click", (e) => {
    const b = e.target.closest("[data-d]");
    if (!b) return;
    val = Math.max(0, Math.min(max(), val + Number(b.dataset.d)));
    upd();
    onChange();
  });
  row.get = () => val;
  row.set = (n) => { val = n; upd(); };
  row.refresh = upd;
  upd();
  return row;
}

function openDiscard() {
  const dlg = $("#dlg-discard");
  if (dlg.open || !S.discard_owed) return;
  const need = S.discard_owed;
  $("#discard-n").textContent = need;
  const box = $("#discard-steppers");
  const rows = [];
  const total = () => rows.reduce((a, r) => a + r.get(), 0);
  const sync = () => {
    rows.forEach((r) => r.refresh());
    $("#discard-ok").disabled = total() !== need;
    $("#discard-ok").textContent = `Discard ${total()}/${need}`;
  };
  RES.forEach((_, r) => rows.push(stepperRow(r, () => Math.min(S.hand[r], (rows[r]?.get() ?? 0) + need - total()), sync, `<span class="have">have ${S.hand[r]}</span>`)));
  box.replaceChildren(...rows);
  sync();
  $("#discard-ok").onclick = async () => {
    dlg.close();
    await act(["discard", rows.map((r) => r.get())]);
  };
  $("#discard-hint").onclick = async () => {
    try {
      const st = await api("hint");
      const a = st.hint.options[0]?.action;
      if (a) { a[1].forEach((n, r) => rows[r].set(n)); sync(); }
    } catch (e) { toast(e.message, "", "var(--worst)"); }
  };
  modal(dlg);
}

function openBotOffer() {
  const dlg = $("#dlg-botoffer");
  const po = S.pending_offer;
  if (!po || dlg.open) return;
  $("#bo-title").textContent = `${S.players[po.from].name} offers you a trade`;
  const mini = (counts) => counts.map((n, r) => n ? `<div class="rcard r-${RES[r]}">${icon("i-" + RES[r])}<span class="n">${n}</span></div>` : "").join("");
  $("#bo-get").innerHTML = mini(po.give);
  $("#bo-give").innerHTML = mini(po.get);
  const offerEv = [...$$("#log li.k-offer")].at(-1);
  $("#bo-why").textContent = prefs.reasons && offerEv?.querySelector(".why") ? `“${offerEv.querySelector(".why").textContent}”` : "";
  $("#bo-hint").textContent = "";
  const answer = async (yes) => {
    dlg.close();
    while (busy) await sleep(40);
    busy = true;
    try { apply(await api("respond", { accept: yes })); } catch (e) { toast(e.message, "", "var(--worst)"); }
    busy = false;
    render();
    pump();
  };
  $("#bo-yes").onclick = () => answer(true);
  $("#bo-no").onclick = () => answer(false);
  $("#bo-ask").onclick = askCoachOffer;
  modal(dlg);
}

// Trade dialog: bank/ports and player offers side by side at a fixed size.
// Give/want tiles add one card per click; the small minus takes one back.
const trade = { give: [0, 0, 0, 0, 0], want: [0, 0, 0, 0, 0], replies: null, note: "" };

function openTrade() {
  if (!myDecision() || S.phase !== "main") return;
  trade.give = [0, 0, 0, 0, 0];
  trade.want = [0, 0, 0, 0, 0];
  trade.replies = null;
  trade.note = "";
  renderTrade();
  modal($("#dlg-trade"));
}

function renderTrade() {
  const live = myDecision() && S.phase === "main";
  trade.give = trade.give.map((n, r) => Math.min(n, S.hand[r]));
  $("#trade-hand").innerHTML = `<span class="th-label">Your hand</span>` + RES.map((r, i) =>
    `<span class="th-res ${S.hand[i] ? "" : "zero"}" title="${S.hand[i]} ${r}">${resIcon(i)}<b>${S.hand[i]}</b></span>`).join("");

  $("#bank-trade").innerHTML = RES.map((r, i) => {
    const ratio = S.ratios[i];
    const ok = live && S.hand[i] >= ratio;
    const gets = RES.map((g, j) => j === i ? "" :
      `<button class="btn res-btn" data-give="${i}" data-get="${j}" ${ok && S.bank[j] > 0 ? "" : "disabled"} title="Give ${ratio} ${r}, get 1 ${g}">${resIcon(j)}</button>`).join("");
    return `<div class="bt-row ${ok ? "" : "off"}"><div class="bt-give">${resIcon(i)}<b>${ratio}:1</b><small>${S.hand[i]}/${ratio}</small></div><div class="bt-gets">${gets}</div></div>`;
  }).join("");

  const tile = (side, r) => {
    const n = trade[side][r];
    const max = side === "give" ? S.hand[r] : 9;
    return `<div class="pick ${n ? "on" : ""} r-${RES[r]}">
      <button class="pick-add" data-side="${side}" data-r="${r}" ${live && n < max ? "" : "disabled"} title="${side === "give" ? "Give" : "Ask for"} one more ${RES[r]}">${icon("i-" + RES[r])}<span class="n">${n || ""}</span></button>
      <button class="pick-sub" data-side="${side}" data-r="${r}" data-d="-1" ${n ? "" : "hidden"} aria-label="one fewer ${RES[r]}">−</button></div>`;
  };
  $("#offer-give").innerHTML = RES.map((_, r) => tile("give", r)).join("");
  $("#offer-get").innerHTML = RES.map((_, r) => tile("want", r)).join("");
  const fmt = (c) => c.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(" + ");
  const ready = trade.give.some(Boolean) && trade.want.some(Boolean);
  $("#offer-summary").innerHTML = ready ? `<b>${fmt(trade.give)}</b> <span class="muted">for</span> <b>${fmt(trade.want)}</b>`
    : `<span class="muted">${trade.give.some(Boolean) ? "Now pick what you want" : "Click your cards to give, then what you want"}</span>`;
  $("#offer-send").disabled = !live || !ready || busy;
  $("#offer-clear").disabled = !trade.give.some(Boolean) && !trade.want.some(Boolean);

  const box = $("#offer-replies");
  if (trade.replies) {
    box.innerHTML = trade.replies.map((r) => `<div class="reply" style="--pc:${pc(r.p)}"><span class="dot"></span><b>${esc(S.players[r.p].name)}</b>
      <span class="ans ${r.ok ? "yes" : "no"}">${r.ok ? "accepts" : "declines"}</span>${r.ok ? `<button class="btn primary" data-q="${r.p}" ${live ? "" : "disabled"}>Trade</button>` : ""}</div>`).join("");
  } else {
    box.innerHTML = `<p class="coach-note">${esc(trade.note || "Every bot answers at once; you pick who to trade with.")}</p>`;
  }
}

async function sendOffer() {
  const give = trade.give.slice(), get = trade.want.slice();
  trade.replies = null;
  trade.note = "Waiting for answers…";
  renderTrade();
  try {
    const st = await api("offer", { give, get });
    apply(st);
    trade.replies = st.my_offer.replies;
    trade.sent = { give, get };
  } catch (e) {
    trade.note = e.message;
  }
  renderTrade();
}

async function onTradeClick(e) {
  const bank = e.target.closest("[data-give]");
  if (bank) {
    await act(["bank", Number(bank.dataset.give), Number(bank.dataset.get)]);
    return renderTrade();
  }
  const pick = e.target.closest("[data-side]");
  if (pick) {
    const side = pick.dataset.side, r = Number(pick.dataset.r);
    const other = side === "give" ? "want" : "give";
    trade[side][r] = Math.max(0, trade[side][r] + (pick.dataset.d === "-1" ? -1 : 1));
    if (trade[side][r]) trade[other][r] = 0; // can't give and want the same card
    trade.replies = null;
    trade.note = "";
    return renderTrade();
  }
  const q = e.target.closest("[data-q]");
  if (q && trade.sent) {
    const name = S.players[Number(q.dataset.q)].name;
    await act(["trade", Number(q.dataset.q), trade.sent.give, trade.sent.get]);
    trade.give = [0, 0, 0, 0, 0];
    trade.want = [0, 0, 0, 0, 0];
    trade.replies = null;
    trade.note = `Traded with ${name}.`;
    return renderTrade();
  }
  if (e.target.closest("#offer-send")) return sendOffer();
  if (e.target.closest("#offer-clear")) {
    trade.give = [0, 0, 0, 0, 0];
    trade.want = [0, 0, 0, 0, 0];
    trade.replies = null;
    trade.note = "";
    renderTrade();
  }
}

function openPick(kind) {
  const dlg = $("#dlg-pick");
  const yop = kind === "yop";
  $("#pick-title").textContent = yop ? "Year of Plenty" : "Monopoly";
  $("#pick-sub").textContent = yop ? "Take any two resources from the bank (the same one twice is fine)." : "Every opponent hands you all their cards of one resource.";
  const picks = [];
  const box = $("#pick-res");
  const draw = () => {
    box.innerHTML = RES.map((r, i) => {
      const n = picks.filter((x) => x === i).length;
      const off = yop && S.bank[i] < 1;
      return `<button data-r="${i}" aria-pressed="${n > 0}" ${off ? "disabled" : ""} title="${r}"><div class="rcard r-${r}">${icon("i-" + r)}<span class="lbl">${r}</span>${n ? `<span class="n">${n}</span>` : ""}</div></button>`;
    }).join("");
    $("#pick-ok").disabled = picks.length !== (yop ? 2 : 1);
  };
  box.onclick = (e) => {
    const b = e.target.closest("[data-r]");
    if (!b) return;
    const r = Number(b.dataset.r);
    if (yop) {
      if (picks.filter((x) => x === r).length >= Math.min(2, S.bank[r])) return;
      if (picks.length === 2) picks.shift();
      picks.push(r);
    } else {
      picks.splice(0, 1, r);
    }
    draw();
  };
  $("#pick-ok").onclick = () => {
    dlg.close();
    if (yop) act(["play_yop", Math.min(...picks), Math.max(...picks)]);
    else act(["play_mono", picks[0]]);
  };
  draw();
  modal(dlg);
}

function openInfo(title, html, wide = false) {
  $("#info-title").textContent = title;
  $("#info-body").innerHTML = html;
  $("#dlg-info").classList.toggle("wide", wide);
  modal($("#dlg-info"));
}

function showStats() {
  if (!S) return;
  const hist = S.roll_hist;
  const total = hist.reduce((a, b) => a + b, 0);
  const ways = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };
  const exp = (t) => (ways[t] / 36) * total;
  const top = Math.max(1, ...hist, ...Object.keys(ways).map((t) => exp(Number(t))));
  const cols = [];
  for (let t = 2; t <= 12; t++) {
    cols.push(`<div class="col"><span class="num">${hist[t]}</span><div class="bars"><div class="bar ${t === 7 ? "seven" : ""}" style="height:${(100 * hist[t]) / top}%"></div><div class="exp" style="bottom:${(100 * exp(t)) / top}%"></div></div><span>${t}</span></div>`);
  }
  openInfo("Dice", `<p class="muted">${total} rolls so far. Bars are actual counts; dashed lines are what fair dice would give on average.</p><div class="hist">${cols.join("")}</div>`);
}

function showHelp() {
  const k = (keys, what) => `<span>${keys.map((x) => `<kbd>${x}</kbd>`).join(" ")}</span><span>${what}</span>`;
  openInfo("How to play", `<div class="help">
    <div class="help-grid">
      ${k(["R"], "Roll the dice")}${k(["1"], "Build a road — then click a highlighted edge")}${k(["2"], "Build a settlement")}${k(["3"], "Upgrade a settlement to a city")}
      ${k(["D"], "Buy a development card")}${k(["T"], "Trade with the bank, ports or players")}${k(["E"], "End your turn")}
      ${k(["H"], "Coach: engine's top options")}${k(["Ctrl", "Z"], "Undo your last decision (the dice stay the same)")}${k(["Esc"], "Cancel build mode")}
    </div>
    <p>First to <b>10 VP</b> on their own turn wins. Settlements are worth 1, cities 2, Longest Road (5+) and Largest Army (3+ knights) 2 each, and hidden victory-point cards 1.</p>
    <p>On a 7 everyone holding more than 7 cards discards half, then the robber moves and steals a card. You can play one development card per turn, never on the turn you bought it — knights may be played before rolling.</p>
    <p>You only see your own cards, exactly like at a real table. Opponents show card <i>counts</i>; the bots get the same information and track your hand from public events.</p>
    <p>The <b>coach</b> reviews every decision in VP-equivalents: ✓ best &lt; 0.05 ≤ ✓ good &lt; 0.2 ≤ ?! inaccuracy &lt; 0.5 ≤ ? mistake &lt; 1.0 ≤ ?? blunder.</p>
  </div>`);
}

function showGameOver() {
  const sm = S.summary;
  if (!sm) return;
  const won = S.winner === S.human;
  $("#over-title").innerHTML = `<div class="over-banner ${won ? "win" : ""}">${won ? "♛ You win!" : `${esc(S.players[S.winner].name)} wins`}</div>`;
  const rows = sm.rows.map((r) => {
    const extra = [r.longest && "longest road", r.army && "largest army", r.vp_cards && `${r.vp_cards} VP card${r.vp_cards > 1 ? "s" : ""}`].filter(Boolean).join(", ");
    return `<tr><td><span style="color:${pc(r.p)};font-weight:700">${esc(S.players[r.p].name)}</span></td><td><b>${r.vp}</b></td><td>${r.settlements}</td><td>${r.cities}</td><td class="muted">${extra}</td></tr>`;
  }).join("");
  let rev = "";
  if (sm.accuracy !== null) {
    rev = `<h3 style="margin-top:18px">Your decisions</h3>
      <div class="vcounts">${Object.entries(sm.counts).map(([k, n]) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${n}</span>`).join("")}</div>
      <p>Accuracy <b>${Math.round(sm.accuracy)}%</b> · total value lost <b>${sm.lost.toFixed(2)}</b> VP-eq · ${sm.turns} turns · <button class="btn ghost" id="over-all" style="padding:2px 8px">every move…</button></p>
      ${sm.worst.length ? `<h3>Biggest misses</h3><ul class="misses">${sm.worst.map((w) => `<li><span class="vd vd-${w.verdict}">−${w.delta.toFixed(2)}</span> ${w.turn ? `turn ${w.turn}` : "setup"}: ${esc(plain(w.label))}<br><span class="eng">engine: ${esc(plain(w.best))}</span></li>`).join("")}</ul>` : ""}`;
  }
  $("#over-body").innerHTML = `<table class="info-table"><thead><tr><th>Player</th><th>VP</th><th>Settl.</th><th>Cities</th><th></th></tr></thead><tbody>${rows}</tbody></table>${rev}`;
  modal($("#dlg-over"));
}

function openNewGame(cancellable = true) {
  const dlg = $("#dlg-new");
  $("#new-cancel").hidden = !cancellable || !S;
  const last = store("newgame", null);
  const f = $("#form-new");
  if (last) {
    for (const [k, v] of Object.entries(last)) {
      const inp = f.querySelector(`[name="${k}"][value="${v}"]`) || (k === "name" && f.elements.name);
      if (inp && inp.type === "radio") inp.checked = true;
      else if (inp && k === "name") inp.value = v;
    }
  }
  syncSeat4();
  f.elements.seed.value = "";
  dlg.returnValue = "";
  modal(dlg);
}

function syncSeat4() {
  const f = $("#form-new");
  const three = f.elements.opponents.value === "3";
  f.querySelector(".seat4").hidden = !three;
  if (!three && f.elements.seat.value === "4") f.querySelector('[name="seat"][value=""]').checked = true;
}

// ------------------------------------------------------------------ wiring
function wireUI() {
  renderCosts();
  const board = $("#board");
  board.addEventListener("click", onBoardClick);
  board.addEventListener("pointermove", onBoardHover);
  board.addEventListener("pointerleave", () => $("#tip").classList.remove("show"));

  $("#btn-roll").onclick = () => act(["roll"]);
  $("#btn-end").onclick = () => { mode = null; act(["end"]); };
  $("#btn-dev").onclick = () => act(["buy_dev"]);
  $("#btn-trade").onclick = () => openTrade();
  $("#btn-discard").onclick = () => openDiscard();
  $("#btn-answer").onclick = () => openBotOffer();
  for (const b of $$("[data-mode]")) b.onclick = () => toggleMode(b.dataset.mode);
  $("#btn-hint").onclick = showHint;
  $("#btn-eval").onclick = showEval;
  $("#btn-review").onclick = showFullReview;
  $("#btn-undo").onclick = undo;
  $("#btn-stats").onclick = showStats;
  $("#btn-help").onclick = showHelp;
  $("#btn-new").onclick = () => openNewGame(true);
  $("#seed").onclick = () => {
    if (!S) return;
    navigator.clipboard?.writeText(String(S.seed)).then(() => toast("Seed copied", `Start a new game with seed ${S.seed} to replay this board and dice.`, "var(--muted)", 2600), () => {});
  };

  $("#devhand").addEventListener("click", (e) => {
    const c = e.target.closest(".dcard.playable");
    if (!c || busy) return;
    const k = Number(c.dataset.dev);
    if (k === 3) openPick("mono");
    else if (k === 4) openPick("yop");
    else act([DEVS[k].play]);
  });

  $("#coach-out").addEventListener("pointerover", (e) => {
    const li = e.target.closest(".opt");
    const opts = $("#coach-out")._opts;
    if (li && opts) glowFor(opts[Number(li.dataset.i)].action);
  });
  $("#coach-out").addEventListener("pointerleave", clearGlow);
  $("#coach-out").addEventListener("click", (e) => {
    const b = e.target.closest(".do");
    const opts = $("#coach-out")._opts;
    if (!b || !opts) return;
    const a = opts[Number(b.dataset.i)].action;
    clearGlow();
    if (playableHint(a)) act(a);
  });

  $("#log").addEventListener("pointerover", (e) => {
    const li = e.target.closest("li");
    if (!li) return clearGlow();
    if (li.dataset.v) glowFor(["settle", Number(li.dataset.v)]);
    else if (li.dataset.e) glowFor(["road", Number(li.dataset.e)]);
    else if (li.dataset.h) glowFor(["robber", Number(li.dataset.h)]);
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

  // speed + autoroll controls live in the log header
  const head = $(".log-card h2");
  head.insertAdjacentHTML("afterend", `<div class="coach-head" style="margin:-4px 0 8px">
    <label class="switch">Speed <select id="speed" style="background:var(--panel-2);color:var(--text);border:1px solid var(--line-2);border-radius:6px;padding:1px 4px;font:inherit">
      <option value="fast">fast</option><option value="normal">normal</option><option value="slow">slow</option></select></label>
    <label class="switch" title="Roll automatically when you have no development card to play first"><input type="checkbox" id="autoroll">Auto-roll</label></div>`);
  $("#speed").value = prefs.speed;
  $("#speed").onchange = (e) => { prefs.speed = e.target.value; save("speed", prefs.speed); };
  $("#autoroll").checked = prefs.autoroll;
  $("#autoroll").onchange = (e) => { prefs.autoroll = e.target.checked; save("autoroll", prefs.autoroll); maybeAutoRoll(); };

  // trade dialog
  $("#dlg-trade").addEventListener("click", onTradeClick);

  // new game dialog
  const f = $("#form-new");
  f.addEventListener("change", syncSeat4);
  $("#dlg-new").addEventListener("close", async () => {
    const dlg = $("#dlg-new");
    if (dlg.returnValue !== "start") { if (!S) openNewGame(false); else pump(); return; }
    dlg.returnValue = "";
    const opts = { opponents: Number(f.elements.opponents.value), level: f.elements.level.value, seat: f.elements.seat.value, name: f.elements.name.value, seed: f.elements.seed.value };
    save("newgame", { opponents: opts.opponents, level: opts.level, seat: opts.seat, name: opts.name });
    try { await newGame(opts); } catch (e) { toast("Couldn't start", e.message, "var(--worst)"); openNewGame(!!S); }
  });
  $("#over-new").onclick = () => { $("#dlg-over").close(); openNewGame(true); };
  $("#over-body").addEventListener("click", (e) => { if (e.target.id === "over-all") { $("#dlg-over").close(); showFullReview(); } });
  $("#over-replay").onclick = async () => {
    $("#dlg-over").close();
    await newGame({ opponents: S.n - 1, level: S.level, seat: S.human + 1, name: S.players[S.human].name, seed: S.seed });
  };

  // forced decisions can't be escaped; they reopen if dismissed
  for (const id of ["#dlg-discard", "#dlg-botoffer"]) {
    $(id).addEventListener("cancel", (e) => e.preventDefault());
  }

  document.addEventListener("keydown", onKey);
}

function toggleMode(m) {
  if (!myDecision() || S.phase !== "main" || !can(m)) return;
  mode = mode === m ? null : m;
  render();
}

async function undo() {
  if (!S?.can_undo || busy || pumping) return;
  busy = true;
  try {
    mode = null;
    holdAutoRoll = true;
    apply(await api("undo", {}));
    reviewsSeen = S.reviews.length;
    toast("Undone", `Back to turn ${S.turn}. Same dice, different choices.`, "var(--muted)", 2200);
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  }
  busy = false;
  render();
  pump();
}

function onKey(e) {
  if (document.querySelector("dialog[open]")) return;
  if (e.target.matches("input, select, textarea")) return;
  const k = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && k === "z") { e.preventDefault(); undo(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const map = {
    r: () => can("roll") && act(["roll"]),
    e: () => can("end") && (mode = null, act(["end"])),
    d: () => can("buy_dev") && act(["buy_dev"]),
    t: () => openTrade(),
    h: () => showHint(),
    1: () => toggleMode("road"),
    2: () => toggleMode("settle"),
    3: () => toggleMode("city"),
    "?": () => showHelp(),
  };
  if (k === "escape") {
    if (mode) { e.preventDefault(); mode = null; render(); }
    return;
  }
  if (map[k]) { e.preventDefault(); map[k](); }
}

boot();

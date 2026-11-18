// Hexfield — browser client. Talks to hexfield/web/server.py; no dependencies.

const RES = ["wood", "brick", "sheep", "wheat", "ore"];
const COSTS = { road: [1, 1, 0, 0, 0], settle: [1, 1, 1, 1, 0], city: [0, 0, 0, 2, 3], dev: [0, 0, 1, 1, 1] };
const DEVS = [
  { key: "knight", name: "Knight", icon: "i-knight", play: "play_knight", hot: "4" },
  { key: "vp", name: "Victory pt", icon: "i-vp", play: null },
  { key: "roadb", name: "Road bldg", icon: "i-roadb", play: "play_road", hot: "5" },
  { key: "mono", name: "Monopoly", icon: "i-mono", play: "play_mono", hot: "6" },
  { key: "yop", name: "Plenty", icon: "i-yop", play: "play_yop", hot: "7" },
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

// "2 wheat, ore" / "wheat, wheat, ore" -> [0,0,0,2,1]
function parseCards(msg) {
  const c = [0, 0, 0, 0, 0];
  for (const m of msg.matchAll(/(?:(\d+) )?(wood|brick|sheep|wheat|ore)\b/g)) c[RES.indexOf(m[2])] += Number(m[1] || 1);
  return c;
}

// Expected production of player p: pips per resource (cards per 36 rolls),
// the dice numbers that pay out, and what the robber is currently blocking.
function production(p) {
  const pips = [0, 0, 0, 0, 0], blocked = [0, 0, 0, 0, 0], nums = [[], [], [], [], []];
  S.v_owner.forEach((o, v) => {
    if (o !== p) return;
    const lv = S.v_level[v];
    for (const h of B.vertices[v].hexes || []) {
      const hx = B.hexes[h];
      if (!hx.res) continue;
      const r = RES.indexOf(hx.res);
      if (h === S.robber) { blocked[r] += hx.pips * lv; continue; }
      pips[r] += hx.pips * lv;
      nums[r].push({ n: hx.num, lv, pips: hx.pips });
    }
  });
  return { pips, blocked, nums, total: pips.reduce((a, b) => a + b, 0) };
}

// ---- what a hand can pay for (used wherever a decision changes your hand)
const BUILD_NAMES = { road: "Road", settle: "Settlement", city: "City", dev: "Dev card" };
const covers = (hand, cost) => cost.every((n, r) => hand[r] >= n);
const handPlus = (hand, d) => hand.map((n, r) => n + (d[r] || 0));

// bank/port trades needed before `hand` covers `cost` (Infinity if your spare cards can't make it)
function tradesShort(hand, cost) {
  const miss = cost.reduce((a, n, r) => a + Math.max(0, n - hand[r]), 0);
  const spare = hand.reduce((a, n, r) => a + Math.floor(Math.max(0, n - cost[r]) / S.ratios[r]), 0);
  return spare >= miss ? miss : Infinity;
}

// "Can pay for" chips
function buildChips(hand) {
  const ks = Object.keys(COSTS).filter((k) => covers(hand, COSTS[k]));
  return ks.map((k) => `<span class="bchip">${BUILD_NAMES[k]}</span>`).join("") || `<span class="muted">nothing yet</span>`;
}

// only what a change of hand unlocks or gives up
function buildChanges(before, after) {
  const diff = (a, b) => Object.keys(COSTS).filter((k) => !covers(a, COSTS[k]) && covers(b, COSTS[k])).map((k) => BUILD_NAMES[k]);
  const gain = diff(before, after), lost = diff(after, before);
  if (!gain.length && !lost.length) return `<span class="muted">doesn't change what you can build</span>`;
  return [gain.length ? `unlocks ${gain.map((n) => `<span class="bchip gain">${n}</span>`).join("")}` : "",
    lost.length ? `gives up ${lost.map((n) => `<span class="bchip lost">${n}</span>`).join("")}` : ""].filter(Boolean).join(" · ");
}

// a hex by what it is ("9 brick") rather than its letter
function hexName(label) {
  const h = B?.hexes.find((x) => x.label === label);
  return !h ? label : h.res ? `${h.num} ${h.res}` : "the desert";
}

// a spot by what it touches: "8 wheat, 5 sheep · 3:1 port"
function spotName(v) {
  const vx = B.vertices[v];
  const hs = (vx.hexes || []).map((h) => B.hexes[h]).filter((h) => h.res).sort((a, b) => b.pips - a.pips).map((h) => `${h.num} ${h.res}`);
  const port = vx.port ? (vx.port === "any" ? "3:1 port" : `2:1 ${vx.port} port`) : "";
  return [hs.slice(0, 2).join(", "), port].filter(Boolean).join(" · ") || "the coast";
}

// a road, or a chain of roads, by where it leads: the far end of the chain, or for a
// single road the end that isn't already on your network
let vEdges = null;
function roadsToward(edges) {
  if (!vEdges) {
    vEdges = B.vertices.map(() => []);
    B.edges.forEach(([a, b], e) => { vEdges[a].push(e); vEdges[b].push(e); });
  }
  const [a, b] = B.edges[edges.at(-1)];
  if (edges.length > 1) return spotName(B.edges[edges.at(-2)].includes(a) ? b : a);
  const mine = (v) => S.v_owner[v] === S.human || vEdges[v].some((e) => e !== edges[0] && S.e_owner[e] === S.human);
  const open = [a, b].filter((v) => !mine(v));
  const end = open.length === 1 ? open[0] : [a, b].sort((x, y) => B.vertices[y].pips - B.vertices[x].pips)[0];
  return spotName(end);
}

// engine notation -> readable line: "road 4 -> road 9 -> city at 12 [..]" => "2 roads toward 8 wheat → city [..]"
function human(s) {
  const text = String(s).replace(/\s*\(eval [^)]*\)/g, "")
    .replace(/bank-trade \(([^)]*)\) and build (\w+)/g, (_, t, k) => `trade ${t.replace(/(\d+ \w+) ?-> ?(\w+)/g, "$1 for $2")}, build ${k}`)
    .replace(/\b(trade \d+ \w+) -> (\w+)/g, "$1 for $2");
  const out = [];
  let chain = [];
  const flush = () => {
    if (!chain.length) return;
    out.push(`${chain.length > 1 ? `${chain.length} roads` : "road"} toward ${B ? roadsToward(chain) : "?"}`);
    chain = [];
  };
  for (const part of text.split(/\s*->\s*/)) {
    const m = part.match(/^road (\d+)( — .*)?$/s);
    if (m && B?.edges[Number(m[1])]) {
      chain.push(Number(m[1]));
      if (m[2]) { flush(); out[out.length - 1] += plain(m[2]); } // "road 4 — why" in setup
      continue;
    }
    flush();
    out.push(plain(part));
  }
  flush();
  return out.join(" → ");
}

// the move only, without the engine's explanation after the dash
const short = (s) => human(String(s).split(" — ")[0]);

// replace an element's markup only when it changed (keeps hover/focus state and avoids churn)
function setHTML(el, html) {
  if (el.dataset.html === html) return false;
  el.dataset.html = html;
  el.innerHTML = html;
  return true;
}

// endpoints of edge e, pulled in from each end by fraction k
function edgeLine(e, k) {
  const [a, b] = B.edges[e].map((v) => B.vertices[v]);
  return { x1: a.x + (b.x - a.x) * k, y1: a.y + (b.y - a.y) * k, x2: b.x - (b.x - a.x) * k, y2: b.y - (b.y - a.y) * k };
}

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
let stopPump = false;   // set by undo: the bot loop stops after its current step
let reviewsSeen = 0;
let logStick = true;
let holdAutoRoll = false; // set by undo so it can step back past a roll
let pollTimer = null;
let overShown = null;
let hintKey = null;
let lastRoller = null;  // who threw the dice on show
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
  if (busy) return false;
  busy = true;
  holdAutoRoll = false;
  let ok = false;
  try {
    const st = await api("act", { action });
    apply(st);
    ok = true;
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  } finally {
    busy = false;
    render();
  }
  pump();
  return ok;
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
  } catch {
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
  logStick = true;
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
    logStick = true;
    initial = true; // don't replay effects after an undo
  }
  const fresh = st.events.filter((e) => e.i >= evCount);
  for (const e of fresh) if (e.kind === "roll") lastRoller = e.p;
  prev = S;
  S = st;
  evCount = st.ev_total;
  appendLog(fresh);
  if (!initial) { effects(fresh); handGains(fresh); }
  recapTurn(fresh, initial);
  if (mode && !(S.legal[mode] || []).length) mode = null;
  if (hintKey && hintKey !== decisionKey()) closeCoach();
  render();
  pinLog();
  pollReviews();
  newReviews();
  if (S.phase === "game_over" && overShown !== S.seed) {
    overShown = S.seed;
    setTimeout(showGameOver, initial ? 0 : 900);
  }
}

// the coach panel answers one decision; it closes once the game moves on
const decisionKey = () => `${S.turn}|${S.phase}|${S.actor}|${S.ev_total}`;
function closeCoach() { $("#coach-out").replaceChildren(); hintKey = null; optSel = null; clearGlow(); }

const myDecision = () => S && S.phase !== "game_over" && S.actor === S.human && !S.pending_offer;
const legal = () => (myDecision() ? S.legal : {});
const can = (kind) => (legal().kinds || []).includes(kind);

// ------------------------------------------------------------------ bot loop
async function pump() {
  if (pumping || !S) return;
  pumping = true;
  try {
    while (S && !stopPump && S.phase !== "game_over" && S.actor !== S.human && !S.pending_offer && !$("#dlg-new").open) {
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
  // only a knight is worth playing before the roll; other dev cards can wait until after it
  if (kinds.includes("roll") && !kinds.includes("play_knight")) {
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
      svg("use", { href: `#i-${port.kind}`, x: -7, y: -14, width: 14, height: 14, style: `fill:var(--${port.kind})` }, g);
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
    return g;
  });

  els.roads = svg("g", { class: "roads" }, board);
  els.pieces = svg("g", { class: "pieces" }, board);
  els.robber = svg("g", { class: "robber" }, board);
  els.robber.innerHTML = `<path d="M0-17a7 7 0 0 1 7 7c0 2.6-1.4 4.6-3.3 5.8C7.6-2.6 10 1.8 10 7v6c0 2-1.5 3-4 3H-6c-2.5 0-4-1-4-3V7c0-5.2 2.4-9.6 6.3-11.2A6.8 6.8 0 0 1-7-10a7 7 0 0 1 7-7z" filter="url(#shadow)"/><circle class="eye" cx="-2.4" cy="-10" r="1.2"/><circle class="eye" cx="2.4" cy="-10" r="1.2"/>`;
  els.targets = svg("g", { class: "targets" }, board);
  els.keys = svg("g", { class: "keys" }, board);
  els.glow = svg("g", { class: "glow" }, board);
  els.fx = svg("g", { class: "fx" }, board);
  els.roadEls = {};
  els.pieceEls = {};
  els.hexPts = hexPts;
  els.hexVerts = B.hexes.map((_, h) => B.vertices.flatMap((v, i) => ((v.hexes || []).includes(h) ? [i] : [])));
}

// who a robber on hex h would block: {player: pips lost}
function robberHits(h) {
  const hx = B.hexes[h], hit = {};
  if (!hx.res) return hit;
  for (const v of els.hexVerts[h]) {
    const o = S.v_owner[v];
    if (o !== -1) hit[o] = (hit[o] || 0) + hx.pips * S.v_level[v];
  }
  return hit;
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
  const { x1, y1, x2, y2 } = edgeLine(e, 0.16);
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
  renderKeys();
  renderPlayers();
  renderHand();
  renderActions();
  renderStatus(pumping ? S.actor : null);
  renderDice(false);
  renderBank();
  renderEcon();
  renderReviews();
  $("#seed").textContent = `seed ${S.seed}`;
  $("#btn-undo").disabled = !S.can_undo;
  for (const b of $$(".dlg-undo")) b.hidden = !S.can_undo;
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
    const hx = B.hexes[h], hit = robberHits(h);
    const who = Object.keys(hit).map(Number).sort((a, b) => (a === S.human) - (b === S.human) || hit[b] - hit[a]);
    const cls = hit[S.human] ? "hurts" : who.length ? "" : "empty";
    svg("polygon", { class: `hex-target ${cls}`, "data-h": h, points: els.hexPts(hx.x, hx.y, 0.9) }, g);
    // one chip per player it would block, with the pips they'd lose
    who.forEach((p, i) => {
      const cg = svg("g", { class: `rb-chip ${p === S.human ? "me" : ""}`, transform: `translate(${(hx.x + (i - (who.length - 1) / 2) * 22).toFixed(1)} ${hx.y + 40})` }, g);
      svg("circle", { r: 9, style: `fill:${pc(p)}` }, cg);
      const tx = svg("text", { y: 3.5 }, cg); tx.textContent = hit[p];
    });
  }
  for (const e of t.e) {
    const at = edgeLine(e, 0.2);
    const tg = svg("g", { class: "e-target", "data-e": e }, g);
    svg("line", { class: "vis", ...at }, tg);
    svg("line", { class: "hit", ...at }, tg);
  }
  for (const [v, kind] of t.v) {
    const p = B.vertices[v];
    const tg = svg("g", { class: `v-target k-${kind}`, "data-v": v, "data-kind": kind }, g);
    svg("circle", { class: "hit", cx: p.x, cy: p.y, r: 16 }, tg);
    if (kind === "settle") {
      // label each spot with its pip total; hotter spots read brighter
      tg.style.setProperty("--heat", Math.min(1, p.pips / 13).toFixed(2));
      tg.classList.add(p.pips >= 10 ? "t-hi" : p.pips <= 5 ? "t-lo" : "t-mid");
      svg("circle", { class: "ring scored", cx: p.x, cy: p.y, r: 11 }, tg);
      const tx = svg("text", { class: "v-pips", x: p.x, y: p.y + 3.6 }, tg);
      tx.textContent = p.pips;
    } else {
      svg("circle", { class: "ring", cx: p.x, cy: p.y, r: 16 }, tg);
    }
  }
}

// ---- keyboard: every control that has a key carries data-key="<label>" and shows it
// as a badge. One handler presses the visible control with that label in the active
// scope (the open dialog, the victim menu, or the main screen), so a label is
// unique per scope by construction and there is no second table to drift.
// Board spot letters use whatever letters the main screen leaves free.
const MAIN_LETTERS = new Set($$("body > .app [data-key]")
  .map((el) => el.dataset.key).filter((k) => /^[A-Z]$/.test(k)).map((k) => k.toLowerCase()));
const KEY_ABC = [..."abcdefghijklmnopqrstuvwxyz"].filter((c) => !MAIN_LETTERS.has(c)).join("");
let keyBuf = "";
let keyMap = []; // [{code, x, y} + one of v/kind, e, hex]

function keyTargets() {
  if (!myDecision()) return [];
  const t = targetsNow();
  const labelled = mode || ["setup_settlement", "setup_road", "road_building", "move_robber"].includes(S.phase);
  if (!labelled) return [];
  if (t.h.length) {
    // the robber uses each hex's fixed letter (never a shortcut), shown only while robbing
    return t.h.map((h) => ({ code: B.hexes[h].label.toLowerCase(), hex: h, x: B.hexes[h].x, y: B.hexes[h].y - 26 }));
  }
  const items = [
    ...t.v.map(([v, kind]) => ({ v, kind, x: B.vertices[v].x, y: B.vertices[v].y })),
    ...t.e.map((e) => {
      const [a, b] = B.edges[e].map((v) => B.vertices[v]);
      return { e, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }),
  ];
  // reading order: top to bottom, then left to right, so codes are easy to find
  items.sort((a, b) => Math.round(a.y / 20) - Math.round(b.y / 20) || a.x - b.x);
  const two = items.length > KEY_ABC.length;
  items.forEach((it, i) => {
    it.code = two ? KEY_ABC[Math.floor(i / KEY_ABC.length)] + KEY_ABC[i % KEY_ABC.length] : KEY_ABC[i];
  });
  return items;
}

function renderKeys() {
  const g = els.keys;
  g.replaceChildren();
  keyMap = keyTargets();
  if (keyBuf && !keyMap.some((k) => k.code.startsWith(keyBuf))) keyBuf = "";
  for (const k of keyMap) {
    const on = keyBuf && k.code.startsWith(keyBuf);
    const kg = svg("g", { class: `key-tag ${on ? "on" : keyBuf ? "dim" : ""}`, transform: `translate(${k.x.toFixed(1)} ${(k.v !== undefined ? k.y - 20 : k.y).toFixed(1)})` }, g);
    const w = 8 + 7 * k.code.length;
    svg("rect", { x: -w / 2, y: -8, width: w, height: 15, rx: 4 }, kg);
    const tx = svg("text", { y: 3.5 }, kg);
    tx.textContent = k.code.toUpperCase();
  }
}

function pickKey(ch) {
  if (!keyMap.length) return false;
  const next = keyBuf + ch;
  const hits = keyMap.filter((k) => k.code.startsWith(next));
  if (!hits.length) return false;
  const exact = hits.find((k) => k.code === next);
  if (exact && hits.length === 1) {
    keyBuf = "";
    renderKeys();
    if (exact.hex !== undefined) {
      // the victim menu opens where the tag sits on screen
      const box = $("#board").getBoundingClientRect(), vb = $("#board").viewBox.baseVal;
      const s = Math.min(box.width / vb.width, box.height / vb.height);
      moveRobber(exact.hex, { clientX: box.left + box.width / 2 + exact.x * s, clientY: box.top + box.height / 2 + exact.y * s });
    } else if (exact.v !== undefined) act([exact.kind, exact.v]);
    else act(["road", exact.e]);
    return true;
  }
  keyBuf = next;
  renderKeys();
  return true;
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
    // a player within two points of winning is the one to stop (bots may also hold hidden VP cards)
    const close = S.phase !== "game_over" && pl.vp >= 8;
    el.classList.toggle("threat", close && p !== S.human);
    if (close) badges.push(`<span class="badge near">${10 - pl.vp} VP from winning${pl.bot ? " (or fewer)" : ""}</span>`);
    const owe = S.discards_pending[p] ? ` · discarding ${S.discards_pending[p]}` : "";
    const inc = production(p).total;
    const perRoll = (inc / 36).toFixed(1);
    const race = Array.from({ length: 10 }, (_, k) => `<i class="${k < pl.vp ? "on" : ""}"></i>`).join("");
    const html = `
      <div class="p-head">
        <span class="p-name">${esc(pl.name)}</span>
        <span class="p-tag">${pl.bot ? esc(S.level) : "you"}</span>
        <span class="p-vp">${hid}<b>${pl.vp}</b><small>VP</small></span>
      </div>
      <div class="p-race" title="${pl.vp} of 10 victory points${pl.bot ? " (hidden VP cards not shown)" : ""}">${race}</div>
      <div class="p-stats">
        <span class="st-cards ${pl.cards > 7 ? "warn" : ""}" title="Resource cards in hand${pl.cards > 7 ? " — over 7, exposed to a 7" : ""}"><b>${pl.cards}</b>&nbsp;card${pl.cards === 1 ? "" : "s"}</span>
        <span title="Development cards held"><b>${pl.devs}</b>&nbsp;dev</span>
        <span title="Knights played (3+ and the most wins Largest Army)"><b>${pl.knights}</b>&nbsp;kn</span>
        <span title="Longest road (5+ and the longest wins Longest Road)">road&nbsp;<b>${pl.road}</b></span>
        <span title="Income: ${inc} pips — about ${(inc / 36).toFixed(2)} cards per roll${p === S.human ? "" : " (from their buildings)"}"><b>${perRoll}</b>/roll</span>
      </div>
      ${badges.length || owe ? `<div class="p-badges">${badges.join("")}${owe ? `<span class="muted" style="font-size:12px">${owe}</span>` : ""}</div>` : ""}
      <div class="p-think"><div class="spinner" style="--pc:${pc(p)}"></div></div>`;
    setHTML(el, html);
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
  const size = S.hand.reduce((a, b) => a + b, 0);
  hand.dataset.warn = size > 7 ? `${size} cards — a 7 costs you ${Math.floor(size / 2)}` : "";
  const dh = $("#devhand");
  const cards = [];
  DEVS.forEach((d, k) => {
    const play = d.play && can(d.play);
    if (S.devs[k]) cards.push(devCard(d, k, S.devs[k], play, false));
    if (S.new_devs[k]) cards.push(devCard(d, k, S.new_devs[k], false, true));
  });
  setHTML(dh, cards.join(""));
}

// why a dev card can't be played right now (shown on the card), or "" if it can / it's not your turn
function devBlock(d, playable, isNew) {
  if (!d.play || playable) return "";
  if (isNew) return "next turn";
  if (!myDecision() || !["main", "pre_roll"].includes(S.phase)) return "";
  if (S.dev_played) return "1 per turn";
  if (d.key === "roadb" && !(S.legal.road || []).length) return "no road spot";
  if (S.phase === "pre_roll") return "after roll";
  return "not now";
}

function devCard(d, k, n, playable, isNew) {
  const why = devBlock(d, playable, isNew);
  const tip = isNew ? `${d.name} — bought this turn, playable next turn` : playable ? `Play ${d.name} (${d.hot})` : d.key === "vp" ? "Victory point (counts automatically)"
    : `${d.name}${why === "1 per turn" ? " — you already played a development card this turn" : why ? ` — ${why}` : ""}`;
  return `<button class="dcard ${d.key === "vp" ? "vp" : ""} ${playable ? "playable" : ""} ${isNew ? "new" : ""} ${why ? "blocked" : ""}" data-dev="${k}" ${playable ? `data-key="${d.hot}"` : "tabindex=-1"} title="${esc(tip)}">${icon(d.icon)}<span class="lbl">${d.name}</span>${n > 1 ? `<span class="n">${n}</span>` : ""}${why ? `<span class="why-tag">${why}</span>` : ""}</button>`;
}

// [button, legal kind, cost, tooltip]
const BUILD_BUTTONS = [
  ["#btn-road", "road", "road", "Build a road (1)"],
  ["#btn-settle", "settle", "settle", "Build a settlement (2)"],
  ["#btn-city", "city", "city", "Upgrade to a city (3)"],
  ["#btn-dev", "buy_dev", "dev", "Buy a development card (D)"],
];

function renderActions() {
  const mine = myDecision();
  const pre = mine && S.phase === "pre_roll";
  const main = mine && S.phase === "main";
  const roll = $("#btn-roll");
  roll.disabled = !can("roll") || busy;
  roll.classList.toggle("pulse", pre);
  // the tooltip says why a button is off
  const why = (kind, cost, base) => {
    if (!mine) return `${base} — not your turn`;
    if (S.phase !== "main") return `${base} — ${S.phase === "pre_roll" ? "roll first" : "finish the current step first"}`;
    const miss = cost.map((n, r) => Math.max(0, n - S.hand[r]));
    if (miss.some(Boolean)) return `${base} — need ${miss.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(", ")}`;
    if (!can(kind)) return `${base} — ${kind === "buy_dev" ? "deck is empty" : kind === "city" ? "no settlement to upgrade" : "no legal spot (extend your roads)"}`;
    return base;
  };
  for (const [id, kind, cost, label] of BUILD_BUTTONS) {
    const b = $(id), ok = main && can(kind);
    b.disabled = !ok || busy;
    b.classList.toggle("ready", ok);
    if (b.dataset.mode) b.setAttribute("aria-pressed", String(mode === b.dataset.mode));
    b.title = why(kind, COSTS[cost], label);
    // costs: one icon per card, dimmed where your hand comes up short
    let c = b.querySelector(".cost");
    if (!c) { c = document.createElement("span"); c.className = "cost"; b.append(c); }
    setHTML(c, COSTS[cost].flatMap((n, r) => Array.from({ length: n }, (_, k) => resIcon(r, `ri ${k < S.hand[r] ? "" : "miss"}`))).join(""));
  }
  $("#btn-trade").disabled = !main || busy;
  $("#btn-roll").title = can("roll") ? "Roll the dice (R)" : mine && S.phase === "main" ? "Already rolled" : "Roll the dice — not now";
  // Roll and End turn share one slot: the next step is always in the same place
  roll.hidden = !pre;
  const end = $("#btn-end");
  end.hidden = pre;
  end.disabled = !can("end") || busy;
  end.classList.toggle("pulse", main && (S.legal.kinds || []).every((k) => k === "end" || k === "bank"));
}

// main phase, no build mode: what you can build now, else what's closest
function mainStatus() {
  const ready = BUILD_BUTTONS.filter(([, kind]) => can(kind));
  if (ready.length) return `Build: ${ready.map(([id, , cost]) => `${BUILD_NAMES[cost]} <kbd>${$(id).dataset.key}</kbd>`).join(" · ")}`;
  const near = BUILD_BUTTONS.map(([, , cost]) => [cost, tradesShort(S.hand, COSTS[cost])]).filter(([, n]) => n > 0 && n < Infinity).sort((a, b) => a[1] - b[1])[0];
  if (near) return `${BUILD_NAMES[near[0]]} is ${near[1]} bank trade${near[1] > 1 ? "s" : ""} away <kbd>T</kbd>`;
  return "Nothing to build — trade <kbd>T</kbd> or end turn <kbd>E</kbd>";
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
      case "setup_settlement": text = `Place your ${S.setup_idx < S.n ? "first" : "second"} settlement — ${TOUCH ? "tap a spot" : "click or type its letters"}`; break;
      case "setup_road": text = `Place a road next to it — ${TOUCH ? "tap one" : "click or type its letter"}`; break;
      case "pre_roll": text = (L.kinds || []).length > 1 ? "Roll the dice — or play a development card first" : "Roll the dice"; break;
      case "main": text = mode ? `${BUILD_NAMES[mode]}: ${TOUCH ? "tap a spot · tap the button again to cancel" : "click a spot or type its letter · Esc to cancel"}` : mainStatus(); break;
      case "discard": text = `Discard ${S.discard_owed} cards`; break;
      case "move_robber": text = `Move the robber — ${TOUCH ? "tap a hex" : "click a hex or type its letter"}`; break;
      case "road_building": text = `Place a free road (${S.free_roads} left)`; break;
      default: text = S.phase;
    }
  } else {
    p = thinker ?? S.actor;
    spin = thinker !== null && thinker !== undefined;
    text = S.phase === "discard" ? `${name(p)} is discarding…` : S.phase.startsWith("setup") ? `${name(p)} is placing…` : `${name(p)} is thinking…`;
  }
  const html = `<div class="pill ${mine ? "mine" : ""}" style="--pc:${pc(p)}">${spin ? `<span class="spinner"></span>` : `<span class="dot"></span>`}<span>${text}</span><span class="muted" style="font-weight:500">· ${roundName()}</span></div>`;
  setHTML($("#status"), html);
}

// a touch-only device: no hover, no keyboard badges, "tap" in the copy
const TOUCH = matchMedia("(hover: none) and (pointer: coarse)").matches;
const roundName = () => (S.round ? `round ${S.round}` : "setup");

const PIP_LAYOUT = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
function renderDice(animate) {
  const box = $("#dice");
  const r = S.last_roll;
  if (!r) { box.replaceChildren(); return; }
  const [d1, d2, total] = r;
  const die = (n, red) => `<div class="die ${red ? "red" : ""}">${Array.from({ length: 9 }, (_, i) => `<i class="${(PIP_LAYOUT[n] || []).includes(i) ? "on" : ""}"></i>`).join("")}</div>`;
  const who = lastRoller === null || lastRoller === undefined ? "" : lastRoller === S.human ? "You rolled" : `${esc(S.players[lastRoller].name)} rolled`;
  const html = (who ? `<span class="d-who">${who}</span>` : "") + (d1 ? die(d1, false) + die(d2, true) : "") + `<span class="total ${total === 7 ? "seven" : ""}">${total}</span>`;
  // before a roll the dice on show are the previous player's
  box.classList.toggle("stale", S.phase === "pre_roll" || S.phase.startsWith("setup"));
  box.title = box.classList.contains("stale") ? `Last roll: ${who.replace(" rolled", "")} — not yours this turn` : "";
  // fresh dice elements replay the tumble animation
  if (animate) { box.dataset.html = ""; box.classList.add("rolling"); }
  setHTML(box, html);
}

function renderBank() {
  const html = RES.map((r, i) => `<div class="bk" title="${S.bank[i]} ${r} in the bank">${resIcon(i)}${S.bank[i]}</div>`).join("") +
    `<div class="bk" title="${S.deck} development cards left">${icon("i-dev", "ri")}${S.deck}</div>`;
  setHTML($("#bank"), html);
}

// Your economy at a glance: what each resource earns, on which numbers, and your trade rate.
function renderEcon() {
  const pr = production(S.human);
  const top = Math.max(10, ...pr.pips.map((n, r) => n + pr.blocked[r]));
  const rows = RES.map((r, i) => {
    const n = pr.pips[i], bl = pr.blocked[i];
    const nums = pr.nums[i].sort((a, b) => b.pips - a.pips).map((x) => `<span class="ec-num ${x.n === 6 || x.n === 8 ? "hot" : ""}">${x.n}${x.lv > 1 ? "<sup>×2</sup>" : ""}</span>`).join("");
    const ratio = S.ratios[i];
    return `<div class="ec-row ${n ? "" : "none"}" title="${r}: ${n} pips${bl ? ` (+${bl} blocked by the robber)` : ""} · trade ${ratio}:1">
      ${resIcon(i)}
      <span class="ec-bar"><i style="width:${(100 * n) / top}%;background:var(--${r})"></i>${bl ? `<i class="bl" style="width:${(100 * bl) / top}%"></i>` : ""}</span>
      <span class="ec-n">${n || "—"}</span>
      <span class="ec-nums">${nums || (bl ? `<span class="muted">blocked</span>` : `<span class="muted">no income</span>`)}</span>
      <span class="ec-ratio ${ratio < 4 ? "port" : ""}">${ratio}:1</span></div>`;
  }).join("");
  const html = rows + `<div class="ec-foot">Pips = cards per 36 rolls · numbers you collect on · trade rate</div>`;
  setHTML($("#econ"), html);
  $("#econ-sum").textContent = `≈ ${(pr.total / 36).toFixed(1)} cards / roll`;
}

// ------------------------------------------------------------------ log
// The log follows the newest line unless you scrolled up yourself; scrolling back to
// the bottom re-pins it. Only scrolls you make count, so the box shrinking (e.g. while
// the coach panel grows) never unpins it.
let logTouched = 0;
function pinLog() { if (logStick) { const log = $("#log"); log.scrollTop = log.scrollHeight; } }
function wireLog() {
  const log = $("#log");
  const touch = () => { logTouched = Date.now(); };
  for (const t of ["wheel", "touchstart", "touchmove", "pointerdown", "keydown"]) log.addEventListener(t, touch, { passive: true });
  log.addEventListener("scroll", () => {
    if (Date.now() - logTouched > 1500) return; // our own pinning, or a layout change
    logStick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  });
  new ResizeObserver(pinLog).observe(log);
}

function appendLog(evs) {
  const log = $("#log");
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
    const rm = msg.match(/robber to ([A-Z])$/);
    if (rm) {
      const h = B.hexes.findIndex((x) => x.label === rm[1]);
      if (h >= 0) li.dataset.h = h;
      msg = msg.replace(/[A-Z]$/, hexName(rm[1]));
    }
    if (e.kind === "turn") {
      li.textContent = msg.replace(/^-+ | -+$/g, "");
    } else {
      li.innerHTML = `<span class="ic">${LOG_ICONS[e.kind] || "·"}</span><span>${esc(msg)}</span>${e.why ? `<span class="why">${esc(plain(e.why))}</span>` : ""}`;
    }
    frag.appendChild(li);
  }
  log.appendChild(frag);
  while (log.children.length > 500) log.firstChild.remove();
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
      const c = parseCards(e.msg);
      const total = c.reduce((a, b) => a + b, 0);
      if (e.p !== null && total) delta($("#players").children[e.p], e.kind === "discard" ? -total : total);
      if (e.p === S.human && e.kind === "produce") {
        c.forEach((n, r) => {
          if (!n) return;
          flash($("#hand").children[r], "bump", 500);
          delta($("#hand").children[r], n, "card-delta");
        });
      }
    }
    if (e.kind === "steal" || e.kind === "private_steal") {
      const tail = e.msg.replace(/\)$/, "").split(" from ").at(-1).toLowerCase();
      const victim = e.victim ?? (tail === "you" ? S.human : S.players.findIndex((pl) => pl.name.toLowerCase() === tail));
      if (victim >= 0) flash($("#players").children[victim], "shake", 450);
    }
    if (e.kind === "award") toast(e.msg, "", pc(e.p ?? 0));
    if (e.kind === "private" && /you drew/.test(e.msg)) toast("Development card", e.msg.replace(/[()]/g, ""), "#9c88e0");
    if (e.kind === "private_steal") toast("Steal", e.msg.replace(/[()]/g, ""), "#e59cf2");
  }
}

// "While you waited": what changed for you and what the others did since your last turn.
// Your card changes come from comparing hands (so steals, discards, monopolies and
// accepted offers all count); the others' actions come from the log.
let recapBuf = [];
let recapTurnSeen = null;
let recapHand = null; // your hand as your last turn ended
function recapTurn(fresh, initial) {
  const mineNow = S.current === S.human;
  if (initial) { recapBuf = []; recapTurnSeen = mineNow ? S.turn : null; recapHand = S.hand.slice(); return; }
  recapBuf.push(...fresh);
  if (!mineNow || S.phase.startsWith("setup") || recapTurnSeen === S.turn) {
    if (mineNow) recapHand = S.hand.slice();
    return;
  }
  recapTurnSeen = S.turn;
  const before = recapHand || S.hand;
  recapHand = S.hand.slice();
  const evs = recapBuf.filter((e) => e.turn < S.turn);
  recapBuf = recapBuf.filter((e) => e.turn >= S.turn);
  const diff = S.hand.map((n, r) => n - before[r]);
  const got = diff.map((d) => Math.max(0, d)), lost = diff.map((d) => Math.max(0, -d));
  const acts = new Map(); // player -> list of things they did
  const did = (p, s) => { if (p === null || p === S.human) return; if (!acts.has(p)) acts.set(p, []); acts.get(p).push(s); };
  let robbedBy = null;
  for (const e of evs) {
    const m = e.msg;
    if (e.kind === "steal" && /from you$/i.test(m)) robbedBy = e.p;
    else if (e.kind === "build") did(e.p, /road/.test(m) ? "road" : /city/.test(m) ? "city" : "settlement");
    else if (e.kind === "dev") did(e.p, /buys/.test(m) ? "dev card" : m.replace(/^\S+ plays? /, "played ").replace(/ \(.*\)$/, ""));
    else if (e.kind === "robber" && e.p !== S.human) did(e.p, `robber → ${hexName((m.match(/robber to ([A-Z])/) || [])[1] || "?")}`);
    else if (e.kind === "trade") did(e.p, /bank/.test(m) ? "bank trade" : "trade");
  }
  const cards = (c, sign) => c.map((n, r) => (n ? `<span class="rc-chip ${sign < 0 ? "neg" : ""}">${resIcon(r)}${sign < 0 ? "−" : "+"}${n}</span>` : "")).join("");
  const count = (list) => {
    const m = new Map();
    for (const x of list) m.set(x, (m.get(x) || 0) + 1);
    return [...m].map(([k, n]) => (n > 1 ? `${n} ${k === "city" ? "cities" : k + "s"}` : k)).join(", ");
  };
  const lines = [...acts].map(([p, l]) => `<div class="rc-line"><span class="dot" style="background:${pc(p)}"></span><b>${esc(S.players[p].name)}</b> ${esc(count(l))}</div>`);
  const mine = cards(got, 1) + cards(lost, -1);
  if (!mine && !lines.length && robbedBy === null) return;
  recap(`${mine ? `<div class="rc-mine">${mine}</div>` : `<div class="rc-mine muted">No cards for you this round</div>`}${robbedBy !== null ? `<div class="rc-line warn">${esc(S.players[robbedBy].name)} robbed you</div>` : ""}${lines.join("")}`);
}

function recap(html) {
  const box = $("#recap");
  box.innerHTML = `<div class="rc-head">While you waited<button class="t-x" aria-label="Dismiss">✕</button></div>${html}`;
  box.querySelector(".t-x").onclick = () => box.classList.remove("show");
  box.classList.remove("show"); void box.offsetWidth; box.classList.add("show");
  clearTimeout(recap.t);
  recap.t = setTimeout(() => box.classList.remove("show"), 9000);
}

function flash(el, cls, ms) {
  if (!el) return;
  el.classList.remove(cls);
  void el.getBoundingClientRect();
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

// Monopoly, Year of Plenty and steals change your hand outside production: float the
// change off each card (production already animates itself)
function handGains(evs) {
  if (!prev || !evs.some((e) => e.kind === "private_steal" || (e.kind === "dev" && /Monopoly|Year of Plenty/.test(e.msg)))) return;
  const produced = [0, 0, 0, 0, 0];
  for (const e of evs) if (e.kind === "produce" && e.p === S.human) parseCards(e.msg).forEach((n, r) => { produced[r] += n; });
  S.hand.forEach((n, r) => {
    const d = n - prev.hand[r] - produced[r];
    if (!d) return;
    flash($("#hand").children[r], "bump", 500);
    delta($("#hand").children[r], d, "card-delta");
  });
}

// a "+2" / "−1" that floats up off a card
function delta(card, n, cls = "") {
  if (!card) return;
  const d = document.createElement("span");
  d.className = `delta ${cls} ${n < 0 ? "neg" : ""}`;
  d.textContent = (n > 0 ? "+" : "") + n;
  card.appendChild(d);
  setTimeout(() => d.remove(), 1400);
}

// ------------------------------------------------------------------ toasts
function modal(dlg) {
  dlg.showModal();
  // without an explicit autofocus the browser focuses the first button (often ✕),
  // and Enter would press that instead of the dialog's ↵ action
  if (!dlg.querySelector("[autofocus]")) { dlg.tabIndex = -1; dlg.focus(); }
  raiseToasts();
}
function raiseToasts() {
  const b = $("#toasts");
  try { if (b.matches(":popover-open")) b.hidePopover(); b.showPopover(); } catch { /* unsupported */ }
  placeToasts();
}
// on wide screens toasts sit in the board's lower-left corner (open sea), off the panels you read
function placeToasts() {
  const b = $("#toasts");
  if (innerWidth < 900) { b.style.inset = ""; b.style.width = ""; return; }
  const r = $(".board-wrap").getBoundingClientRect();
  b.style.inset = `auto auto ${Math.max(8, innerHeight - r.bottom + 8)}px ${r.left + 6}px`;
  b.style.width = `${Math.min(340, r.width * 0.55)}px`;
}
function toast(title, sub = "", color = "var(--accent)", ms = 4200) {
  const box = $("#toasts");
  if (!box.matches(":popover-open") || $("dialog[open]")) raiseToasts();
  else placeToasts();
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
  // only real slips get a toast; best and good moves are listed in the coach panel
  for (const r of $("dialog[open]") ? [] : scored(revs.slice(reviewsSeen)).filter((x) => x.delta >= 0.2)) {
    toast(`${symbol(r.verdict)} ${r.verdict}  −${r.delta.toFixed(2)}`, `You: ${short(r.label)}\nBetter: ${short(r.best)}`, VERDICT_COLORS[r.verdict], 7000);
  }
  reviewsSeen = revs.length;
}
const plain = (s) => String(s).replace(/\brobber (to|on) ([A-Z])(?: \([^)]*\))?/g, (_, w, l) => `robber ${w} ${hexName(l)}`).replace(/vertex \d+ ?/g, "").replace(/\b(road|settlement at|city at) \d+/g, "$1").replace(/settlement at \[/g, "settlement [").replace(/city at \[/g, "city [");
const symbol = (v) => ({ forced: "·", best: "✓", good: "✓", inaccuracy: "?!", mistake: "?", blunder: "??" })[v];
const engineLine = (r) => r.forced ? "no real choice" : r.delta < 0.05 ? "engine agrees" : `better: ${short(r.best)}`;
const scored = (revs) => revs.filter((r) => !r.forced);
const accuracy = (real) => (real.length ? Math.round((100 * real.filter((r) => r.delta < 0.2).length) / real.length) : 100);
const totalLost = (real) => real.reduce((a, r) => a + r.delta, 0);

function renderReviews() {
  const revs = S.reviews;
  const ol = $("#reviews");
  const key = revs.length + ":" + (revs.at(-1)?.delta ?? "");
  if (ol.dataset.sig === key) return;
  ol.dataset.sig = key;
  $("#btn-review").disabled = !revs.length;
  if (!revs.length) { ol.replaceChildren(); $("#accuracy").textContent = ""; return; }
  const real = scored(revs);
  $("#accuracy").innerHTML = real.length ? `Accuracy <b>${accuracy(real)}%</b> of ${real.length} decisions · lost <b>${totalLost(real).toFixed(2)}</b> VP-eq` : "";
  // forced moves only appear in the full "All moves" table
  ol.innerHTML = real.slice().reverse().slice(0, 60).map(reviewItem).join("");
}

function reviewItem(r) {
  return `<li class="rev v-${r.verdict}" ${r.action ? `data-a='${JSON.stringify(r.action)}'` : ""} ${r.best_action ? `data-b='${JSON.stringify(r.best_action)}'` : ""}>
    <span class="vd vd-${r.verdict}" title="${r.verdict}">${symbol(r.verdict)}</span>
    <span class="rev-move">${esc(short(r.label))}</span>
    <span class="loss-n">${r.delta >= 0.05 ? "−" + r.delta.toFixed(2) : ""}</span>
    ${r.delta >= 0.05 ? `<span class="best-line">${esc(engineLine(r))}</span>` : ""}</li>`;
}

function showFullReview() {
  const revs = S?.reviews || [];
  const real = scored(revs);
  const counts = {};
  for (const r of real) counts[r.verdict] = (counts[r.verdict] || 0) + 1;
  const rows = revs.map((r) => `<tr class="${r.forced ? "forced" : ""}">
      <td class="muted">${r.turn ? r.turn : "setup"}</td>
      <td>${esc(plain(r.label))}</td>
      <td><span class="vd vd-${r.verdict}">${symbol(r.verdict)} ${r.verdict}</span></td>
      <td class="num">${r.forced ? "" : r.delta < 0.005 ? "0" : "−" + r.delta.toFixed(2)}</td>
      <td class="muted">${r.forced ? "—" : r.delta < 0.05 ? "same as yours" : esc(human(r.best))}</td></tr>`).join("");
  openInfo("Every move, rated", `
    <p class="muted">Each of your decisions compared with the engine's best line, in VP-equivalents lost. Forced moves (only one sensible choice) are listed but not scored.</p>
    <div class="vcounts">${["best", "good", "inaccuracy", "mistake", "blunder"].map((k) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${counts[k] || 0}</span>`).join("")}</div>
    <p>Accuracy <b>${accuracy(real)}%</b> · total lost <b>${totalLost(real).toFixed(2)}</b> VP-eq · ${revs.length} moves</p>
    <div class="table-scroll"><table class="info-table review-table"><thead><tr><th>Turn</th><th>Your move</th><th>Verdict</th><th>Lost</th><th>Engine's best</th></tr></thead><tbody>${rows}</tbody></table></div>`, true);
}

async function showHint() {
  if (!S) return;
  if (!myDecision() && !S.pending_offer) { toast("Hints are for your decisions", "Wait for your turn.", "var(--muted)", 2200); return; }
  if (S.pending_offer) return askCoachOffer();
  await coachPanel("Thinking…", async (out) => {
    const st = await api("hint");
    apply(st);
    const h = st.hint;
    // lines that read the same are the same move for you: keep the better one
    const seen = new Set();
    const opts = h.options.filter((o) => { const t = human(o.text); if (seen.has(t)) return false; seen.add(t); return true; });
    if (!opts.length) return `<p class="coach-note">Nothing to decide here.</p>`;
    out._opts = opts;
    optSel = null;
    const how = matchMedia("(hover: hover)").matches ? "hover or select one" : "tap one";
    return `<p class="coach-note">${CLOSE_COACH}Engine's top options (${h.secs.toFixed(1)}s) — ${how} to see it on the board</p><ol class="opts">${opts.map((o, i) => `
      <li class="opt" data-i="${i}" tabindex="0"><span class="loss ${i === 0 ? "best" : o.loss < 0.5 ? "near" : "far"}">${i === 0 ? "best" : o.loss < 0.005 ? "≈ best" : "−" + o.loss.toFixed(2)}</span><span>${esc(human(o.text))}</span>${playButton(o, i)}</li>`).join("")}</ol>`;
  });
  // on a phone the coach sits below the board: bring the answer into view
  if (!onScreen($("#coach-out"))) $("#coach-out").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// "Play" carries out the whole line when it can (bank trades plus the build they pay for);
// a longer plan plays its first step and says so
function playButton(o, i) {
  const steps = o.steps || [o.action];
  if (!steps.every(playableHint)) return "<span></span>";
  const what = steps.map((a) => human(describeStep(a))).join(", then ");
  return `<button class="btn do" data-i="${i}" data-key="⇧${i + 1}" title="${esc(`Plays: ${what}${o.more ? " — ask again for the rest" : ""}`)}">${o.more ? "Play step 1" : "Play"}</button>`;
}

// enough of the engine's notation for human() to read a single step
function describeStep(a) {
  switch (a[0]) {
    case "bank": return `trade ${S.ratios[a[1]]} ${RES[a[1]]} -> ${RES[a[2]]}`;
    case "road": return `road ${a[1]}`;
    case "settle": return `settlement [${spotName(a[1])}]`;
    case "city": return `city [${spotName(a[1])}]`;
    case "buy_dev": return "buy development card";
    case "robber": return `robber to ${B.hexes[a[1]].label}${a[2] >= 0 ? `, rob ${S.players[a[2]].name}` : ""}`;
    default: return a[0].replace("play_", "play ").replace("_", " ");
  }
}

const onScreen = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 40 && r.top < innerHeight - 80; };

// the board spots a line would build on
let optSel = null;
function glowSteps(steps) {
  clearGlow();
  for (const a of steps || []) glowFor(a, true);
}
function optSteps(i) { const o = $("#coach-out")._opts?.[i]; return o ? o.steps || [o.action] : null; }
function selectOpt(li, scroll) {
  $$("#coach-out .opt.sel").forEach((x) => x.classList.toggle("sel", false));
  optSel = li ? Number(li.dataset.i) : null;
  if (!li) return clearGlow();
  li.classList.add("sel");
  glowSteps(optSteps(optSel));
  if (scroll && !onScreen($("#board"))) $(".board-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
}

async function playHint(i) {
  const steps = optSteps(i);
  if (!steps) return;
  glowSteps(steps);
  // on a phone, show where it goes before it happens
  if (!onScreen($("#board"))) {
    $(".board-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
    await sleep(700);
  }
  for (const a of steps) if (!(await act(a))) break;
  clearGlow();
}

const CLOSE_COACH = `<button class="co-x" aria-label="Close" title="Back to your move reviews (Esc)">✕</button>`;

// fill the coach panel with what load() returns; it stays until the decision changes
async function coachPanel(loading, load) {
  const out = $("#coach-out");
  out.innerHTML = `<p class="coach-note">${loading}</p>`;
  try {
    const html = await load(out);
    hintKey = decisionKey();
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<p class="coach-note">${esc(e.message)}</p>`;
  }
}

function playableHint(a) {
  return a && !["macro", "discard", "trade", "propose"].includes(a[0]);
}

async function askCoachOffer() {
  const box = $("#bo-hint");
  const offer = JSON.stringify(S.pending_offer);
  box.textContent = "Coach is thinking…";
  try {
    const st = await api("hint");
    if (JSON.stringify(S.pending_offer) !== offer) return; // answered meanwhile
    const h = st.hint;
    box.innerHTML = `Engine: <b style="color:${h.accept ? "var(--good)" : "var(--worst)"}">${h.accept ? "ACCEPT" : "DECLINE"}</b> · net value for you ${h.gain >= 0 ? "+" : ""}${h.gain.toFixed(2)} VP-eq <span class="muted">(includes what they build with it)</span>`;
  } catch (e) {
    box.textContent = e.message;
  }
}

async function showEval() {
  if (!S) return;
  await coachPanel("Evaluating…", async () => {
    const rows = (await api("eval")).eval.rows;
    const top = Math.max(1, ...rows.map((r) => r.value));
    return `<p class="coach-note">${CLOSE_COACH}Engine evaluation from your point of view (VP-equivalents). Pips = expected cards per 36 rolls.</p><div class="evals">${rows.map((r) => `
      <div class="ev-row" style="--pc:${pc(r.p)}"><b>${esc(S.players[r.p].name)}</b><span class="bar"><i style="width:${Math.max(2, (100 * r.value) / top)}%"></i></span><span>${r.value.toFixed(2)}</span>
      <span class="pips">${r.pips.map((n, i) => `<span>${resIcon(i)}${Math.round(n)}</span>`).join("")}<span>Σ${Math.round(r.pips.reduce((a, b) => a + b, 0))}</span></span></div>`).join("")}</div>`;
  });
}

function glowFor(a, keep = false) {
  if (!keep) clearGlow();
  if (!a || a[0] === "macro") return;
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
  if (v) act([v.dataset.kind, Number(v.dataset.v)]); // kind: settle | city
  else if (e) act(["road", Number(e.dataset.e)]);
  else if (h) moveRobber(Number(h.dataset.h), ev);
  else if (mode) {
    mode = null;
    render();
  }
}

// with two or more players to rob, ask which one (menu at the click point)
function moveRobber(hex, at) {
  const vics = S.legal.robber[hex] || [];
  if (vics.length <= 1) act(["robber", hex, vics[0] ?? -1]);
  else chooseVictim(hex, vics, at);
}

function chooseVictim(hex, vics, ev) {
  const pop = $("#victims");
  pop.innerHTML = `<h3>Steal from…</h3>` + vics.map((q, i) => `<button class="btn ghost" data-q="${q}" data-key="${i + 1}" style="--pc:${pc(q)}"><span class="dot"></span>${esc(S.players[q].name)} <span class="muted">${S.players[q].cards} cards · ${S.players[q].vp} VP</span></button>`).join("");
  pop.style.left = `${Math.min(ev.clientX + 8, innerWidth - 240)}px`;
  pop.style.top = `${Math.min(ev.clientY + 8, innerHeight - 160)}px`;
  pop.onclick = (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    pop.hidePopover();
    act(["robber", hex, Number(b.dataset.q)]);
  };
  pop.showPopover();
  pop.querySelector("button")?.focus();
}

function onBoardHover(ev) {
  const tip = $("#tip");
  const v = ev.target.closest("[data-v]");
  const hx = ev.target.closest("[data-h]");
  let html = null;
  if (v) {
    const vx = B.vertices[Number(v.dataset.v)];
    const what = { settle: "Build a settlement", city: "Upgrade to a city" }[v.dataset.kind];
    const lines = (vx.hexes || []).map((h) => B.hexes[h]).filter((h) => h.res)
      .map((h) => `<span class="tt-line">${resIcon(RES.indexOf(h.res))}<b>${h.num}</b> ${h.res} <span class="tt-pips">${"•".repeat(h.pips)}</span></span>`).join("");
    const port = vx.port ? `<span class="tt-line muted">⚓ ${vx.port === "any" ? "3:1 port" : `2:1 ${vx.port} port`}</span>` : "";
    const gain = what ? `<span class="muted">+${vx.pips} pips to your income</span>` : `<span class="muted">${vx.pips} pips</span>`;
    html = `${what ? `<b style="color:var(--accent)">${what}</b><br>` : ""}${lines || "<span class='muted'>coast</span>"}${port}${gain}`;
  } else if (ev.target.closest(".e-target")) {
    html = `<b style="color:var(--accent)">Build a road</b><br><span class="muted">wood + brick</span>`;
  } else if (hx) {
    const i = Number(hx.dataset.h), h = B.hexes[i];
    const robbing = !!ev.target.closest(".hex-target");
    html = h.res ? `${robbing ? `<b style="color:var(--accent)">Move the robber here</b><br>` : ""}<b>${h.num} ${h.res}</b> <span class="muted">(${h.pips} pips)</span>` : "<b>Desert</b>";
    const hit = robberHits(i);
    const who = Object.keys(hit).map(Number);
    if (who.length) html += who.map((p) => `<span class="tt-line"><span class="dot" style="background:${pc(p)}"></span>${esc(S.players[p].name)} <span class="muted">${robbing ? "loses" : "earns"} ${hit[p]} pips</span></span>`).join("");
    else if (h.res) html += `<span class="tt-line muted">no buildings</span>`;
    if (robbing && hit[S.human]) html += `<span class="tt-line" style="color:var(--bad)">blocks your own income</span>`;
    const vics = robbing ? S.legal.robber?.[i] || [] : [];
    if (vics.some((q) => q !== -1)) html += `<span class="tt-line muted">steal from ${vics.filter((q) => q !== -1).map((q) => `${esc(S.players[q].name)} (${S.players[q].cards})`).join(" or ")}</span>`;
    if (i === S?.robber) html += `<span class="tt-line muted">robber is here</span>`;
  }
  if (!html) { tip.classList.remove("show"); return; }
  tip.innerHTML = html;
  tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - 290)}px`;
  tip.style.top = `${ev.clientY + 26}px`;
  tip.classList.add("show");
}

// ------------------------------------------------------------------ dialogs
function stepperRow(r, max, onChange) {
  const row = document.createElement("div");
  row.className = `stepper r-${RES[r]}`;
  row.innerHTML = `${resIcon(r)}<span class="name">${RES[r]}</span><span class="have">have ${S.hand[r]}</span><button class="btn" data-d="-1" data-key="⇧${r + 1}" aria-label="fewer ${RES[r]}">−</button><span class="val">0</span><button class="btn" data-d="1" data-key="${r + 1}" aria-label="more ${RES[r]}">+</button>`;
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

// "Your hand" strip shown in every decision dialog. With a delta it previews
// the hand afterwards: changed counts show old → new.
function handStrip(el, delta = [0, 0, 0, 0, 0], label = "Your hand") {
  el.innerHTML = `<span class="th-label">${label}</span>` + RES.map((r, i) => {
    const n = S.hand[i], d = delta[i] || 0, after = n + d;
    const cls = d > 0 ? "up" : d < 0 ? "down" : "";
    return `<span class="th-res ${after ? "" : "zero"} ${cls}" title="${n} ${r}${d ? ` → ${after}` : ""}">${resIcon(i)}<b>${d ? `<s>${n}</s>${after}` : n}</b></span>`;
  }).join("") + handTotal(S.hand.reduce((a, b) => a + b, 0) + delta.reduce((a, b) => a + b, 0)) +
    (delta.some(Boolean) ? `<span class="th-builds">${buildChanges(S.hand, handPlus(S.hand, delta))}</span>`
      : `<span class="th-builds"><span class="th-label">Can pay for</span>${buildChips(S.hand)}</span>`);
}

// card count, with the cost of a 7 once it's over seven
function handTotal(n) {
  return `<span class="th-total ${n > 7 ? "over" : ""}">${n} cards${n > 7 ? ` · a 7 would cost you ${Math.floor(n / 2)}` : ""}</span>`;
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
    handStrip($("#discard-hand"), rows.map((r) => -r.get()), total() ? "Keeping" : "Your hand");
  };
  RES.forEach((_, r) => rows.push(stepperRow(r, () => Math.min(S.hand[r], (rows[r]?.get() ?? 0) + need - total()), sync)));
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
  const offerEv = $$("#log li.k-offer").at(-1);
  $("#bo-why").textContent = prefs.reasons && offerEv?.querySelector(".why") ? `“${offerEv.querySelector(".why").textContent}”` : "";
  handStrip($("#bo-hand"), RES.map((_, r) => po.give[r] - po.get[r]), "If you accept");
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
  askCoachOffer(); // the verdict is cheap: show it straight away
}

// 20 bank cells, row by row; J K L are the reply buttons, digits the offer tiles
const BANK_KEYS = "ABCDEFGHIMNOPQRSTUVW";

// Trade dialog: bank/ports and player offers side by side at a fixed size.
// Give/want tiles add one card per click; the small minus takes one back.
const trade = { give: [0, 0, 0, 0, 0], want: [0, 0, 0, 0, 0], replies: null, note: "", sent: null };

function clearOffer(note = "") {
  trade.give = [0, 0, 0, 0, 0];
  trade.want = [0, 0, 0, 0, 0];
  trade.replies = null;
  trade.note = note;
  renderTrade();
}

function openTrade() {
  if (!myDecision() || S.phase !== "main") return;
  clearOffer();
  modal($("#dlg-trade"));
}

function renderTrade() {
  const live = myDecision() && S.phase === "main";
  trade.give = trade.give.map((n, r) => Math.min(n, S.hand[r]));
  $("#trade-count").textContent = `· ${S.hand.reduce((a, b) => a + b, 0)} cards in hand`;

  $("#bank-trade").innerHTML = `<div class="bt-row bt-head"><span>You give · rate</span><span class="bt-get">for 1 of <span class="muted">(→ how many you'd have)</span></span></div>` + RES.map((r, i) => {
    const ratio = S.ratios[i];
    const ok = live && S.hand[i] >= ratio;
    let cell = i * 4; // fixed letters per give/get pair, whatever is affordable
    if (!ok) {
      // can't pay this rate: one compact line instead of a row of dead buttons
      return `<div class="bt-row off"><div class="bt-give">${resIcon(i)}<b class="bt-have">${S.hand[i]}</b><span class="bt-rate ${ratio < 4 ? "port" : ""}">${ratio}:1</span></div><span class="bt-need">${live ? `need ${ratio - S.hand[i]} more ${r} to trade at ${ratio}:1` : "trade after you roll, on your turn"}</span></div>`;
    }
    const unlocks = (j) => {
      const after = S.hand.map((n, k) => n + (k === j) - (k === i ? ratio : 0));
      const gain = Object.keys(COSTS).filter((k) => !covers(S.hand, COSTS[k]) && covers(after, COSTS[k]));
      return gain.length ? `<i class="bt-unlock" title="Lets you pay for: ${gain.map((k) => BUILD_NAMES[k]).join(", ")}">★</i>` : "";
    };
    const gets = RES.map((g, j) => j === i ? `<span class="bt-self" aria-hidden="true">·</span>` :
      `<button class="btn res-btn" data-give="${i}" data-get="${j}" data-key="${BANK_KEYS[cell++]}" ${S.bank[j] > 0 ? "" : "disabled"} title="Give ${ratio} ${r} (${S.hand[i]} → ${S.hand[i] - ratio}), get 1 ${g} (${S.hand[j]} → ${S.hand[j] + 1})${S.bank[j] ? "" : " — bank is out"}" aria-label="Give ${ratio} ${r} for 1 ${g}">${resIcon(j)}<span class="bt-cnt">${S.bank[j] ? `→ ${S.hand[j] + 1}` : "out"}</span>${unlocks(j)}</button>`).join("");
    return `<div class="bt-row"><div class="bt-give">${resIcon(i)}<b class="bt-have">${S.hand[i]}</b><span class="bt-rate ${ratio < 4 ? "port" : ""}">${ratio}:1</span><small class="bt-after">→ ${S.hand[i] - ratio}</small></div>${gets}</div>`;
  }).join("");

  const tile = (side, r) => {
    const n = trade[side][r];
    const max = side === "give" ? S.hand[r] : 9;
    return `<div class="pick ${n ? "on" : ""} r-${RES[r]}">
      <button class="pick-add" data-side="${side}" data-r="${r}" data-key="${side === "give" ? "" : "⇧"}${r + 1}" ${live && n < max ? "" : "disabled"} title="${side === "give" ? "Give" : "Ask for"} one more ${RES[r]} (you have ${S.hand[r]})">${icon("i-" + RES[r])}${n ? `<span class="n">×${n}</span>` : ""}<span class="have">${n ? `${S.hand[r]} → <b>${S.hand[r] + (side === "give" ? -n : n)}</b>` : `have ${S.hand[r]}`}</span></button>
      <button class="pick-sub" data-side="${side}" data-r="${r}" data-d="-1" data-key="⌥${side === "give" ? "" : "⇧"}${r + 1}" ${n ? "" : "hidden"} aria-label="one fewer ${RES[r]}">−</button></div>`;
  };
  $("#offer-give").innerHTML = RES.map((_, r) => tile("give", r)).join("");
  $("#offer-get").innerHTML = RES.map((_, r) => tile("want", r)).join("");
  const fmt = (c) => c.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(" + ");
  const ready = trade.give.some(Boolean) && trade.want.some(Boolean);
  $("#offer-summary").innerHTML = ready ? `<b>${fmt(trade.give)}</b> <span class="muted">for</span> <b>${fmt(trade.want)}</b><div class="th-builds">${buildChanges(S.hand, handPlus(S.hand, trade.want.map((n, r) => n - trade.give[r])))}</div>`
    : `<span class="muted">${trade.give.some(Boolean) ? "Now pick what you want" : "Click your cards to give, then what you want"}</span>`;
  $("#offer-send").disabled = !live || !ready || busy;
  $("#offer-clear").disabled = !trade.give.some(Boolean) && !trade.want.some(Boolean);

  const box = $("#offer-replies");
  if (trade.replies) {
    let nth = 0;
    box.innerHTML = trade.replies.map((r) => `<div class="reply" style="--pc:${pc(r.p)}"><span class="dot"></span><b>${esc(S.players[r.p].name)}</b>
      ${r.why ? `<span class="why muted">${esc(r.why)}</span>` : ""}
      <span class="ans ${r.ok ? "yes" : "no"}">${r.ok ? "accepts" : "declines"}</span>${r.ok ? `<button class="btn primary" data-q="${r.p}" data-key="${"JKL"[nth++]}" ${live ? "" : "disabled"}>Trade</button>` : ""}</div>`).join("");
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
    return clearOffer(`Traded with ${name}.`);
  }
  if (e.target.closest("#offer-send")) return sendOffer();
  if (e.target.closest("#offer-clear")) clearOffer();
}

function openPick(kind) {
  const dlg = $("#dlg-pick");
  const yop = kind === "yop";
  $("#pick-title").textContent = yop ? "Year of Plenty" : "Monopoly";
  $("#pick-sub").textContent = yop ? "Take any two resources from the bank (the same one twice is fine)." : "Every opponent hands you all their cards of one resource. ≈ is what your card tracker reckons they hold between them.";
  const haul = (r) => Math.round(S.tracker?.[r] ?? 0);
  const picks = [];
  const box = $("#pick-res");
  const draw = () => {
    box.innerHTML = RES.map((r, i) => {
      const n = picks.filter((x) => x === i).length;
      const off = yop && S.bank[i] < 1;
      const est = yop || !S.tracker ? "" : `<span class="haul" title="Your tracker: opponents hold about ${S.tracker[i]} ${r}">≈${haul(i)}</span>`;
      return `<button data-r="${i}" data-key="${i + 1}" aria-pressed="${n > 0}" ${off ? "disabled" : ""} title="${r} (${i + 1})"><div class="rcard r-${r}">${icon("i-" + r)}<span class="lbl">${r}</span>${n ? `<span class="n">${n}</span>` : ""}</div>${est}</button>`;
    }).join("");
    $("#pick-ok").disabled = picks.length !== (yop ? 2 : 1);
    if (yop) handStrip($("#pick-hand"), RES.map((_, r) => picks.filter((x) => x === r).length), picks.length ? "After" : "Your hand");
    else handStrip($("#pick-hand"), RES.map((_, r) => (picks[0] === r ? haul(r) : 0)), picks.length ? "About" : "Your hand");
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
  const undoKey = $("#btn-undo").dataset.key;
  const cost = (c) => c.flatMap((n, r) => Array.from({ length: n }, () => resIcon(r))).join("");
  openInfo("How to play", `<div class="help">
    <p>First to <b>10 VP</b> on their own turn wins. Settlements are worth 1, cities 2, Longest Road (5+ roads) and Largest Army (3+ knights) 2 each, and hidden victory-point cards 1.</p>
    <p>Each roll pays every settlement next to a hex with that number 1 card (a city 2), unless the robber sits on it. On a 7 everyone holding more than 7 cards discards half, then the robber moves and steals a card. You can play one development card per turn, never on the turn you bought it — knights may be played before rolling.</p>
    <h3>Build costs</h3>
    <div class="help-costs">
      <span>Road</span><span>${cost(COSTS.road)}</span><span>Settlement <span class="muted">1 VP</span></span><span>${cost(COSTS.settle)}</span>
      <span>City <span class="muted">2 VP, pays double</span></span><span>${cost(COSTS.city)}</span><span>Development card</span><span>${cost(COSTS.dev)}</span>
    </div>
    <p>Trade 4 of a kind with the bank for any card — 3 with a 3:1 port, 2 of that resource with a 2:1 port — or offer cards to the other players.</p>
    <p>You only see your own cards, exactly like at a real table. Opponents show card <i>counts</i>; the bots get the same information and track your hand from public events.</p>
    <p>The <b>coach</b> reviews every decision in VP-equivalents: ✓ best &lt; 0.05 ≤ ✓ good &lt; 0.2 ≤ ?! inaccuracy &lt; 0.5 ≤ ? mistake &lt; 1.0 ≤ ?? blunder.</p>
    <details class="help-keys"><summary>Keyboard shortcuts</summary>
    <p class="muted">Every button with a key shows it in a small badge. A key means one thing on each screen.</p>
    <h3>Your turn</h3>
    <div class="help-grid">
      ${k(["R"], "Roll the dice")}${k(["1", "2", "3"], "Build a road / settlement / city: then type the letter on the spot")}
      ${k(["D"], "Buy a development card")}${k(["4", "5", "6", "7"], "Play Knight / Road Building / Monopoly / Year of Plenty")}
      ${k(["T"], "Trade with the bank, ports or players")}${k(["E"], "End your turn")}
      ${k(["A", "…", "Z"], "Pick the board spot with that letter (setup, build modes, robber; never R, E, D, T or H)")}
      ${k(["⌫"], "Take back a typed letter")}${k(["1", "2", "3"], "Choose who to rob (when asked)")}
      ${k(["⇧D"], "Reopen the discard dialog")}${k(["⇧Y"], "Reopen a bot's trade offer")}${k(["Esc"], "Cancel: typed letters, then build mode, then the coach panel")}
    </div>
    <h3>Coach and game</h3>
    <div class="help-grid">
      ${k(["H"], "Hint: the engine's top options")}${k(["⇧1–⇧5"], "Play hint option 1–5")}${k(["⇧V"], "Evaluate every player")}${k(["⇧A"], "All your moves, rated")}
      ${k([undoKey], "Undo your last decision (the dice stay the same)")}${k(["⇧S"], "Dice statistics")}${k(["⇧N"], "New game")}${k(["?"], "This help")}
      ${k(["⇧R"], "Turn move reviews on/off")}${k(["⇧B"], "Show/hide bot reasoning")}${k(["⇧F"], "Bot speed")}${k(["⇧O"], "Auto-roll on/off")}
    </div>
    <h3>In dialogs</h3>
    <div class="help-grid">
      ${k(["1", "…", "5"], "Wood, brick, sheep, wheat, ore: add one (discard, offer you give, Year of Plenty, Monopoly)")}
      ${k(["⇧1", "…", "⇧5"], "Discard: remove one · Offer: add one to what you want")}${k(["⌥1", "…"], "Offer: take one back (⌥⇧ for the want side)")}
      ${k(["A", "…", "W"], "Trade: the bank or port trade with that letter")}${k(["J", "K", "L"], "Trade: accept a bot's answer")}
      ${k(["Y", "N", "C"], "Bot offer: accept, decline, ask the coach")}${k(["S"], "Discard: suggest")}${k(["↵"], "Confirm (discard, play, send offer, start game)")}${k(["⌫"], "Clear the offer")}${k(["Esc"], "Close")}
    </div>
    </details>
  </div>`);
}

function showGameOver() {
  const sm = S.summary;
  if (!sm) return;
  const won = S.winner === S.human;
  $("#over-title").innerHTML = `<div class="over-banner ${won ? "win" : ""}">${won ? "♛ You win!" : `${esc(S.players[S.winner].name)} wins`}</div>`;
  // where every point came from
  const pts = (vp, note) => (vp ? `${vp}${note ? `<small class="muted"> ${note}</small>` : ""}` : `<span class="muted">–</span>`);
  const rows = sm.rows.map((r) => `<tr><td><span style="color:${pc(r.p)};font-weight:700">${esc(S.players[r.p].name)}</span></td>
    <td class="num">${pts(r.settlements, "")}</td><td class="num">${pts(2 * r.cities, r.cities ? `(${r.cities})` : "")}</td>
    <td class="num">${pts(r.longest ? 2 : 0)}</td><td class="num">${pts(r.army ? 2 : 0)}</td><td class="num">${pts(r.vp_cards)}</td>
    <td class="num"><b>${r.vp}</b></td></tr>`).join("");
  let rev = "";
  if (sm.accuracy !== null) {
    rev = `<h3 style="margin-top:18px">Your decisions</h3>
      <div class="vcounts">${Object.entries(sm.counts).map(([k, n]) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${n}</span>`).join("")}</div>
      <p>Accuracy <b>${Math.round(sm.accuracy)}%</b> · total value lost <b>${sm.lost.toFixed(2)}</b> VP-eq · ${sm.rounds} round${sm.rounds === 1 ? "" : "s"} · <button class="btn ghost" id="over-all" data-key="A" style="padding:2px 8px">every move…</button></p>
      ${sm.worst.length ? `<h3>Biggest misses</h3><ul class="misses">${sm.worst.map((w) => `<li><span class="vd vd-${w.verdict}">−${w.delta.toFixed(2)}</span> ${w.turn ? `turn ${w.turn}` : "setup"}: ${esc(plain(w.label))}<br><span class="eng">better: ${esc(human(w.best))}</span></li>`).join("")}</ul>` : ""}`;
  }
  $("#over-body").innerHTML = `<div class="table-x"><table class="info-table vp-table"><thead><tr><th>Player</th><th title="1 VP each">Settlements</th><th title="2 VP each (count)">Cities</th><th title="2 VP">Longest road</th><th title="2 VP">Largest army</th><th title="1 VP each">VP cards</th><th>Total</th></tr></thead><tbody>${rows}</tbody></table></div>${rev}`;
  modal($("#dlg-over"));
}

function openNewGame(cancellable = true) {
  const dlg = $("#dlg-new");
  $("#new-cancel").hidden = !cancellable || !S;
  const last = store("newgame", null);
  const f = $("#form-new");
  for (const [k, v] of Object.entries(last || {})) {
    if (k === "name") f.elements.name.value = v;
    else { const radio = f.querySelector(`[name="${k}"][value="${v}"]`); if (radio) radio.checked = true; }
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
  const board = $("#board");
  board.addEventListener("click", onBoardClick);
  board.addEventListener("pointermove", onBoardHover);
  board.addEventListener("pointerleave", () => $("#tip").classList.remove("show"));

  $("#btn-roll").onclick = () => act(["roll"]);
  $("#btn-end").onclick = () => { mode = null; act(["end"]); };
  const mac = /Mac|iP/.test(navigator.platform);
  $("#btn-undo").dataset.key = mac ? "⌘Z" : "Ctrl Z";
  $("#btn-undo").title = `Undo your last decision (${mac ? "⌘Z" : "Ctrl+Z"})`;
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

  $("#speed").value = prefs.speed;
  $("#speed").onchange = (e) => { prefs.speed = e.target.value; save("speed", prefs.speed); };
  $("#autoroll").checked = prefs.autoroll;
  $("#autoroll").onchange = (e) => { prefs.autoroll = e.target.checked; save("autoroll", prefs.autoroll); maybeAutoRoll(); };

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
  wireLog();
}

function toggleMode(m) {
  if (!myDecision() || S.phase !== "main" || !can(m)) return;
  mode = mode === m ? null : m;
  render();
}

let undoing = false;
async function undo() {
  if (!S?.can_undo || undoing) return;
  undoing = true;
  // undo is always allowed: let the bots finish their current step and any request land first
  stopPump = true;
  while (busy || pumping) await sleep(40);
  stopPump = false;
  // forced dialogs (discard, a bot's offer) and the game-over screen give way to the undo
  for (const d of $$("dialog[open]")) if (d.id !== "dlg-new") d.close();
  busy = true;
  try {
    mode = null;
    holdAutoRoll = true;
    const st = await api("undo", {});
    apply(st);
    reviewsSeen = S.reviews.length;
    overShown = null; // undoing out of a finished game: show the result again if it ends again
    toast(`Undid: ${st.undone ? human(st.undone) : "your last decision"}`, "Same dice, different choices.", "var(--muted)", 2600);
  } catch (e) {
    toast(e.message, "", "var(--worst)");
  }
  busy = false;
  undoing = false;
  render();
  pump();
}

// the badge label a key press produces: "R", "⇧V", "4", "⇧2", "⌥1", "↵", "⌫", "?"
function keyLabel(e) {
  if (e.key === "Enter") return "↵";
  if (e.key === "Backspace") return "⌫";
  if (e.key === "Escape") return "Esc";
  const digit = /^Digit(\d)$/.exec(e.code);
  if (digit) return (e.altKey ? "⌥" : "") + (e.shiftKey ? "⇧" : "") + digit[1];
  if (e.altKey) return null;
  if (/^Key[A-Z]$/.test(e.code)) return (e.shiftKey ? "⇧" : "") + e.code.slice(3);
  return e.key.length === 1 ? e.key : null;
}

const shown = (el) => (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null) && !el.disabled;

// the visible control with this label inside scope (dialogs and popovers only count when they are the scope)
function keyTarget(scope, label) {
  return $$("[data-key]", scope).find((el) => el.dataset.key === label && shown(el) && (scope !== document || !el.closest("dialog, [popover]")));
}

function press(el) {
  const sel = el.querySelector("select");
  if (sel) { // a select cycles through its options
    sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
    sel.dispatchEvent(new Event("change"));
    toast(`Speed: ${sel.value}`, "", "var(--muted)", 1200);
    return;
  }
  el.click();
}

// badges: every [data-key] gets a <kbd> showing its key (kept in sync as the UI re-renders)
function decorateKeys() {
  for (const el of document.querySelectorAll("[data-key]")) {
    let k = el.querySelector(":scope > .kb");
    if (!k) { k = document.createElement("kbd"); k.className = "kb"; k.setAttribute("aria-hidden", "true"); el.append(k); }
    if (k.textContent !== el.dataset.key) k.textContent = el.dataset.key;
  }
}
new MutationObserver(decorateKeys).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-key"] });

function onKey(e) {
  if (e.target.matches("input:not([type=checkbox]):not([type=radio]), select, textarea")) return;
  const dlg = $$("dialog[open]").at(-1);
  const k = e.key.toLowerCase();
  if (e.metaKey || e.ctrlKey) {
    if (k === "z" && (!dlg || dlg.id !== "dlg-new")) { e.preventDefault(); undo(); }
    return;
  }
  const label = keyLabel(e);
  if (!label) return;
  // an open dialog or the victim menu owns the keyboard
  const scope = dlg || $("#victims:popover-open");
  if (scope) {
    if (label === "↵" && e.target.closest("button, a")) return; // Enter on a focused button presses that button
    if (label === "Esc" && dlg?.getAttribute("closedby") !== "none") return; // the browser closes it
    const el = keyTarget(scope, label);
    if (el) { e.preventDefault(); press(el); }
    return;
  }
  if (k === "backspace" && keyBuf) { e.preventDefault(); keyBuf = keyBuf.slice(0, -1); renderKeys(); return; }
  if (/^[A-Z]$/.test(label) && !busy && pickKey(k)) { e.preventDefault(); return; }
  // halfway through a two-letter spot code, any other key just cancels the code
  if (keyBuf && label !== "Esc") { e.preventDefault(); keyBuf = ""; renderKeys(); return; }
  if (label === "Esc") {
    if (keyBuf) { keyBuf = ""; renderKeys(); }
    else if (mode) { mode = null; render(); }
    else if ($("#coach-out").children.length) closeCoach();
    else if ($("#recap").classList.contains("show")) $("#recap").classList.remove("show");
    else return;
    e.preventDefault();
    return;
  }
  if (label === "↵" && e.target.closest("button, a")) return;
  const el = keyTarget(document, label);
  if (el) { e.preventDefault(); press(el); }
}

boot();

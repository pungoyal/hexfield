// The side panels and the tray: players, hand, actions, status, dice, bank, income, log.

import { B, G, S, TOUCH, can, myDecision, roundName, session } from "./state.js";
import { BUILD_NAMES, hexName, plain, production, tradesShort } from "./text.js";
import { $, COSTS, VENTURES, LOG_ICONS, RES, STYLE_NOTES, esc, icon, pc, resIcon, setHTML } from "./util.js";

export function renderPlayers() {
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
    const hid = p === S.human && pl.hidden_fame ? `<span class="hid" title="includes ${pl.hidden_fame} hidden landmark(s)">★${pl.hidden_fame}</span>` : "";
    const badges = [];
    // the two awards ride in the header as icon chips (2 fame each); warnings get a line of their own
    const awards = (S.longest === p ? `<span class="badge award" title="Great Trail: 2 fame">${icon("i-longest")}</span>` : "") +
      (S.patrol === p ? `<span class="badge award" title="Great Patrol: 2 fame">${icon("i-patrol")}</span>` : "");
    // a player within two points of winning is the one to stop (bots may also hold hidden Landmarks)
    const close = S.phase !== "game_over" && pl.fame >= 8;
    el.classList.toggle("threat", close && p !== S.human);
    if (close) badges.push(`<span class="badge near">${10 - pl.fame} fame from winning${pl.bot ? " (or fewer)" : ""}</span>`);
    const owe = S.discards_pending[p] ? ` · discarding ${S.discards_pending[p]}` : "";
    const inc = production(p).total;
    const perRoll = (inc / 36).toFixed(1);
    const race = Array.from({ length: 10 }, (_, k) => `<i class="${k < pl.fame ? "on" : ""}"></i>`).join("");
    const html = `
      <div class="p-head">
        <span class="p-seat" title="Seat ${p + 1}">${p + 1}</span><span class="p-name">${esc(pl.name)}</span>
        <span class="p-tag" ${pl.bot && pl.style ? `title="${esc(pl.level)} bot, ${esc(pl.style)}: ${esc(STYLE_NOTES[pl.style] || "")}"` : ""}>${pl.open ? "open" : pl.bot ? esc(pl.style || pl.level) : p === S.human ? "you" : "player"}</span>${awards}
        <span class="p-fame">${hid}<b>${pl.fame}</b><small>fame</small></span>
      </div>
      <div class="p-race" title="${pl.fame} of 10 fame${pl.bot ? " (hidden landmarks not shown)" : ""}">${race}</div>
      <div class="p-stats">
        <span class="st-cards ${pl.cards > 7 ? "warn" : ""}" title="Resource cards in hand${pl.cards > 7 ? " — over 7, exposed to a 7" : ""}"><b>${pl.cards}</b>&nbsp;card${pl.cards === 1 ? "" : "s"}</span>
        <span title="Ventures held"><b>${pl.ventures}</b>&nbsp;vn</span>
        <span title="Rangers played (3+ and the most wins Great Patrol)"><b>${pl.rangers}</b>&nbsp;rg</span>
        <span title="Great Trail (5+ and the longest wins Great Trail)">trail&nbsp;<b>${pl.trail}</b></span>
        <span title="Income: ${inc} pips — about ${(inc / 36).toFixed(2)} cards per roll${p === S.human ? "" : " (from their buildings)"}"><b>${perRoll}</b>/roll</span>
      </div>
      ${badges.length || owe ? `<div class="p-badges">${badges.join("")}${owe ? `<span class="muted" style="font-size:12px">${owe}</span>` : ""}</div>` : ""}
      ${pl.hand ? `<div class="p-open" title="This player's cards (replay with all cards shown)">${pl.hand.map((n, r) => (n ? `<span>${resIcon(r)}${n}</span>` : "")).join("") || `<span class="muted">no cards</span>`}${pl.venture_cards.some(Boolean) ? `<span class="muted">· ${pl.venture_cards.map((n, k) => (n ? `${n} ${VENTURES[k].name.toLowerCase()}` : "")).filter(Boolean).join(", ")}</span>` : ""}</div>` : ""}
      <div class="p-think"><div class="spinner" style="--pc:${pc(p)}"></div></div>`;
    setHTML(el, html);
  });
}

export function renderHand() {
  const hand = $("#hand");
  if (!hand.children.length) {
    hand.replaceChildren(...RES.map((r) => {
      const d = document.createElement("div");
      d.className = `rcard r-${r}`;
      d.innerHTML = `${icon("a-" + r)}<span class="n"></span><span class="lbl">${r}</span>`;
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
  setHTML($("#hand-cap"), `<span>Your hand</span><b>${size}</b>${size > 7 ? `<span class="cap-warn" title="Over 7 cards: a rolled 7 makes you discard half">a 7 costs you ${Math.floor(size / 2)}</span>` : ""}`);
  const dh = $("#venturehand");
  const cards = [];
  VENTURES.forEach((d, k) => {
    const play = d.play && can(d.play);
    if (S.ventures[k]) cards.push(ventureCard(d, k, S.ventures[k], play, false));
    if (S.new_ventures[k]) cards.push(ventureCard(d, k, S.new_ventures[k], false, true));
  });
  setHTML(dh, cards.join(""));
  const nVenture = S.ventures.reduce((a, b) => a + b, 0) + S.new_ventures.reduce((a, b) => a + b, 0);
  setHTML($("#venture-cap"), `<span>Ventures</span>${nVenture ? `<b>${nVenture}</b>` : ""}`);
}

// why a venture can't be played right now (shown on the card), or "" if it can / it's not your turn
export function ventureBlock(d, playable, isNew) {
  if (!d.play || playable) return "";
  if (isNew) return "next turn";
  if (!myDecision() || !["main", "pre_roll"].includes(S.phase)) return "";
  if (S.venture_played) return "1 per turn";
  if (d.key === "pathfinder" && !(S.legal.trail || []).length) return "no trail spot";
  if (S.phase === "pre_roll") return "after roll";
  return "not now";
}

export function ventureCard(d, k, n, playable, isNew) {
  const why = ventureBlock(d, playable, isNew);
  const tip = isNew ? `${d.name} — bought this turn, playable next turn` : playable ? `Play ${d.name} (${d.hot})` : d.key === "landmark" ? "Landmark: 1 fame (counts automatically)"
    : `${d.name}${why === "1 per turn" ? " — you already played a venture this turn" : why ? ` — ${why}` : ""}`;
  return `<button class="dcard ${d.key === "landmark" ? "landmark" : ""} ${playable ? "playable" : ""} ${isNew ? "new" : ""} ${why ? "blocked" : ""}" data-venture="${k}" ${playable ? `data-key="${d.hot}"` : "tabindex=-1"} title="${esc(tip)}">${icon(d.art)}<span class="lbl">${d.name}</span>${n > 1 ? `<span class="n">${n}</span>` : ""}${why ? `<span class="why-tag">${why}</span>` : ""}</button>`;
}

// [button, legal kind, cost, tooltip]
export const BUILD_BUTTONS = [
  ["#btn-trail", "trail", "trail", "Build a trail (1)"],
  ["#btn-settle", "settle", "settle", "Build a village (2)"],
  ["#btn-town", "town", "town", "Upgrade to a town (3)"],
  ["#btn-venture", "buy_venture", "venture", "Buy a venture (D)"],
];

export function renderActions() {
  const mine = myDecision();
  const pre = mine && S.phase === "pre_roll";
  const main = mine && S.phase === "main";
  const special = mine && S.phase === "special_build";
  const roll = $("#btn-roll");
  roll.disabled = !can("roll") || G.busy;
  roll.classList.toggle("pulse", pre);
  roll.classList.toggle("obvious", can("roll"));
  // "go": what you can press right now grows; "go-main" is the one step that moves the game on
  const go = (el, on, lead = false) => { el.classList.toggle("go", on); el.classList.toggle("go-main", on && lead); };
  go(roll, can("roll"), true);
  go($("#btn-discard"), true, true);
  go($("#btn-answer"), true, true);
  // the tooltip says why a button is off
  const why = (kind, cost, base) => {
    if (!mine) return `${base} — not your turn`;
    if (S.phase !== "main" && S.phase !== "special_build") return `${base} — ${S.phase === "pre_roll" ? "roll first" : "finish the current step first"}`;
    const miss = cost.map((n, r) => Math.max(0, n - S.hand[r]));
    if (miss.some(Boolean)) return `${base} — need ${miss.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(", ")}`;
    if (!can(kind)) return `${base} — ${kind === "buy_venture" ? "deck is empty" : kind === "town" ? "no village to upgrade" : "no legal spot (extend your trails)"}`;
    return base;
  };
  for (const [id, kind, cost, label] of BUILD_BUTTONS) {
    const b = $(id), ok = (main || special) && can(kind);
    b.disabled = !ok || G.busy;
    b.classList.toggle("ready", ok);
    go(b, ok);
    if (b.dataset.mode) b.setAttribute("aria-pressed", String(G.mode === b.dataset.mode));
    b.title = why(kind, COSTS[cost], label);
    // costs: one icon per card, dimmed where your hand comes up short
    let c = b.querySelector(".cost");
    if (!c) { c = document.createElement("span"); c.className = "cost"; b.append(c); }
    setHTML(c, COSTS[cost].flatMap((n, r) => Array.from({ length: n }, (_, k) => resIcon(r, `ri ${k < S.hand[r] ? "" : "miss"}`))).join(""));
  }
  $("#btn-trade").disabled = !main || G.busy;
  go($("#btn-trade"), main);
  $("#btn-roll").title = can("roll") ? "Roll the dice (R)" : mine && S.phase === "main" ? "Already rolled" : "Roll the dice — not now";
  // Roll and End turn share one slot: the next step is always in the same place
  roll.hidden = !pre;
  const end = $("#btn-end");
  end.hidden = pre;
  end.disabled = !can("end") || G.busy;
  const onlyEnd = (main || special) && (S.legal.kinds || []).every((k) => k === "end" || k === "bank");
  end.classList.toggle("pulse", onlyEnd);
  go(end, can("end"), onlyEnd);
  const label = special ? "Pass" : "End turn";
  if (end.firstChild.nodeValue !== label) end.firstChild.nodeValue = label;
  end.title = special ? "Done building: pass to the next player (E)" : "End your turn (E)";
}

// main phase, no build mode: what you can build now, else what's closest
export function mainStatus() {
  const ready = BUILD_BUTTONS.filter(([, kind]) => can(kind));
  if (ready.length) return `Build: ${ready.map(([id, , cost]) => `${BUILD_NAMES[cost]} <kbd>${$(id).dataset.key}</kbd>`).join(" · ")}`;
  const near = BUILD_BUTTONS.map(([, , cost]) => [cost, tradesShort(S.hand, COSTS[cost])]).filter(([, n]) => n > 0 && n < Infinity).sort((a, b) => a[1] - b[1])[0];
  if (near) return `${BUILD_NAMES[near[0]]} is ${near[1]} bank trade${near[1] > 1 ? "s" : ""} away <kbd>T</kbd>`;
  return "Nothing to build — trade <kbd>T</kbd> or end turn <kbd>E</kbd>";
}

export function renderStatus(thinker) {
  let text, p = S.current, mine = false, spin = false;
  const name = (q) => esc(S.players[q].name);
  const L = S.legal || {};
  if (session.replay) {
    text = `Replay${S.phase === "game_over" ? ` — ${name(S.winner)} won` : ""}`;
    p = S.phase === "game_over" ? S.winner : S.actor;
  } else if (S.lobby) {
    text = "Waiting for players to join";
  } else if (S.phase === "game_over") {
    text = S.winner === S.human ? "You win! ♛" : `${name(S.winner)} wins`;
    p = S.winner;
  } else if (S.pending_offer) {
    text = `${name(S.pending_offer.from)} offers you a trade`; p = S.pending_offer.from; mine = true;
  } else if (S.actor === S.human && myDecision()) {
    mine = true; p = S.human;
    switch (S.phase) {
      case "setup_village": text = `Place your ${S.setup_idx < S.n ? "first" : "second"} village — ${TOUCH ? "tap a spot" : "click or type its letters"}`; break;
      case "setup_trail": text = `Place a trail next to it — ${TOUCH ? "tap one" : "click or type its letter"}`; break;
      case "pre_roll": text = (L.kinds || []).length > 1 ? "Roll the dice — or play a venture first" : "Roll the dice"; break;
      case "main": text = G.mode ? `${BUILD_NAMES[G.mode]}: ${TOUCH ? "tap a spot · tap the button again to cancel" : "click a spot or type its letter · Esc to cancel"}` : mainStatus(); break;
      case "discard": text = `Discard ${S.discard_owed} cards`; break;
      case "move_bandit": text = `Move the bandit — ${TOUCH ? "tap a hex" : "click a hex or type its letter"}`; break;
      case "free_trails": text = `Place a free trail (${S.free_trails} left)`; break;
      case "special_build": text = G.mode ? `${BUILD_NAMES[G.mode]}: ${TOUCH ? "tap a spot" : "click a spot or type its letter · Esc to cancel"}`
        : `Special building phase after ${name(S.current)}'s turn — build, or pass <kbd>E</kbd>`; break;
      default: text = S.phase;
    }
  } else {
    p = thinker ?? S.actor;
    spin = thinker !== null && thinker !== undefined;
    const away = !S.players[p].bot; // a person, on another device or about to take this one
    text = S.phase === "discard" ? `${name(p)} is discarding…` : S.phase.startsWith("setup") ? `${name(p)} is placing…`
      : away ? `Waiting for ${name(p)}…` : `${name(p)} is thinking…`;
  }
  // once the game is over the pill reopens the result (it can be dismissed to look at the board)
  const more = S.phase === "game_over" && S.summary ? `<button class="pill-link" id="show-over">Results</button>` : "";
  const html = `<div class="pill ${mine ? "mine" : ""}" style="--pc:${pc(p)}">${spin ? `<span class="spinner"></span>` : `<span class="dot"></span>`}<span>${text}</span><span class="muted" style="font-weight:500">· ${roundName()}</span>${more}</div>`;
  setHTML($("#status"), html);
}


export const PIP_LAYOUT = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
export function renderDice(animate) {
  const box = $("#dice");
  const r = S.last_roll;
  if (!r) { box.replaceChildren(); return; }
  const [d1, d2, total] = r;
  const die = (n, red) => `<div class="die ${red ? "red" : ""}">${Array.from({ length: 9 }, (_, i) => `<i class="${(PIP_LAYOUT[n] || []).includes(i) ? "on" : ""}"></i>`).join("")}</div>`;
  const who = G.lastRoller === null || G.lastRoller === undefined ? "" : G.lastRoller === S.human ? "You rolled" : `${esc(S.players[G.lastRoller].name)} rolled`;
  const html = (who ? `<span class="d-who">${who}</span>` : "") + `<span class="d-row">${d1 ? die(d1, false) + die(d2, true) : ""}<span class="total ${total === 7 ? "seven" : ""}">${total}</span></span>`;
  // before a roll the dice on show are the previous player's
  box.classList.toggle("stale", S.phase === "pre_roll" || S.phase.startsWith("setup"));
  box.title = box.classList.contains("stale") ? `Last roll: ${who.replace(" rolled", "")} — not yours this turn` : "";
  // fresh dice elements replay the tumble animation
  if (animate) { box.dataset.html = ""; box.classList.add("rolling"); }
  setHTML(box, html);
}

export function renderBank() {
  const html = RES.map((r, i) => `<div class="bk ${S.bank[i] === 0 ? "out" : S.bank[i] <= 3 ? "low" : ""}" title="${S.bank[i]} ${r} left in the bank${S.bank[i] === 0 ? ": nobody collects it until some come back" : ""}">${resIcon(i)}${S.bank[i]}</div>`).join("") +
    `<div class="bk bk-deck" title="${S.deck} ventures left">${icon("i-venture", "ri")}${S.deck}</div>`;
  setHTML($("#bank"), html);
}

// Your economy at a glance: what each resource earns, on which numbers, and your trade rate.
export function renderEcon() {
  const pr = production(S.human);
  const top = Math.max(10, ...pr.pips.map((n, r) => n + pr.blocked[r]));
  const rows = RES.map((r, i) => {
    const n = pr.pips[i], bl = pr.blocked[i];
    const nums = pr.nums[i].sort((a, b) => b.pips - a.pips).map((x) => `<span class="ec-num ${x.n === 6 || x.n === 8 ? "hot" : ""}">${x.n}${x.lv > 1 ? "<sup>×2</sup>" : ""}</span>`).join("");
    const ratio = S.ratios[i];
    return `<div class="ec-row ${n ? "" : "none"}" title="${r}: ${n} pips${bl ? ` (+${bl} blocked by the bandit)` : ""} · trade ${ratio}:1">
      ${resIcon(i)}
      <span class="ec-bar"><i style="width:${(100 * n) / top}%;background:var(--${r})"></i>${bl ? `<i class="bl" style="width:${(100 * bl) / top}%"></i>` : ""}</span>
      <span class="ec-n">${n || "—"}</span>
      <span class="ec-nums">${nums || (bl ? `<span class="muted">blocked</span>` : `<span class="muted">no income</span>`)}</span>
      <span class="ec-ratio ${ratio < 4 ? "dock" : ""}">${ratio}:1</span></div>`;
  }).join("");
  setHTML($("#econ"), rows);
  $("#econ-sum").textContent = `≈ ${(pr.total / 36).toFixed(1)} cards / roll`;
}

// ------------------------------------------------------------------ log
// The log follows the newest line unless you scrolled up yourself; scrolling back to
// the bottom re-pins it. Only scrolls you make count, so the box shrinking (e.g. while
// the coach panel grows) never unpins it.
export let logTouched = 0;
export function pinLog() { if (G.logStick) { const log = $("#log"); log.scrollTop = log.scrollHeight; } }
export function wireLog() {
  const log = $("#log");
  const touch = () => { logTouched = Date.now(); };
  for (const t of ["wheel", "touchstart", "touchmove", "pointerdown", "keydown"]) log.addEventListener(t, touch, { passive: true });
  log.addEventListener("scroll", () => {
    if (Date.now() - logTouched > 1500) return; // our own pinning, or a layout change
    G.logStick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  });
  new ResizeObserver(pinLog).observe(log);
}

export function appendLog(evs) {
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
      if (/trail/.test(msg)) li.dataset.e = id; else li.dataset.v = id;
      msg = msg.replace(/ at \d+/, "").replace(/upgrades? \d+ to/, (s) => s.replace(/ \d+/, ""));
    }
    const rm = msg.match(/bandit to ([A-Z]{1,2})$/);
    if (rm) {
      const h = B.hexes.findIndex((x) => x.label === rm[1]);
      if (h >= 0) li.dataset.h = h;
      msg = msg.replace(/[A-Z]+$/, hexName(rm[1]));
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

// What a change looks like: dice, flying cards, the turn recap, toasts.

import { renderDice } from "./panels.js";
import { B, G, S, els } from "./state.js";
import { hexName } from "./text.js";
import { $, RES, delta, esc, flash, icon, parseCards, pc, resIcon } from "./util.js";

// ------------------------------------------------------------------ effects
export function effects(evs) {
  for (const e of evs) {
    if (e.kind === "roll" && S.last_roll) {
      renderDice(true);
      const total = S.last_roll[2];
      if (total !== 7) {
        B.hexes.forEach((h, i) => {
          if (h.num === total && i !== S.bandit) { flash(els.hex[i], "produce", 1300); flash(els.token[i], "produce", 1300); }
        });
        flyProduction(total, new Set(evs.filter((x) => x.kind === "produce").map((x) => x.p)));
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
    if (e.kind === "private" && /you drew/.test(e.msg)) toast("Venture", e.msg.replace(/[()]/g, ""), "#9c88e0");
    if (e.kind === "private_steal") toast("Steal", e.msg.replace(/[()]/g, ""), "#e59cf2");
  }
}

// A roll pays out visibly: one card per card collected leaves the hex and lands on the
// collector (your hand card, or the bot's panel), so you see which hex paid whom.
export const calm = matchMedia("(prefers-reduced-motion: reduce)");
export function flyProduction(total, paid) {
  if (calm.matches || document.hidden) return;
  const m = $("#board").getScreenCTM();
  if (!m) return;
  let k = 0;
  B.hexes.forEach((h, i) => {
    if (h.num !== total || i === S.bandit || !h.res) return;
    const r = RES.indexOf(h.res);
    const from = { x: m.a * h.x + m.e, y: m.d * h.y + m.f };
    for (const v of els.hexVerts[i]) {
      const o = S.v_owner[v];
      if (o === -1 || !paid.has(o)) continue; // the bank ran dry for this one
      const to = o === S.human ? $("#hand").children[r] : $("#players").children[o];
      for (let c = 0; c < S.v_level[v] && k < 24; c++) flyCard(from, to, r, o, 70 * k++);
    }
  });
}

export function flyCard(from, toEl, r, owner, delay) {
  const box = toEl?.getBoundingClientRect();
  if (!box || !box.width) return;
  const el = document.createElement("div");
  el.className = `fly r-${RES[r]}`;
  el.style.cssText = `left:${from.x}px;top:${from.y}px;--pc:${pc(owner)}`;
  el.innerHTML = icon("a-" + RES[r]);
  document.body.append(el);
  // bots: land on the card count, at the left of their panel; you: on the matching hand card
  const tx = (owner === S.human ? box.left + box.width / 2 : box.left + 34) - from.x;
  const ty = box.top + box.height / 2 - from.y;
  const at = (x, y, sc) => `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${sc})`;
  const done = () => el.remove();
  el.animate([
    { transform: at(0, 0, 0.3), opacity: 0 },
    { transform: at(0, -14, 1.15), opacity: 1, offset: 0.18 },
    { transform: at(tx, ty, 0.8), opacity: 1, offset: 0.9 },
    { transform: at(tx, ty, 0.5), opacity: 0 },
  ], { duration: 820, delay, easing: "cubic-bezier(.45, 0, .25, 1)", fill: "both" }).finished.then(done, done);
}

// "While you waited": what changed for you and what the others did since your last turn.
// Your card changes come from comparing hands (so steals, discards, hoards and
// accepted offers all count); the others' actions come from the log.
export let recapBuf = [];
export let recapTurnSeen = null;
export let recapHand = null; // your hand as your last turn ended
export function recapTurn(fresh, initial) {
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
    else if (e.kind === "build") did(e.p, /trail/.test(m) ? "trail" : /town/.test(m) ? "town" : "village");
    else if (e.kind === "venture") did(e.p, /buys/.test(m) ? "venture" : m.replace(/^\S+ plays? /, "played ").replace(/ \(.*\)$/, ""));
    else if (e.kind === "bandit" && e.p !== S.human) did(e.p, `bandit → ${hexName((m.match(/bandit to ([A-Z]{1,2})\b/) || [])[1] || "?")}`);
    else if (e.kind === "trade") did(e.p, /bank/.test(m) ? "bank trade" : "trade");
  }
  const cards = (c, sign) => c.map((n, r) => (n ? `<span class="rc-chip ${sign < 0 ? "neg" : ""}">${resIcon(r)}${sign < 0 ? "−" : "+"}${n}</span>` : "")).join("");
  const count = (list) => {
    const m = new Map();
    for (const x of list) m.set(x, (m.get(x) || 0) + 1);
    return [...m].map(([k, n]) => (n > 1 ? `${n} ${k === "town" ? "towns" : k + "s"}` : k)).join(", ");
  };
  const lines = [...acts].map(([p, l]) => `<div class="rc-line"><span class="dot" style="background:${pc(p)}"></span><b>${esc(S.players[p].name)}</b> ${esc(count(l))}</div>`);
  const mine = cards(got, 1) + cards(lost, -1);
  if (!mine && !lines.length && robbedBy === null) return;
  recap(`${mine ? `<div class="rc-mine">${mine}</div>` : `<div class="rc-mine muted">No cards for you this round</div>`}${robbedBy !== null ? `<div class="rc-line warn">${esc(S.players[robbedBy].name)} robbed you</div>` : ""}${lines.join("")}`);
}

export function recap(html) {
  const box = $("#recap");
  box.innerHTML = `<div class="rc-head">While you waited<button class="t-x" aria-label="Dismiss">✕</button></div>${html}`;
  box.querySelector(".t-x").onclick = () => box.classList.remove("show");
  box.classList.remove("show"); void box.offsetWidth; box.classList.add("show");
  clearTimeout(recap.t);
  recap.t = setTimeout(() => box.classList.remove("show"), 9000);
}

// Hoard, Windfall and steals change your hand outside production: float the
// change off each card (production already animates itself)
export function handGains(evs) {
  if (!G.prev || !evs.some((e) => e.kind === "private_steal" || (e.kind === "venture" && /Hoard|Windfall/.test(e.msg)))) return;
  const produced = [0, 0, 0, 0, 0];
  for (const e of evs) if (e.kind === "produce" && e.p === S.human) parseCards(e.msg).forEach((n, r) => { produced[r] += n; });
  S.hand.forEach((n, r) => {
    const d = n - G.prev.hand[r] - produced[r];
    if (!d) return;
    flash($("#hand").children[r], "bump", 500);
    delta($("#hand").children[r], d, "card-delta");
  });
}

// ------------------------------------------------------------------ toasts
export function modal(dlg) {
  dlg.showModal();
  // without an explicit autofocus the browser focuses the first button (often ✕),
  // and Enter would press that instead of the dialog's ↵ action
  if (!dlg.querySelector("[autofocus]")) { dlg.tabIndex = -1; dlg.focus(); }
  raiseToasts();
}
export function raiseToasts(force = false) {
  const b = $("#toasts");
  // (a dialog with no room around it is not covered: toasts already up step aside until it closes)
  try { if (b.matches(":popover-open")) b.hidePopover(); if (force || !crowded()) b.showPopover(); } catch { /* unsupported */ }
  placeToasts();
}
// on wide screens toasts sit in the board's lower-left corner (open sea), off the panels you read
export function placeToasts() {
  const b = $("#toasts");
  b.style.inset = b.style.width = b.style.translate = "";
  // beside or above an open dialog, never across it
  const dlg = [...document.querySelectorAll("dialog[open]")].at(-1);
  if (dlg) {
    const d = dlg.getBoundingClientRect();
    if (d.left >= 230) { b.style.inset = `auto auto 14px 10px`; b.style.width = `${Math.min(300, d.left - 20)}px`; b.style.translate = "none"; return; }
    // no room beside it: under it, or over it, wherever a toast fits (toast() holds them back otherwise)
    if (innerHeight - d.bottom >= TOAST_ROOM) b.style.inset = `${d.bottom + 6}px auto auto 50%`;
    else b.style.inset = `6px auto auto 50%`;
    b.style.translate = "-50% 0";
    return;
  }
  if (innerWidth < 900) return;
  const r = $(".board-wrap").getBoundingClientRect();
  b.style.inset = `auto auto ${Math.max(8, innerHeight - r.bottom + 8)}px ${r.left + 6}px`;
  b.style.width = `${Math.min(270, r.width * 0.42)}px`; // narrow: the island now reaches into this corner
}
// A dialog that leaves no room around it (most of them on a phone) is not talked over:
// what the game has to say waits until it closes. Problems are shown at once.
const TOAST_ROOM = 64;
const held = [];
function crowded() {
  const dlg = [...document.querySelectorAll("dialog[open]")].at(-1);
  if (!dlg) return false;
  const d = dlg.getBoundingClientRect();
  return d.left < 230 && innerHeight - d.bottom < TOAST_ROOM && d.top < TOAST_ROOM;
}
document.addEventListener("close", () => {
  if (crowded()) return;
  if ($("#toasts").children.length) raiseToasts();
  for (const args of held.splice(0).slice(-3)) toast(...args);
}, true);
export function toast(title, sub = "", color = "var(--accent)", ms = 4200) {
  const box = $("#toasts");
  if (color !== "var(--worst)" && !$("#dlg-over").open && crowded()) { held.push([title, sub, color, ms]); return; }
  // the result screen already says it all: only problems may speak over it
  if ($("#dlg-over").open && color !== "var(--worst)") return;
  if (!box.matches(":popover-open") || $("dialog[open]")) raiseToasts(color === "var(--worst)"); // problems are shown wherever you are
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

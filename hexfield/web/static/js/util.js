// Small helpers with no knowledge of the game.

import { RES } from "./logic.js";
export { RES, parseCards } from "./logic.js";


export const COSTS = { trail: [1, 1, 0, 0, 0], settle: [1, 1, 1, 1, 0], town: [0, 0, 0, 2, 3], venture: [0, 0, 1, 1, 1] };
export const VENTURES = [
  { key: "ranger", name: "Ranger", icon: "i-ranger", art: "a-ranger", play: "play_ranger", hot: "4",
    does: "Move the bandit and steal a card from a player beside it. Three rangers earn the Great Patrol." },
  { key: "landmark", name: "Landmark", icon: "i-fame", art: "a-landmark", play: null,
    does: "1 fame, kept face down. It counts for you automatically." },
  { key: "pathfinder", name: "Pathfinder", icon: "i-pathfinder", art: "a-pathfinder", play: "play_pathfinder", hot: "5",
    does: "Build two trails, free." },
  { key: "hoard", name: "Hoard", icon: "i-hoard", art: "a-hoard", play: "play_hoard", hot: "6",
    does: "Name a resource: every other player hands you all of theirs." },
  { key: "windfall", name: "Windfall", icon: "i-windfall", art: "a-windfall", play: "play_windfall", hot: "7",
    does: "Take any two resource cards from the bank." },
];
export const VERDICT_COLORS = { forced: "var(--muted)", best: "var(--good)", good: "var(--ok)", inaccuracy: "var(--warn)", mistake: "var(--bad)", blunder: "var(--worst)" };
export const LOG_ICONS = { build: "⌂", roll: "⚄", produce: "+", bandit: "☗", steal: "✋", venture: "★", trade: "⇄", award: "♛",
  discard: "−", offer: "?", reply: "·", info: "·", win: "♛", private: "👁", private_steal: "👁" };
export const STYLE_NOTES = {
  balanced: "plays the position", builder: "expands fast and races for the Great Trail",
  citizen: "wants grain and stone, and builds towns early", general: "buys ventures and goes for the Great Patrol",
  trader: "settles on docks and trades often",
};
export const SPEEDS = { fast: 60, normal: 380, slow: 900 };
export const SVGNS = "http://www.w3.org/2000/svg";

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const pc = (p) => `var(--p${p})`;
export const icon = (id, cls = "") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
export const resIcon = (r, cls = "ri") => `<svg class="${cls} r-${RES[r]}" aria-hidden="true"><use href="#i-${RES[r]}"/></svg>`;

// replace an element's markup only when it changed (keeps hover/focus state and avoids churn)
export function setHTML(el, html) {
  if (el.dataset.html === html) return false;
  el.dataset.html = html;
  el.innerHTML = html;
  return true;
}

export function svg(tag, attrs = {}, parent) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

export function store(key, fallback) {
  try { const v = localStorage.getItem("hexfield." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function save(key, v) { try { localStorage.setItem("hexfield." + key, JSON.stringify(v)); } catch { /* private mode */ } }

export function flash(el, cls, ms) {
  if (!el) return;
  el.classList.remove(cls);
  void el.getBoundingClientRect();
  el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), ms);
}

// a "+2" / "−1" that floats up off a card
export function delta(card, n, cls = "") {
  if (!card) return;
  const d = document.createElement("span");
  d.className = `delta ${cls} ${n < 0 ? "neg" : ""}`;
  d.textContent = (n > 0 ? "+" : "") + n;
  card.appendChild(d);
  setTimeout(() => d.remove(), 1400);
}

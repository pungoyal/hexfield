// Keyboard: one handler presses the visible control whose badge matches the key.

import { act, render, undo } from "./app.js";
import { moveRobber, robberHits, targetsNow } from "./board.js";
import { closeCoach } from "./coach.js";
import { toast } from "./effects.js";
import { replayKey } from "./games.js";
import { assignCodes, keyLabel } from "./logic.js";
import { B, G, S, els, myDecision, session } from "./state.js";
import { $, $$, svg } from "./util.js";

// ---- keyboard: every control that has a key carries data-key="<label>" and shows it
// as a badge. One handler presses the visible control with that label in the active
// scope (the open dialog, the victim menu, or the main screen), so a label is
// unique per scope by construction and there is no second table to drift.
// Board spot letters use whatever letters the main screen leaves free.
export const MAIN_LETTERS = new Set($$("body > .app [data-key]")
  .map((el) => el.dataset.key).filter((k) => /^[A-Z]$/.test(k)).map((k) => k.toLowerCase()));
// easiest keys first: home row, then the top row, then the bottom row
export const KEY_ABC = [..."asfgjklqwyuiopzxcvbnm"].filter((c) => !MAIN_LETTERS.has(c)).join("");
export let keyBuf = "";
export let keyMap = []; // [{code, x, y, best} + one of v/kind, e, hex]

// ---- "obvious" controls: in every scope (main screen, each dialog, the victim menu) the
// recommended choice is ringed green, and Space presses it. Multi-step choices (a discard,
// Year of Plenty) move the ring along, so pressing Space repeatedly plays the recommended line.
export function setObvious(scope, el) {
  for (const o of $$(".obvious", scope)) if (o !== el) o.classList.remove("obvious");
  el?.classList.add("obvious");
}

// ---- ranking: the better a spot, the easier its key. The best spot is always A.
export let adjacent = null; // vertex -> neighbouring vertices
export function resetKeyCaches() { adjacent = null; keyBuf = ""; keyMap = []; }
export function neighbours(v) {
  if (!adjacent) {
    adjacent = B.vertices.map(() => []);
    for (const [a, b] of B.edges) { adjacent[a].push(b); adjacent[b].push(a); }
  }
  return adjacent[v];
}
export const openSpot = (v) => S.v_owner[v] === -1 && neighbours(v).every((u) => S.v_owner[u] === -1);

// resources the human already collects (setup's second settlement and later builds favour new ones)
export function myResources() {
  const have = new Set();
  S.v_owner.forEach((o, v) => {
    if (o === S.human) for (const h of B.vertices[v].hexes) if (B.hexes[h].res) have.add(B.hexes[h].res);
  });
  return have;
}

export function spotScore(v, have) {
  const p = B.vertices[v];
  const res = new Set(p.hexes.map((h) => B.hexes[h].res).filter(Boolean));
  let sc = p.pips + 0.6 * res.size + 0.8 * [...res].filter((r) => !have.has(r)).length;
  // a 3:1 port always helps; a 2:1 port only for a resource you will collect
  if (p.port) sc += p.port === "any" || res.has(p.port) || have.has(p.port) ? 1.2 : 0.4;
  return sc;
}

// a road is worth the best open spot it leads to (one more road away counts a bit less)
export function roadScore(e, have) {
  let best = 0;
  for (const u of B.edges[e]) {
    if (openSpot(u)) best = Math.max(best, spotScore(u, have));
    for (const w of neighbours(u)) if (openSpot(w)) best = Math.max(best, 0.8 * spotScore(w, have));
  }
  return best;
}

// the robber: opponents' pips blocked (the leaders' count more), never your own, and a card to steal
export function robberScore(h) {
  const hit = robberHits(h);
  let sc = 0;
  for (const [q, pips] of Object.entries(hit)) {
    sc += Number(q) === S.human ? -3 * pips : pips * (1 + S.players[q].vp / 5);
  }
  return sc + ((S.legal.robber?.[h] || []).length ? 2 : 0);
}

export function keyTargets() {
  if (!myDecision()) return [];
  const t = targetsNow();
  const labelled = G.mode || ["setup_settlement", "setup_road", "road_building", "move_robber"].includes(S.phase);
  if (!labelled) return [];
  const have = myResources();
  const items = [
    ...t.h.map((h) => ({ hex: h, x: B.hexes[h].x, y: B.hexes[h].y - 26, score: robberScore(h) })),
    ...t.v.map(([v, kind]) => ({ v, kind, x: B.vertices[v].x, y: B.vertices[v].y,
      score: kind === "city" ? B.vertices[v].pips : spotScore(v, have) })),
    ...t.e.map((e) => {
      const [a, b] = B.edges[e].map((v) => B.vertices[v]);
      return { e, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, score: roadScore(e, have) };
    }),
  ];
  // best first; equal spots in reading order, so the codes are easy to find
  items.sort((a, b) => b.score - a.score || Math.round(a.y / 20) - Math.round(b.y / 20) || a.x - b.x);
  assignCodes(items, KEY_ABC);
  if (items.length > 1) items[0].best = true;
  return items;
}

export function renderKeys() {
  const g = els.keys;
  g.replaceChildren();
  keyMap = keyTargets();
  // the top-ranked spot also stands out on the board itself
  const top = keyMap[0]?.best ? keyMap[0] : null;
  for (const el of els.targets.children) {
    el.classList.toggle("best", !!top && (top.v !== undefined ? el.dataset.v === String(top.v)
      : top.e !== undefined ? el.dataset.e === String(top.e) : el.dataset.h === String(top.hex)));
  }
  if (keyBuf && !keyMap.some((k) => k.code.startsWith(keyBuf))) keyBuf = "";
  for (const k of keyMap) {
    const on = keyBuf && k.code.startsWith(keyBuf);
    const kg = svg("g", { class: `key-tag ${on ? "on" : keyBuf ? "dim" : ""} ${k.best ? "best" : ""}`, transform: `translate(${k.x.toFixed(1)} ${(k.v !== undefined ? k.y - 20 : k.y).toFixed(1)})` }, g);
    const w = 8 + 7 * k.code.length;
    svg("rect", { x: -w / 2, y: -8, width: w, height: 15, rx: 4 }, kg);
    const tx = svg("text", { y: 3.5 }, kg);
    tx.textContent = k.code.toUpperCase();
  }
}

export function pickKey(ch) {
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
      const m = $("#board").getScreenCTM();
      moveRobber(exact.hex, { clientX: m.a * exact.x + m.e, clientY: m.d * exact.y + m.f });
    } else if (exact.v !== undefined) act([exact.kind, exact.v]);
    else act(["road", exact.e]);
    return true;
  }
  keyBuf = next;
  renderKeys();
  return true;
}

export const shown = (el) => (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null) && !el.disabled;

// the visible control with this label inside scope (dialogs and popovers only count when they are the scope)
export function keyTarget(scope, label) {
  return $$("[data-key]", scope).find((el) => el.dataset.key === label && shown(el) && (scope !== document || !el.closest("dialog, [popover]")));
}

export function press(el) {
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
export function decorateKeys() {
  for (const el of document.querySelectorAll("[data-key]")) {
    let k = el.querySelector(":scope > .kb");
    if (!k) { k = document.createElement("kbd"); k.className = "kb"; k.setAttribute("aria-hidden", "true"); el.append(k); }
    if (k.textContent !== el.dataset.key) k.textContent = el.dataset.key;
  }
}
new MutationObserver(decorateKeys).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-key"] });

export function onKey(e) {
  if (e.target.matches("input:not([type=checkbox]):not([type=radio]), select, textarea")) return;
  const dlg = $$("dialog[open]").at(-1);
  const k = e.key.toLowerCase();
  if (!dlg && !e.metaKey && !e.ctrlKey && !e.target.matches("button, a, [role=button]") && replayKey(e)) { e.preventDefault(); return; }
  if (!dlg && session.replay && e.key === "Escape") { e.preventDefault(); replayKey(e); return; }
  if (e.metaKey || e.ctrlKey) {
    // U is the undo key on every screen; Ctrl/⌘+Z still works out of habit
    if (k === "z" && (!dlg || dlg.id !== "dlg-new") && !session.replay) { e.preventDefault(); undo(); }
    return;
  }
  const label = keyLabel(e);
  if (!label) return;
  // an open dialog or the victim menu owns the keyboard
  const scope = dlg || $("#victims:popover-open");
  if (scope) {
    if (label === "↵" && e.target.closest("button, a")) return; // Enter on a focused button presses that button
    if (label === "Esc" && dlg?.getAttribute("closedby") !== "none") return; // the browser closes it
    if (label === " ") { // Space: the recommended choice, wherever focus is
      const o = $$(".obvious", scope).find(shown);
      if (o) { e.preventDefault(); press(o); }
      return;
    }
    const el = keyTarget(scope, label);
    if (el) { e.preventDefault(); press(el); }
    return;
  }
  if (k === "backspace" && keyBuf) { e.preventDefault(); keyBuf = keyBuf.slice(0, -1); renderKeys(); return; }
  if (/^[A-Z]$/.test(label) && !G.busy && pickKey(k)) { e.preventDefault(); return; }
  // Space is always the obvious move: the top-ranked spot (always a single letter), or the roll
  if (label === " " && !G.busy && !keyBuf) {
    if (keyMap.length) { e.preventDefault(); pickKey(keyMap[0].code); return; }
    const o = $$("body > .app .obvious").find((el) => shown(el) && !el.closest("dialog, [popover]"));
    if (o) { e.preventDefault(); press(o); return; }
  }
  // halfway through a two-letter spot code, any other key just cancels the code
  if (keyBuf && label !== "Esc") { e.preventDefault(); keyBuf = ""; renderKeys(); return; }
  if (label === "Esc") {
    if (keyBuf) { keyBuf = ""; renderKeys(); }
    else if (G.mode) { G.mode = null; render(); }
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

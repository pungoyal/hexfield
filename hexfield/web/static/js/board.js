// The SVG board: tiles, pieces, click targets, highlights and hover tips.

import { act, render } from "./app.js";
import { BANDIT, boardDefs, cityArt, hexPoints, portBadge, roadArt, settlementArt, terrain, tokenArt } from "./art.js";
import { resetKeyCaches, setObvious } from "./keys.js";
import { boardBox, zoomBox } from "./logic.js";
import { B, G, S, els, legal, myDecision } from "./state.js";
import { edgeLine, resetBoardCaches as resetTextCaches, roadsToward, spotName } from "./text.js";
import { $, RES, esc, pc, resIcon, svg } from "./util.js";

// ------------------------------------------------------------------ board
export function buildBoard() {
  const board = $("#board");
  board.replaceChildren();
  const R = B.radius;
  // just the island and its ports: the sea is the panel behind (and fills whatever is left over)
  const box = boardBox(B.vertices);
  els.box = box;
  els.view = { x: box.x, y: box.y, w: box.w, h: box.h };
  els.targetSig = null;
  cancelAnimationFrame(els.viewRaf);
  board.setAttribute("viewBox", `${box.x} ${box.y} ${box.w} ${box.h}`);
  resetTextCaches();
  resetKeyCaches();
  svg("defs", {}, board).innerHTML = boardDefs();
  // waves over the whole stage, well past the viewBox, so the sea has no edge at any window shape
  svg("rect", { x: -3000, y: -3000, width: 6000, height: 6000, fill: "url(#waves)", "pointer-events": "none" }, board);

  const hexPts = (cx, cy, k = 1) => hexPoints(cx, cy, R * k);

  // the coast: oversized hexes merge into one outline, from open water in to dry sand
  const gCoast = svg("g", { class: "coast" }, board);
  for (const [cls, k] of [["shelf", 1.5], ["shallow", 1.3], ["foam", 1.17], ["beach", 1.115], ["sand", 1.04]]) {
    const g = svg("g", { class: cls }, gCoast);
    for (const h of B.hexes) svg("polygon", { points: hexPts(h.x, h.y, k) }, g);
  }

  // ports (under the tiles so the jetties tuck in)
  const gPorts = svg("g", { class: "ports" }, board);
  for (const port of B.ports) {
    const [a, b] = port.v.map((v) => B.vertices[v]);
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const len = Math.hypot(mx - box.cx, my - box.cy);
    const bx = mx + ((mx - box.cx) / len) * 36, by = my + ((my - box.cy) / len) * 36;
    for (const v of [a, b]) {
      const at = { x1: f1(v.x + (bx - v.x) * 0.12), y1: f1(v.y + (by - v.y) * 0.12), x2: f1(bx), y2: f1(by) };
      svg("line", { class: "jetty-shadow", ...at, transform: "translate(1.5 3)" }, gPorts);
      svg("line", { class: "jetty", ...at }, gPorts);
      svg("line", { class: "jetty-planks", ...at }, gPorts);
    }
    const g = svg("g", { class: `port-badge${port.kind === "any" ? " any" : ""}`, transform: `translate(${bx.toFixed(1)} ${by.toFixed(1)})` }, gPorts);
    g.innerHTML = portBadge(port.kind);
    const title = svg("title", {}, g);
    title.textContent = port.kind === "any" ? "3:1 port — any resource" : `2:1 ${port.kind} port`;
  }

  // the shadow the island's tiles throw on the sand
  const gShade = svg("g", { class: "tile-shade" }, board);
  for (const h of B.hexes) svg("polygon", { points: hexPts(h.x, h.y + 4.5, 1) }, gShade);

  // tiles: each one a small painted landscape
  const gTiles = svg("g", { class: "tiles" }, board);
  els.hex = B.hexes.map((h, i) => {
    const g = svg("g", { class: `hex-tile t-${h.res || "badlands"}`, "data-h": i, transform: `translate(${h.x} ${h.y}) scale(${R / 60})` }, gTiles);
    g.innerHTML = terrain(h, i);
    return g;
  });
  // number tokens sit above every landscape, so a tall tree never hides a neighbour's number
  const gTokens = svg("g", { class: "tokens" }, board);
  els.token = B.hexes.map((h) => {
    if (!h.num) return null;
    const tk = svg("g", { class: "token" + (h.num === 6 || h.num === 8 ? " hot" : ""), transform: `translate(${h.x} ${h.y + 6})` }, gTokens);
    tk.innerHTML = tokenArt(h.num, h.pips);
    return tk;
  });

  els.roads = svg("g", { class: "roads" }, board);
  els.pieces = svg("g", { class: "pieces" }, board);
  els.bandit = svg("g", { class: "bandit" }, board);
  els.bandit.innerHTML = BANDIT;
  els.targets = svg("g", { class: "targets" }, board);
  els.keys = svg("g", { class: "keys" }, board);
  els.glow = svg("g", { class: "glow" }, board);
  els.fx = svg("g", { class: "fx" }, board);
  els.roadEls = {};
  els.pieceEls = {};
  els.hexPts = hexPts;
  els.hexVerts = B.hexes.map((_, h) => B.vertices.flatMap((v, i) => ((v.hexes || []).includes(h) ? [i] : [])));
}

// who a bandit on hex h would block: {player: pips lost}
export function banditHits(h) {
  const hx = B.hexes[h], hit = {};
  if (!hx.res) return hit;
  for (const v of els.hexVerts[h]) {
    const o = S.v_owner[v];
    if (o !== -1) hit[o] = (hit[o] || 0) + hx.pips * S.v_level[v];
  }
  return hit;
}

export function ripple(p, owner) {
  const c = svg("circle", { class: "ripple", cx: p.x, cy: p.y, r: 10, style: `--pc:${pc(owner)}` }, els.fx);
  setTimeout(() => c.remove(), 1300);
}

export function roadEl(e, owner) {
  const g = svg("g", { class: "road", style: `--pc:${pc(owner)}` });
  g.innerHTML = roadArt(edgeLine(e, 0.17));
  return g;
}

export function pieceEl(v, owner, level) {
  const { x, y } = B.vertices[v];
  const g = svg("g", { class: `piece ${level === 1 ? "settlement" : "city"}`, style: `--pc:${pc(owner)}`, transform: `translate(${x} ${y}) scale(1.12)` });
  const inner = svg("g", { class: "pop" }, g);
  inner.innerHTML = level === 1 ? settlementArt() : cityArt();
  svg("text", { class: "seat-mark", x: level === 1 ? -3.6 : -8.9, y: level === 1 ? 2 : 3 }, inner).textContent = owner + 1;
  return g;
}

export function renderPieces() {
  for (let e = 0; e < S.e_owner.length; e++) {
    const o = S.e_owner[e];
    const cur = els.roadEls[e];
    if (o === -1) { if (cur) { cur.remove(); delete els.roadEls[e]; } continue; }
    if (cur && cur.dataset.o === String(o)) continue;
    if (cur) cur.remove();
    const g = roadEl(e, o);
    g.dataset.o = o;
    if (G.prev) g.classList.add("pop");
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
    if (!G.prev) g.firstChild.classList.remove("pop");
    else if (o !== S.human) ripple(B.vertices[v], o);
    els.pieces.appendChild(g);
    els.pieceEls[v] = g;
  }
  const h = B.hexes[S.bandit];
  els.bandit.style.transform = `translate(${h.x + 30}px, ${h.y + 8}px)`;
  els.hex.forEach((g, i) => g.classList.toggle("blocked", i === S.bandit));
  els.token.forEach((t, i) => t?.classList.toggle("blocked", i === S.bandit));
}

const f1 = (n) => Math.round(n * 10) / 10;

// Everything you can build right now is clickable on the board; the build buttons
// only narrow the highlights down to one kind.
export function targetsNow() {
  const L = legal();
  const t = { v: [], e: [], h: [], quick: false };
  if (!myDecision()) return t;
  const verts = (kind) => (L[kind] || []).map((v) => [v, kind]);
  switch (S.phase) {
    case "setup_settlement": t.v = verts("settle"); break;
    case "setup_road": case "road_building": t.e = L.road || []; break;
    case "move_bandit": t.h = Object.keys(L.bandit || {}).map(Number); break;
    case "main": case "special_build":
      t.quick = !G.mode;
      if (!G.mode || G.mode === "road") t.e = L.road || [];
      if (!G.mode || G.mode === "settle") t.v.push(...verts("settle"));
      if (!G.mode || G.mode === "city") t.v.push(...verts("city"));
      break;
  }
  return t;
}

// board targets are real buttons to a keyboard or a screen reader
const button = (label) => ({ role: "button", tabindex: 0, "aria-label": label });

export function onBoardKey(ev) {
  if (ev.key !== "Enter") return;
  const t = ev.target.closest("[role=button]");
  if (!t) return;
  ev.preventDefault();
  ev.stopPropagation();
  const r = t.getBoundingClientRect();
  onBoardClick({ target: t, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 });
}

// ------------------------------------------------------------------ the camera
// When the spots you can click sit close together (the road beside a new settlement, a
// free road, a city to upgrade), the board zooms in on them: bigger targets, and no
// doubt about where the game wants you. It zooms back out as soon as you have chosen.
const calm = matchMedia("(prefers-reduced-motion: reduce)");
export const MAX_ZOOM = 1.7;

export function focusBox(t) {
  const full = els.box;
  const pts = [...t.v.map(([v]) => B.vertices[v]), ...t.e.flatMap((e) => B.edges[e].map((v) => B.vertices[v]))];
  if (!pts.length || t.h.length || t.quick) return full;
  return zoomBox(full, pts, B.radius * 1.8, MAX_ZOOM);
}

export function setView(to) {
  const board = $("#board"), from = { ...els.view };
  const put = (v) => { els.view = v; board.setAttribute("viewBox", `${f1(v.x)} ${f1(v.y)} ${f1(v.w)} ${f1(v.h)}`); };
  cancelAnimationFrame(els.viewRaf);
  if (["x", "y", "w", "h"].every((k) => Math.abs(from[k] - to[k]) < 0.5)) return;
  board.classList.toggle("zoomed", to.w < els.box.w - 1);
  if (calm.matches || document.hidden) return put({ x: to.x, y: to.y, w: to.w, h: to.h });
  const t0 = performance.now(), ms = 520;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ms), e = k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2;
    put({ x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, w: from.w + (to.w - from.w) * e, h: from.h + (to.h - from.h) * e });
    if (k < 1) els.viewRaf = requestAnimationFrame(step);
  };
  els.viewRaf = requestAnimationFrame(step);
}

export function renderTargets() {
  const g = els.targets;
  const t = targetsNow();
  // the same choices as last time: leave them alone, so hover, focus and their entrance survive a re-render
  const sig = JSON.stringify([t, S.human, S.turn, S.phase, S.bandit]);
  if (sig === els.targetSig) return;
  els.targetSig = sig;
  g.replaceChildren();
  setView(focusBox(t));
  $("#board").classList.toggle("robbing", t.h.length > 0);
  g.classList.toggle("quick", t.quick);
  let order = 0;
  const enter = (el) => el.style.setProperty("--i", Math.min(order++, 40));
  for (const h of t.h) {
    const hx = B.hexes[h], hit = banditHits(h);
    const who = Object.keys(hit).map(Number).sort((a, b) => (a === S.human) - (b === S.human) || hit[b] - hit[a]);
    const cls = hit[S.human] ? "hurts" : who.length ? "" : "empty";
    const label = `Move the bandit to ${hx.res ? `${hx.num} ${hx.res}` : "the badlands"}: blocks ${who.length ? who.map((p) => `${p === S.human ? "you" : S.players[p].name} (${hit[p]} pips)`).join(", ") : "nobody"}`;
    enter(svg("polygon", { class: `hex-target ${cls}`, "data-h": h, points: els.hexPts(hx.x, hx.y, 0.9), ...button(label) }, g));
    // one chip per player it would block, with the pips they'd lose
    who.forEach((p, i) => {
      const cg = svg("g", { class: `rb-chip ${p === S.human ? "me" : ""}`, transform: `translate(${(hx.x + (i - (who.length - 1) / 2) * 22).toFixed(1)} ${hx.y + 40})` }, g);
      svg("circle", { r: 9, style: `fill:${pc(p)}` }, cg);
      const tx = svg("text", { y: 3.5 }, cg); tx.textContent = hit[p];
    });
  }
  for (const e of t.e) {
    const at = edgeLine(e, 0.2);
    const tg = svg("g", { class: "e-target", "data-e": e, ...button(`Build a road toward ${roadsToward([e])}`) }, g);
    enter(tg);
    svg("line", { class: "bed", ...at }, tg);
    svg("line", { class: "vis", ...at }, tg);
    svg("line", { class: "hit", ...at }, tg);
  }
  for (const [v, kind] of t.v) {
    const p = B.vertices[v];
    const tg = svg("g", { class: `v-target k-${kind}`, "data-v": v, "data-kind": kind,
      ...button(`${kind === "city" ? "Upgrade to a city" : "Build a settlement"}: ${spotName(v)}, ${p.pips} pips`) }, g);
    enter(tg);
    svg("circle", { class: "hit", cx: p.x, cy: p.y, r: 18 }, tg);
    if (kind === "settle") {
      // label each spot with its pip total; hotter spots read brighter
      tg.style.setProperty("--heat", Math.min(1, p.pips / 13).toFixed(2));
      tg.classList.add(p.pips >= 10 ? "t-hi" : p.pips <= 5 ? "t-lo" : "t-mid");
      svg("circle", { class: "halo", cx: p.x, cy: p.y, r: 13 }, tg);
      svg("circle", { class: "ring scored", cx: p.x, cy: p.y, r: 13 }, tg);
      const tx = svg("text", { class: "v-pips", x: p.x, y: p.y + 4.2 }, tg);
      tx.textContent = p.pips;
    } else {
      svg("circle", { class: "halo", cx: p.x, cy: p.y, r: 19 }, tg);
      svg("circle", { class: "ring", cx: p.x, cy: p.y, r: 19 }, tg);
      svg("path", { class: "up", d: `M${f1(p.x)} ${f1(p.y - 31)}l5.5 6.5h-3.4v5h-4.2v-5h-3.4z` }, tg);
    }
  }
}

export function glowFor(a, keep = false) {
  if (!keep) clearGlow();
  if (!a || a[0] === "macro") return;
  const g = els.glow;
  if ((a[0] === "settle" || a[0] === "city") && B.vertices[a[1]]) {
    const p = B.vertices[a[1]];
    svg("circle", { class: "glow-mark", cx: p.x, cy: p.y, r: 15 }, g);
  } else if (a[0] === "road" && B.edges[a[1]]) {
    const [p, q] = B.edges[a[1]].map((v) => B.vertices[v]);
    svg("line", { class: "glow-mark", x1: p.x, y1: p.y, x2: q.x, y2: q.y, "stroke-width": 9, "stroke-linecap": "round" }, g);
  } else if (a[0] === "bandit") {
    const h = B.hexes[a[1]];
    svg("polygon", { class: "glow-mark", points: els.hexPts(h.x, h.y, 0.88) }, g);
  }
}
export function clearGlow() { els.glow?.replaceChildren(); }

// ------------------------------------------------------------------ board input
export function onBoardClick(ev) {
  if (G.busy || !myDecision()) return;
  const v = ev.target.closest("[data-v]");
  const e = ev.target.closest(".e-target");
  const h = ev.target.closest(".hex-target");
  if (v) act([v.dataset.kind, Number(v.dataset.v)]); // kind: settle | city
  else if (e) act(["road", Number(e.dataset.e)]);
  else if (h) moveBandit(Number(h.dataset.h), ev);
  else if (G.mode) {
    G.mode = null;
    render();
  }
}

// with two or more players to rob, ask which one (menu at the click point)
export function moveBandit(hex, at) {
  const vics = S.legal.bandit[hex] || [];
  if (vics.length <= 1) act(["bandit", hex, vics[0] ?? -1]);
  else chooseVictim(hex, vics, at);
}

export function chooseVictim(hex, vics, ev) {
  const pop = $("#victims");
  // the leader with the most cards first: key 1 and Space
  const worth = (q) => 2 * S.players[q].vp + S.players[q].cards;
  vics = [...vics].sort((a, b) => worth(b) - worth(a));
  pop.innerHTML = `<h3>Steal from…</h3>` + vics.map((q, i) => `<button class="btn ghost" data-q="${q}" data-key="${i + 1}" style="--pc:${pc(q)}"><span class="dot"></span>${esc(S.players[q].name)} <span class="muted">${S.players[q].cards} cards · ${S.players[q].vp} VP</span></button>`).join("");
  pop.style.left = `${Math.min(ev.clientX + 8, innerWidth - 240)}px`;
  pop.style.top = `${Math.min(ev.clientY + 8, innerHeight - 160)}px`;
  pop.onclick = (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    pop.hidePopover();
    act(["bandit", hex, Number(b.dataset.q)]);
  };
  setObvious(pop, pop.querySelector("button"));
  pop.showPopover();
  pop.querySelector("button")?.focus();
}

export function onBoardHover(ev) {
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
    html = `<b style="color:var(--accent)">Build a road</b><br><span class="muted">timber + clay</span>`;
  } else if (hx) {
    const i = Number(hx.dataset.h), h = B.hexes[i];
    const robbing = !!ev.target.closest(".hex-target");
    html = h.res ? `${robbing ? `<b style="color:var(--accent)">Move the bandit here</b><br>` : ""}<b>${h.num} ${h.res}</b> <span class="muted">(${h.pips} pips)</span>` : "<b>Badlands</b>";
    const hit = banditHits(i);
    const who = Object.keys(hit).map(Number);
    if (who.length) html += who.map((p) => `<span class="tt-line"><span class="dot" style="background:${pc(p)}"></span>${esc(S.players[p].name)} <span class="muted">${robbing ? "loses" : "earns"} ${hit[p]} pips</span></span>`).join("");
    else if (h.res) html += `<span class="tt-line muted">no buildings</span>`;
    if (robbing && hit[S.human]) html += `<span class="tt-line" style="color:var(--bad)">blocks your own income</span>`;
    const vics = robbing ? S.legal.bandit?.[i] || [] : [];
    if (vics.some((q) => q !== -1)) html += `<span class="tt-line muted">steal from ${vics.filter((q) => q !== -1).map((q) => `${esc(S.players[q].name)} (${S.players[q].cards})`).join(" or ")}</span>`;
    if (i === S?.bandit) html += `<span class="tt-line muted">bandit is here</span>`;
  }
  if (!html) { tip.classList.remove("show"); return; }
  tip.innerHTML = html;
  tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - 290)}px`;
  tip.style.top = `${ev.clientY + 26}px`;
  tip.classList.add("show");
}

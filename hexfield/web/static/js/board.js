// The SVG board: tiles, pieces, click targets, highlights and hover tips.

import { act, render } from "./app.js";
import { resetKeyCaches, setObvious } from "./keys.js";
import { boardBox } from "./logic.js";
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
  board.setAttribute("viewBox", `${box.x} ${box.y} ${box.w} ${box.h}`);
  resetTextCaches();
  resetKeyCaches();
  const defs = svg("defs", {}, board);
  defs.innerHTML = `
    <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="1.6" stdDeviation="1.4" flood-color="#000" flood-opacity=".5"/></filter>
    <filter id="tileshadow" x="-10%" y="-10%" width="120%" height="120%"><feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#000" flood-opacity=".45"/></filter>
    <pattern id="waves" width="60" height="26" patternUnits="userSpaceOnUse"><path d="M0 13 q15 -9 30 0 t30 0" fill="none" stroke="#fff" stroke-opacity=".06" stroke-width="2"/></pattern>
    ${terrainGradient("wood", "#3fa36a", "#2a7a4b", "#1c5434")}
    ${terrainGradient("brick", "#d9774f", "#b9563a", "#843a26")}
    ${terrainGradient("sheep", "#a8d86d", "#86bd4f", "#5c8c32")}
    ${terrainGradient("wheat", "#f6d164", "#e2b22f", "#b3861a")}
    ${terrainGradient("ore", "#a8b2c3", "#8591a6", "#5d677a")}
    ${terrainGradient("desert", "#eadbb3", "#d6c091", "#ad9566")}
  `;
  // waves over the whole stage, well past the viewBox, so the sea has no edge at any window shape
  svg("rect", { x: -3000, y: -3000, width: 6000, height: 6000, fill: "url(#waves)", "pointer-events": "none" }, board);

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
    const len = Math.hypot(mx - box.cx, my - box.cy);
    const bx = mx + ((mx - box.cx) / len) * 34, by = my + ((my - box.cy) / len) * 34;
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
export function robberHits(h) {
  const hx = B.hexes[h], hit = {};
  if (!hx.res) return hit;
  for (const v of els.hexVerts[h]) {
    const o = S.v_owner[v];
    if (o !== -1) hit[o] = (hit[o] || 0) + hx.pips * S.v_level[v];
  }
  return hit;
}

export function terrainGradient(id, hi, mid, lo) {
  return `<radialGradient id="g-${id}" cx="38%" cy="30%" r="80%"><stop offset="0" stop-color="${hi}"/><stop offset=".55" stop-color="${mid}"/><stop offset="1" stop-color="${lo}"/></radialGradient>`;
}
export function shade(res) {
  return { wood: "#174a2c", brick: "#7a3220", sheep: "#f4fbe9", wheat: "#9a6f10", ore: "#3e4757", desert: "#a08350" }[res];
}

export function ripple(p, owner) {
  const c = svg("circle", { class: "ripple", cx: p.x, cy: p.y, r: 10, style: `--pc:${pc(owner)}` }, els.fx);
  setTimeout(() => c.remove(), 1300);
}

export function roadEl(e, owner) {
  const { x1, y1, x2, y2 } = edgeLine(e, 0.16);
  const g = svg("g", { class: "road", style: `--pc:${pc(owner)}` });
  svg("line", { class: "under", x1, y1, x2, y2 }, g);
  svg("line", { class: "top", x1, y1, x2, y2 }, g);
  svg("line", { class: "shine", x1, y1: y1 - 1.5, x2, y2: y2 - 1.5 }, g);
  return g;
}

export function pieceEl(v, owner, level) {
  const { x, y } = B.vertices[v];
  const g = svg("g", { class: "piece", style: `--pc:${pc(owner)}`, transform: `translate(${x} ${y}) scale(1.15)` });
  const inner = svg("g", { class: "pop" }, g);
  if (level === 1) {
    svg("path", { d: "M-10 9V-2L0-11l10 9V9z" }, inner);
    svg("path", { class: "roof", d: "M-10-2 0-11l10 9-2 1.6L0-8l-8 7.6z" }, inner);
    svg("text", { class: "seat-mark", y: 6.5 }, inner).textContent = owner + 1;
  } else {
    svg("path", { d: "M-16 11V-1h8v-6l7.5-7 7.5 7V-1h9v12z" }, inner);
    svg("path", { class: "roof", d: "M-8-7l7.5-7 7.5 7-1.8 1.4L-.5-11-6.2-5.6z" }, inner);
    svg("text", { class: "seat-mark", y: 8 }, inner).textContent = owner + 1;
  }
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
  const h = B.hexes[S.robber];
  els.robber.style.transform = `translate(${h.x + 30}px, ${h.y + 8}px)`;
  els.hex.forEach((g, i) => g.classList.toggle("blocked", i === S.robber));
}

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
    case "move_robber": t.h = Object.keys(L.robber || {}).map(Number); break;
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

export function renderTargets() {
  const g = els.targets;
  g.replaceChildren();
  const t = targetsNow();
  $("#board").classList.toggle("robbing", t.h.length > 0);
  g.classList.toggle("quick", t.quick);
  for (const h of t.h) {
    const hx = B.hexes[h], hit = robberHits(h);
    const who = Object.keys(hit).map(Number).sort((a, b) => (a === S.human) - (b === S.human) || hit[b] - hit[a]);
    const cls = hit[S.human] ? "hurts" : who.length ? "" : "empty";
    const label = `Move the robber to ${hx.res ? `${hx.num} ${hx.res}` : "the desert"}: blocks ${who.length ? who.map((p) => `${p === S.human ? "you" : S.players[p].name} (${hit[p]} pips)`).join(", ") : "nobody"}`;
    svg("polygon", { class: `hex-target ${cls}`, "data-h": h, points: els.hexPts(hx.x, hx.y, 0.9), ...button(label) }, g);
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
    svg("line", { class: "vis", ...at }, tg);
    svg("line", { class: "hit", ...at }, tg);
  }
  for (const [v, kind] of t.v) {
    const p = B.vertices[v];
    const tg = svg("g", { class: `v-target k-${kind}`, "data-v": v, "data-kind": kind,
      ...button(`${kind === "city" ? "Upgrade to a city" : "Build a settlement"}: ${spotName(v)}, ${p.pips} pips`) }, g);
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
  } else if (a[0] === "robber") {
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
  else if (h) moveRobber(Number(h.dataset.h), ev);
  else if (G.mode) {
    G.mode = null;
    render();
  }
}

// with two or more players to rob, ask which one (menu at the click point)
export function moveRobber(hex, at) {
  const vics = S.legal.robber[hex] || [];
  if (vics.length <= 1) act(["robber", hex, vics[0] ?? -1]);
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
    act(["robber", hex, Number(b.dataset.q)]);
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

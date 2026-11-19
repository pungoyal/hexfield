// Words and numbers: turning engine notation and positions into things a person reads.

import { covers, handPlus, tradesNeeded } from "./logic.js";
import { B, S } from "./state.js";
import { COSTS, RES } from "./util.js";
export { accuracy, covers, handPlus, scored, totalLost } from "./logic.js";

// Expected production of player p: pips per resource (cards per 36 rolls),
// the dice numbers that pay out, and what the robber is currently blocking.
export function production(p) {
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
export const BUILD_NAMES = { road: "Road", settle: "Settlement", city: "City", dev: "Dev card" };
export const BUILD_ORDER = ["city", "settle", "dev", "road"]; // most valuable first: what a recommendation aims for

// bank/port trades needed before `hand` covers `cost`, at your trade rates
export const tradesShort = (hand, cost) => tradesNeeded(hand, cost, S.ratios);

// "Can pay for" chips
export function buildChips(hand) {
  const ks = Object.keys(COSTS).filter((k) => covers(hand, COSTS[k]));
  return ks.map((k) => `<span class="bchip">${BUILD_NAMES[k]}</span>`).join("") || `<span class="muted">nothing yet</span>`;
}

// only what a change of hand unlocks or gives up
export function buildChanges(before, after) {
  const diff = (a, b) => Object.keys(COSTS).filter((k) => !covers(a, COSTS[k]) && covers(b, COSTS[k])).map((k) => BUILD_NAMES[k]);
  const gain = diff(before, after), lost = diff(after, before);
  if (!gain.length && !lost.length) return `<span class="muted">doesn't change what you can build</span>`;
  return [gain.length ? `unlocks ${gain.map((n) => `<span class="bchip gain">${n}</span>`).join("")}` : "",
    lost.length ? `gives up ${lost.map((n) => `<span class="bchip lost">${n}</span>`).join("")}` : ""].filter(Boolean).join(" · ");
}

// a hex by what it is ("9 brick") rather than its letter
export function hexName(label) {
  const h = B?.hexes.find((x) => x.label === label);
  return !h ? label : h.res ? `${h.num} ${h.res}` : "the desert";
}

// a spot by what it touches: "8 wheat, 5 sheep · 3:1 port"
export function spotName(v) {
  const vx = B.vertices[v];
  const hs = (vx.hexes || []).map((h) => B.hexes[h]).filter((h) => h.res).sort((a, b) => b.pips - a.pips).map((h) => `${h.num} ${h.res}`);
  const port = vx.port ? (vx.port === "any" ? "3:1 port" : `2:1 ${vx.port} port`) : "";
  return [hs.slice(0, 2).join(", "), port].filter(Boolean).join(" · ") || "the coast";
}

// a road, or a chain of roads, by where it leads: the far end of the chain, or for a
// single road the end that isn't already on your network
export let vEdges = null;
export function resetBoardCaches() { vEdges = null; }
export function roadsToward(edges) {
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
export function human(s) {
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
export const short = (s) => human(String(s).split(" — ")[0]);

// endpoints of edge e, pulled in from each end by fraction k
export function edgeLine(e, k) {
  const [a, b] = B.edges[e].map((v) => B.vertices[v]);
  return { x1: a.x + (b.x - a.x) * k, y1: a.y + (b.y - a.y) * k, x2: b.x - (b.x - a.x) * k, y2: b.y - (b.y - a.y) * k };
}

export const plain = (s) => String(s).replace(/\brobber (to|on) ([A-Z]{1,2})\b(?: \([^)]*\))?/g, (_, w, l) => `robber ${w} ${hexName(l)}`).replace(/vertex \d+ ?/g, "").replace(/\b(road|settlement at|city at) \d+/g, "$1").replace(/settlement at \[/g, "settlement [").replace(/city at \[/g, "city [");
export const symbol = (v) => ({ forced: "·", best: "✓", good: "✓", inaccuracy: "?!", mistake: "?", blunder: "??" })[v];
export const engineLine = (r) => r.forced ? "no real choice" : r.delta < 0.05 ? "engine agrees" : `better: ${short(r.best)}`;

// enough of the engine's notation for human() to read a single step
export function describeStep(a) {
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

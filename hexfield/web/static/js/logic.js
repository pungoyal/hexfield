// Pure functions: no DOM, no shared state. Everything here runs under `node --test`
// (tests/js) as well as in the browser.

export const RES = ["timber", "clay", "wool", "grain", "stone"];

// "2 grain, stone" / "grain, grain, stone" -> [0,0,0,2,1]
export function parseCards(msg) {
  const c = [0, 0, 0, 0, 0];
  for (const m of msg.matchAll(/(?:(\d+) )?(timber|clay|wool|grain|stone)\b/g)) c[RES.indexOf(m[2])] += Number(m[1] || 1);
  return c;
}

export const covers = (hand, cost) => cost.every((n, r) => hand[r] >= n);
export const handPlus = (hand, d) => hand.map((n, r) => n + (d[r] || 0));

// bank/dock trades needed before `hand` covers `cost` (Infinity if the spare cards can't make it)
export function tradesNeeded(hand, cost, ratios) {
  const miss = cost.reduce((a, n, r) => a + Math.max(0, n - hand[r]), 0);
  const spare = hand.reduce((a, n, r) => a + Math.floor(Math.max(0, n - cost[r]) / ratios[r]), 0);
  return spare >= miss ? miss : Infinity;
}

// Spot codes in rank order: the top spots get one letter, the rest two letters that begin
// with the hardest-to-reach letters (so a one-letter code is never the start of a longer one)
export function assignCodes(items, abc) {
  const n = items.length, L = abc.length;
  let pre = 0;
  while (L - pre + pre * L < n) pre++;
  const singles = abc.slice(0, L - pre), prefixes = abc.slice(L - pre);
  items.forEach((it, i) => {
    const j = i - singles.length;
    it.code = j < 0 ? singles[i] : prefixes[Math.floor(j / L)] + abc[j % L];
  });
  return items;
}

// the badge label a key press produces: "R", "⇧V", "4", "⇧2", "⌥1", "↵", "⌫", "?"
export function keyLabel(e) {
  if (e.key === "Enter") return "↵";
  if (e.key === "Backspace") return "⌫";
  if (e.key === "Escape") return "Esc";
  const digit = /^Digit(\d)$/.exec(e.code);
  if (digit) return (e.altKey ? "⌥" : "") + (e.shiftKey ? "⇧" : "") + digit[1];
  if (e.altKey) return null;
  if (/^Key[A-Z]$/.test(e.code)) return (e.shiftKey ? "⇧" : "") + e.code.slice(3);
  return e.key.length === 1 ? e.key : null;
}

// what a key does while a replay is on screen
export function replayStep(e) {
  switch (e.key) {
    case "ArrowRight": return e.shiftKey ? "last" : "next";
    case "ArrowLeft": return e.shiftKey ? "first" : "prev";
    case "Home": return "first";
    case "End": return "last";
    case "]": return "nextd";
    case "[": return "prevd";
    case " ": return "play";
    case "Escape": return "close";
    default: return null;
  }
}

// review arithmetic (forced moves are listed but never scored)
export const scored = (revs) => revs.filter((r) => !r.forced);
export const accuracy = (real) => (real.length ? Math.round((100 * real.filter((r) => r.delta < 0.2).length) / real.length) : 100);
export const totalLost = (real) => real.reduce((a, r) => a + r.delta, 0);

// "today 14:05", "yesterday 09:30", "3 Nov"
export function fmtWhen(ts, now = Date.now()) {
  if (!ts) return "";
  const d = new Date(ts * 1000), n = new Date(now);
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(n) - day(d)) / 864e5);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (days === 0) return `today ${hm}`;
  if (days === 1) return `yesterday ${hm}`;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getDate()} ${months[d.getMonth()]}${d.getFullYear() === n.getFullYear() ? "" : " " + d.getFullYear()}`;
}

// points of a line chart: values spread across W, scaled between lo and hi into H (y grows downward)
export function sparkPoints(values, W, H, lo, hi, pad = 12) {
  const n = values.length;
  return values.map((v, i) => {
    const x = n === 1 ? W / 2 : pad + ((W - 2 * pad) * i) / (n - 1);
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
    return [Math.round(x * 10) / 10, Math.round((H - pad - t * (H - 2 * pad)) * 10) / 10];
  });
}

// the viewBox that frames a board of any size, docks included
export function boardBox(vertices, marginX = 40, marginY = 54) {
  const xs = vertices.map((v) => v.x), ys = vertices.map((v) => v.y);
  const x0 = Math.min(...xs) - marginX, y0 = Math.min(...ys) - marginY;
  const w = Math.max(...xs) + marginX - x0, h = Math.max(...ys) + marginY - y0;
  return { x: Math.round(x0), y: Math.round(y0), w: Math.round(w), h: Math.round(h), cx: x0 + w / 2, cy: y0 + h / 2 };
}

// Windfall: the two cards that complete the best build, else the two you hold fewest of
export function windfallPicks(hand, bank, costs, order) {
  const avail = (r, n) => bank[r] >= n;
  for (const b of order) {
    const need = costs[b].flatMap((c, r) => Array(Math.max(0, c - hand[r])).fill(r));
    if (need.length && need.length <= 2 && need.every((r) => avail(r, need.filter((x) => x === r).length))) {
      if (need.length === 1) need.push(RES.map((_, r) => r).filter((r) => avail(r, r === need[0] ? 2 : 1))
        .sort((a, b2) => hand[a] - hand[b2])[0]);
      return need;
    }
  }
  const low = RES.map((_, r) => r).filter((r) => avail(r, 1)).sort((a, b) => hand[a] - hand[b]);
  return [low[0], low[1] ?? low[0]];
}

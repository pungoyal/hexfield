// Dialogs: discard, offers, trading, ventures, help, game over, new game.

import { act, apply, pump, render } from "./app.js";
import { openAccount } from "./account.js";
import { askCoachOffer } from "./coach.js";
import { modal, toast } from "./effects.js";
import { setObvious } from "./keys.js";
import { windfallPicks } from "./logic.js";
import { G, S, api, myDecision } from "./state.js";
import { BUILD_NAMES, BUILD_ORDER, buildChanges, buildChips, covers, handPlus, human, plain, symbol } from "./text.js";
import { $, $$, COSTS, RES, delta, esc, icon, pc, resIcon, save, sleep, store } from "./util.js";

// ------------------------------------------------------------------ dialogs
export function stepperRow(r, max, onChange) {
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
export function handStrip(el, delta = [0, 0, 0, 0, 0], label = "Your hand") {
  el.innerHTML = `<span class="th-label">${label}</span>` + RES.map((r, i) => {
    const n = S.hand[i], d = delta[i] || 0, after = n + d;
    const cls = d > 0 ? "up" : d < 0 ? "down" : "";
    return `<span class="th-res ${after ? "" : "zero"} ${cls}" title="${n} ${r}${d ? ` → ${after}` : ""}">${resIcon(i)}<b>${after}</b>${d ? `<small class="th-d">${d > 0 ? "+" : "−"}${Math.abs(d)}</small>` : ""}</span>`;
  }).join("") + handTotal(S.hand.reduce((a, b) => a + b, 0) + delta.reduce((a, b) => a + b, 0)) +
    (delta.some(Boolean) ? `<span class="th-builds">${buildChanges(S.hand, handPlus(S.hand, delta))}</span>`
      : `<span class="th-builds"><span class="th-label">Can pay for</span>${buildChips(S.hand)}</span>`);
}

// card count, with the cost of a 7 once it's over seven
export function handTotal(n) {
  return `<span class="th-total ${n > 7 ? "over" : ""}">${n} card${n === 1 ? "" : "s"}${n > 7 ? ` · a 7 would cost you ${Math.floor(n / 2)}` : ""}</span>`;
}

export function openDiscard() {
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
    setObvious(dlg, total() === need ? $("#discard-ok") : $("#discard-hint"));
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

// An offer made to you (by a bot, or in a shared game by another player): accept,
// decline, or answer with different terms.
const counter = { on: false, give: [0, 0, 0, 0, 0], get: [0, 0, 0, 0, 0] }; // from your side

export function openBotOffer() {
  const dlg = $("#dlg-botoffer");
  const po = S.pending_offer;
  if (!po) { if (dlg.open) dlg.close(); return; }
  const sig = JSON.stringify([po.from, po.give, po.get, po.note, po.counters_left]);
  if (dlg.open && dlg.dataset.sig === sig) return;
  const fresh = dlg.dataset.offer !== JSON.stringify([po.from, po.give, po.get]) || !dlg.open;
  dlg.dataset.sig = sig;
  dlg.dataset.offer = JSON.stringify([po.from, po.give, po.get]);
  $("#bo-name").textContent = S.players[po.from].name;
  $("#bo-dot").style.background = pc(po.from);
  const mini = (counts) => counts.map((n, r) => n ? `<div class="rcard r-${RES[r]}">${icon("a-" + RES[r])}<span class="n">${n}</span></div>` : "").join("");
  $("#bo-get").innerHTML = mini(po.give);
  $("#bo-give").innerHTML = mini(po.get);
  handStrip($("#bo-hand"), RES.map((_, r) => po.give[r] - po.get[r]), "After");
  $("#bo-note").textContent = po.note || "";
  if (fresh) { counter.on = false; counter.give = po.get.slice(); counter.get = po.give.slice(); }
  $("#bo-counter-btn").hidden = po.counters_left <= 0;
  if (po.counters_left <= 0) counter.on = false;
  const answer = async (body) => {
    while (G.busy) await sleep(40);
    // a second press while the first answer was in flight: that offer is already answered
    const now = S.pending_offer;
    if (!now || JSON.stringify([now.from, now.give, now.get, now.note, now.counters_left]) !== sig) return;
    G.busy = true;
    try {
      apply(await api("respond", body));
      if (!S.pending_offer) dlg.close();
    } catch (e) { toast(e.message, "", "var(--worst)"); }
    G.busy = false;
    render();
    openBotOffer();
    pump();
  };
  $("#bo-yes").onclick = () => answer({ accept: true });
  $("#bo-no").onclick = () => answer({ accept: false });
  $("#bo-counter-btn").onclick = () => { counter.on = !counter.on; renderCounter(); };
  // the offer is written from the proposer's side: it gives what you get
  $("#boc-send").onclick = () => answer({ counter: { give: counter.get, get: counter.give } });
  $("#bo-counter").onclick = (e) => {
    const pick = e.target.closest("[data-side]");
    if (!pick) return;
    const side = pick.dataset.side, r = Number(pick.dataset.r), other = side === "give" ? "get" : "give";
    counter[side][r] = Math.max(0, counter[side][r] + (pick.dataset.d === "-1" ? -1 : 1));
    if (counter[side][r]) counter[other][r] = 0;
    renderCounter();
  };
  renderCounter();
  if (fresh) setObvious(dlg, $("#bo-no")); // safe default until the coach's verdict arrives
  if (!dlg.open) modal(dlg);
  if (fresh) askCoachOffer(); // the verdict is cheap: show it straight away
}

function renderCounter() {
  const po = S.pending_offer;
  $("#bo-counter").hidden = !counter.on;
  $("#bo-counter-btn").setAttribute("aria-pressed", String(counter.on));
  if (!counter.on || !po) return;
  counter.give = counter.give.map((n, r) => Math.min(n, S.hand[r]));
  const tile = (side, r) => {
    const n = counter[side][r], max = side === "give" ? S.hand[r] : 9;
    return `<div class="pick ${n ? "on" : ""} r-${RES[r]}">
      <button class="pick-add" data-side="${side}" data-r="${r}" ${n < max ? "" : "disabled"} aria-label="${side === "give" ? "give" : "ask for"} one more ${RES[r]}">${icon("i-" + RES[r])}${n ? `<span class="n">×${n}</span>` : ""}<span class="have">have ${S.hand[r]}</span></button>
      <button class="pick-sub" data-side="${side}" data-r="${r}" data-d="-1" ${n ? "" : "hidden"} aria-label="one fewer ${RES[r]}">−</button></div>`;
  };
  $("#boc-give").innerHTML = RES.map((_, r) => tile("give", r)).join("");
  $("#boc-get").innerHTML = RES.map((_, r) => tile("get", r)).join("");
  const fmt = (c) => c.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(" + ");
  const same = JSON.stringify(counter.give) === JSON.stringify(po.get) && JSON.stringify(counter.get) === JSON.stringify(po.give);
  const ready = counter.give.some(Boolean) && counter.get.some(Boolean) && !same;
  $("#boc-summary").innerHTML = ready ? `<b>${fmt(counter.give)}</b> <span class="muted">for</span> <b>${fmt(counter.get)}</b>`
    : `<span class="muted">${same ? "Change the cards to make it your offer" : "Pick cards on both sides"}</span>`;
  $("#boc-send").disabled = !ready;
  $("#bo-note").textContent = po.note || (po.counters_left ? `${po.counters_left} counter-offer${po.counters_left === 1 ? "" : "s"} left` : "");
}

// 20 bank cells, row by row; J K L are the reply buttons, digits the offer tiles
export const BANK_KEYS = "ABCDEFGHIMNOPQRSTUVW";

// Trade dialog: bank/docks and player offers side by side at a fixed size.
// Give/want tiles add one card per click; the small minus takes one back.
export const trade = { give: [0, 0, 0, 0, 0], want: [0, 0, 0, 0, 0], replies: null, note: "", sent: null };

export function clearOffer(note = "") {
  trade.give = [0, 0, 0, 0, 0];
  trade.want = [0, 0, 0, 0, 0];
  trade.replies = null;
  trade.note = note;
  renderTrade();
}

export function openTrade() {
  if (!myDecision() || S.phase !== "main") return;
  clearOffer();
  modal($("#dlg-trade"));
}

export function renderTrade() {
  const live = myDecision() && S.phase === "main";
  // in a shared game the other players' answers arrive one by one
  if (trade.sent && S.my_offer) trade.replies = S.my_offer.replies;
  trade.give = trade.give.map((n, r) => Math.min(n, S.hand[r]));
  $("#trade-count").textContent = `· ${S.hand.reduce((a, b) => a + b, 0)} cards in hand`;

  $("#bank-trade").innerHTML = `<div class="bt-row bt-head"><span>You give · rate</span><span class="bt-get">for 1 of <span class="muted">(→ how many you'd have)</span></span></div>` + RES.map((r, i) => {
    const ratio = S.ratios[i];
    const ok = live && S.hand[i] >= ratio;
    let cell = i * 4; // fixed letters per give/get pair, whatever is affordable
    if (!ok) {
      // can't pay this rate: one compact line instead of a row of dead buttons
      return `<div class="bt-row off"><div class="bt-give">${resIcon(i)}<b class="bt-have">${S.hand[i]}</b><span class="bt-rate ${ratio < 4 ? "dock" : ""}">${ratio}:1</span></div><span class="bt-need">${live ? `need ${ratio - S.hand[i]} more ${r} to trade at ${ratio}:1` : "trade after you roll, on your turn"}</span></div>`;
    }
    const unlocks = (j) => {
      const after = S.hand.map((n, k) => n + (k === j) - (k === i ? ratio : 0));
      const gain = Object.keys(COSTS).filter((k) => !covers(S.hand, COSTS[k]) && covers(after, COSTS[k]));
      return gain.length ? `<i class="bt-unlock" title="Lets you pay for: ${gain.map((k) => BUILD_NAMES[k]).join(", ")}">★</i>` : "";
    };
    const gets = RES.map((g, j) => j === i ? `<span class="bt-self" aria-hidden="true">·</span>` :
      `<button class="btn res-btn" data-give="${i}" data-get="${j}" data-key="${BANK_KEYS[cell++]}" ${S.bank[j] > 0 ? "" : "disabled"} title="Give ${ratio} ${r} (${S.hand[i]} → ${S.hand[i] - ratio}), get 1 ${g} (${S.hand[j]} → ${S.hand[j] + 1})${S.bank[j] ? "" : " — bank is out"}" aria-label="Give ${ratio} ${r} for 1 ${g}">${resIcon(j)}<span class="bt-cnt">${S.bank[j] ? `→ ${S.hand[j] + 1}` : "out"}</span>${unlocks(j)}</button>`).join("");
    return `<div class="bt-row"><div class="bt-give">${resIcon(i)}<b class="bt-have">${S.hand[i]}</b><span class="bt-rate ${ratio < 4 ? "dock" : ""}">${ratio}:1</span><small class="bt-after">→ ${S.hand[i] - ratio}</small></div>${gets}</div>`;
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
  $("#offer-send").disabled = !live || !ready || G.busy;
  $("#offer-clear").disabled = !trade.give.some(Boolean) && !trade.want.some(Boolean);

  const box = $("#offer-replies");
  if (trade.replies) {
    let nth = 0;
    // a bot (or player) that says no may name its price: trading on those terms is one click too
    const fmt2 = (c) => c.map((n, r) => n ? `${n} ${RES[r]}` : "").filter(Boolean).join(" + ");
    box.innerHTML = trade.replies.map((r) => {
      const c = r.counter;
      const can2 = c && c.give.every((n, k) => n <= S.hand[k]);
      const ans = r.ok === null ? `<span class="ans muted">thinking…</span>`
        : c ? `<span class="ans counter" title="Their counter-offer">wants <b>${fmt2(c.give)}</b> for ${fmt2(c.get)}</span>`
        : `<span class="ans ${r.ok ? "yes" : "no"}">${r.ok ? "accepts" : "declines"}</span>`;
      const btn = r.ok ? `<button class="btn primary" data-q="${r.p}" data-key="${"JKLMNO"[nth++]}" ${live ? "" : "disabled"}>Trade</button>`
        : c ? `<button class="btn" data-q="${r.p}" data-counter="1" data-key="${"JKLMNO"[nth++]}" ${live && can2 ? "" : "disabled"} title="${can2 ? "Trade on their terms" : "You don't have those cards"}">Take it</button>` : "";
      return `<div class="reply" style="--pc:${pc(r.p)}"><span class="dot"></span><b>${esc(S.players[r.p].name)}</b>
      ${r.why && !c ? `<span class="why muted">${esc(r.why)}</span>` : ""}${ans}${btn}</div>`;
    }).join("");
  } else {
    box.innerHTML = `<p class="coach-note">${esc(trade.note || "Every bot answers at once; you pick who to trade with.")}</p>`;
  }
  obviousTrade();
}

export function obviousTrade() {
  const dlg = $("#dlg-trade");
  const accept = $("#offer-replies [data-q]:not(:disabled):not([data-counter])");
  if (accept) return setObvious(dlg, accept);
  if (!$("#offer-send").disabled && !trade.replies) return setObvious(dlg, $("#offer-send"));
  let best = null, bestRank = Infinity, bestLeft = -1;
  for (const b of $$("#bank-trade [data-give]:not(:disabled)")) {
    const i = Number(b.dataset.give), j = Number(b.dataset.get);
    const after = S.hand.map((n, k) => n + (k === j) - (k === i ? S.ratios[i] : 0));
    const rank = BUILD_ORDER.findIndex((k) => !covers(S.hand, COSTS[k]) && covers(after, COSTS[k]));
    const left = after[i]; // keep the most of what you give
    if (rank >= 0 && (rank < bestRank || (rank === bestRank && left > bestLeft))) { best = b; bestRank = rank; bestLeft = left; }
  }
  setObvious(dlg, best);
}

export async function sendOffer() {
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

export async function onTradeClick(e) {
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
    const who = Number(q.dataset.q), name = S.players[who].name;
    const terms = q.dataset.counter ? trade.replies.find((r) => r.p === who).counter : trade.sent;
    if (await act(["trade", who, terms.give, terms.get])) return clearOffer(`Traded with ${name}.`);
    return renderTrade();
  }
  if (e.target.closest("#offer-send")) return sendOffer();
  if (e.target.closest("#offer-clear")) clearOffer();
}

export function openPick(kind) {
  const dlg = $("#dlg-pick");
  const windfall = kind === "windfall";
  $("#pick-title").textContent = windfall ? "Windfall" : "Hoard";
  $("#pick-sub").textContent = windfall ? "Take any two resources from the bank (the same one twice is fine)." : "Every opponent hands you all their cards of one resource. ≈ is what your card tracker reckons they hold between them.";
  const haul = (r) => Math.round(S.tracker?.[r] ?? 0);
  const picks = [];
  const box = $("#pick-res");
  const rec = windfall ? windfallPicks(S.hand, S.bank, COSTS, BUILD_ORDER) : [RES.map((_, i) => i).sort((a, b) => (S.tracker?.[b] ?? 0) - (S.tracker?.[a] ?? 0))[0]];
  const draw = () => {
    box.innerHTML = RES.map((r, i) => {
      const n = picks.filter((x) => x === i).length;
      const off = windfall && S.bank[i] < 1;
      const est = windfall || !S.tracker ? "" : `<span class="haul" title="Your tracker: opponents hold about ${S.tracker[i]} ${r}">≈${haul(i)}</span>`;
      return `<button data-r="${i}" data-key="${i + 1}" aria-pressed="${n > 0}" ${off ? "disabled" : ""} title="${r} (${i + 1})"><div class="rcard r-${r}">${icon("a-" + r)}<span class="lbl">${r}</span>${n ? `<span class="n">${n}</span>` : ""}</div>${est}</button>`;
    }).join("");
    $("#pick-ok").disabled = picks.length !== (windfall ? 2 : 1);
    if (windfall) handStrip($("#pick-hand"), RES.map((_, r) => picks.filter((x) => x === r).length), picks.length ? "After" : "Your hand");
    else handStrip($("#pick-hand"), RES.map((_, r) => (picks[0] === r ? haul(r) : 0)), picks.length ? "About" : "Your hand");
    // the next recommended card, then Play
    const left = rec.slice();
    for (const x of picks) { const i = left.indexOf(x); if (i >= 0) left.splice(i, 1); }
    const done = picks.length === (windfall ? 2 : 1);
    setObvious(dlg, done ? $("#pick-ok") : box.querySelector(`[data-r="${left[0] ?? rec[0]}"]`));
  };
  box.onclick = (e) => {
    const b = e.target.closest("[data-r]");
    if (!b) return;
    const r = Number(b.dataset.r);
    if (windfall) {
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
    if (windfall) act(["play_windfall", Math.min(...picks), Math.max(...picks)]);
    else act(["play_hoard", picks[0]]);
  };
  draw();
  modal(dlg);
}

export function openInfo(title, html, wide = false) {
  $("#info-title").textContent = title;
  $("#info-body").innerHTML = html;
  $("#dlg-info").classList.toggle("wide", wide);
  modal($("#dlg-info"));
}

export function showStats() {
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

export function showHelp() {
  const k = (keys, what) => `<span>${keys.map((x) => `<kbd>${x}</kbd>`).join(" ")}</span><span>${what}</span>`;
  const cost = (c) => c.flatMap((n, r) => Array.from({ length: n }, () => resIcon(r))).join("");
  openInfo("How to play", `<div class="help">
    <p>First to <b>10 fame</b> on their own turn wins. Villages are worth 1, towns 2, Great Trail (5+ trails) and Great Patrol (3+ rangers) 2 each, and hidden landmark 1.</p>
    <p>Each roll pays every village next to a hex with that number 1 card (a town 2), unless the bandit sits on it. On a 7 everyone holding more than 7 cards discards half, then the bandit moves and steals a card. You can play one venture per turn, never on the turn you bought it — rangers may be played before rolling.</p>
    <h3>Build costs</h3>
    <div class="help-costs">
      <span>Trail</span><span>${cost(COSTS.trail)}</span><span>Village <span class="muted">1 fame</span></span><span>${cost(COSTS.settle)}</span>
      <span>Town <span class="muted">2 fame, pays double</span></span><span>${cost(COSTS.town)}</span><span>Venture</span><span>${cost(COSTS.venture)}</span>
    </div>
    <p>Trade 4 of a kind with the bank for any card — 3 with a 3:1 dock, 2 of that resource with a 2:1 dock — or offer cards to the other players. A player who says no may name a price, and you can answer any offer with a <b>counter-offer</b> of your own.</p>
    <p>With <b>5 or 6 players</b> the board is larger, and after every turn the other players may build (no trading, no ventures) in a special building phase: build, or pass.</p>
    <p>Every game is <b>saved as you play</b>. <b>Games</b> lists them: resume one, step through a <b>replay</b> with the coach's verdict on each of your decisions, play on from any point, or see your <b>progress</b> across games. A new game can also be <b>pass and play</b> on one screen or <b>online</b> with a device each.</p>
    <p>You only see your own cards, exactly like at a real table. Opponents show card <i>counts</i>; the bots get the same information and track your hand from public events.</p>
    <p>The <b>coach</b> reviews every decision in fame-equivalents: ✓ best &lt; 0.05 ≤ ✓ good &lt; 0.2 ≤ ?! inaccuracy &lt; 0.5 ≤ ? mistake &lt; 1.0 ≤ ?? blunder.</p>
    <details class="help-keys"><summary>Keyboard shortcuts</summary>
    <p class="muted">Every button with a key shows it in a small badge. A key means one thing on each screen.</p>
    <h3>Your turn</h3>
    <div class="help-grid">
      ${k(["R"], "Roll the dice")}${k(["1", "2", "3"], "Build a trail / village / town: then type the letter on the spot")}
      ${k(["D"], "Buy a venture")}${k(["4", "5", "6", "7"], "Play Ranger / Pathfinder / Hoard / Windfall")}
      ${k(["T"], "Trade with the bank, docks or players")}${k(["E"], "End your turn")}
      ${k(["Space"], "The obvious move, everywhere: the green-ringed choice. On the board the best spot or the roll; in a dialog the recommended answer (the coach's verdict on an offer, the suggested discard, the trade that unlocks your best build, the richest player to rob). Keep pressing to follow a multi-step recommendation")}
      ${k(["A", "S", "F", "…"], "Pick a board spot (setup, build modes, bandit). Spots are ranked: A is always the best, then S, F, G… down the home row; weaker spots get two letters")}
      ${k(["⌫"], "Take back a typed letter")}${k(["1", "2", "3"], "Choose who to rob (when asked)")}
      ${k(["⇧D"], "Reopen the discard dialog")}${k(["⇧Y"], "Reopen a bot's trade offer")}${k(["Esc"], "Cancel: typed letters, then build mode, then the coach panel")}
    </div>
    <h3>Coach and game</h3>
    <div class="help-grid">
      ${k(["H"], "Hint: the engine's top options")}${k(["⇧1–⇧5"], "Play hint option 1–5")}${k(["⇧V"], "Evaluate every player")}${k(["⇧A"], "All your moves, rated")}
      ${k(["U"], "Undo your last decision, on every screen and in every dialog (the dice stay the same)")}${k(["⇧S"], "Dice statistics")}${k(["⇧N"], "New game")}${k(["⇧G"], "Saved games, replays, progress")}${k(["?"], "This help")}
      ${k(["⇧C"], "Colour-blind marks: a safer palette and seat numbers on the pieces")}${k(["Tab", "↵"], "Every board spot is a button: Tab to it, Enter to build")}
      ${k(["⇧R"], "Turn move reviews on/off")}${k(["⇧B"], "Show/hide bot reasoning")}${k(["⇧F"], "Bot speed")}${k(["⇧O"], "Auto-roll on/off")}
    </div>
    <h3>In a replay</h3>
    <div class="help-grid">
      ${k(["←", "→"], "One move back / forward")}${k(["[", "]"], "Your previous / next decision")}${k(["Home", "End"], "Start / end of the game")}
      ${k(["Space"], "Play or pause")}${k(["Esc"], "Leave the replay")}
    </div>
    <h3>In dialogs</h3>
    <div class="help-grid">
      ${k(["1", "…", "5"], "Timber, clay, wool, grain, stone: add one (discard, offer you give, Windfall, Hoard)")}
      ${k(["⇧1", "…", "⇧5"], "Discard: remove one · Offer: add one to what you want")}${k(["⌥1", "…"], "Offer: take one back (⌥⇧ for the want side)")}
      ${k(["A", "…", "W"], "Trade: the bank or dock trade with that letter")}${k(["J", "K", "L"], "Trade: accept a bot's answer")}
      ${k(["Y", "N"], "An offer to you: accept, decline (Space follows the coach)")}${k(["C"], "An offer to you: make a counter-offer")}${k(["S"], "Discard: suggest")}${k(["↵"], "Confirm (discard, play, send offer, start game)")}${k(["⌫"], "Clear the offer")}${k(["Esc"], "Close")}
    </div>
    </details>
  </div>`);
}

export function showGameOver() {
  const sm = S.summary;
  if (!sm) return;
  $("#over-watch").hidden = S.own === false; // a game you were invited to is saved by its host
  const won = S.winner === S.human;
  $("#over-title").innerHTML = `<div class="over-banner ${won ? "win" : ""}">${won ? "♛ You win!" : `${esc(S.players[S.winner].name)} wins`}</div>`;
  // where every point came from
  const pts = (fame, note) => (fame ? `${fame}${note ? `<small class="muted"> ${note}</small>` : ""}` : `<span class="muted">–</span>`);
  const rows = sm.rows.map((r) => `<tr><td><span style="color:${pc(r.p)};font-weight:700">${esc(S.players[r.p].name)}</span></td>
    <td class="num">${pts(r.villages, "")}</td><td class="num">${pts(2 * r.towns, r.towns ? `(${r.towns})` : "")}</td>
    <td class="num">${pts(r.longest ? 2 : 0)}</td><td class="num">${pts(r.patrol ? 2 : 0)}</td><td class="num">${pts(r.landmarks)}</td>
    <td class="num"><b>${r.fame}</b></td></tr>`).join("");
  let rev = "";
  if (sm.accuracy !== null) {
    rev = `<h3 style="margin-top:18px">Your decisions</h3>
      <div class="vcounts">${Object.entries(sm.counts).map(([k, n]) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${n}</span>`).join("")}</div>
      <p>Accuracy <b>${Math.round(sm.accuracy)}%</b> · total value lost <b>${sm.lost.toFixed(2)}</b> fame-eq · ${sm.rounds} round${sm.rounds === 1 ? "" : "s"} · <button class="btn ghost" id="over-all" data-key="A" style="padding:2px 8px">every move…</button></p>
      ${sm.worst.length ? `<h3>Biggest misses</h3><ul class="misses">${sm.worst.map((w) => `<li><span class="vd vd-${w.verdict}">−${w.delta.toFixed(2)}</span> ${w.turn ? `turn ${w.turn}` : "setup"}: ${esc(plain(w.label))}<br><span class="eng">better: ${esc(human(w.best))}</span></li>`).join("")}</ul>` : ""}`;
  }
  $("#over-body").innerHTML = `<div class="table-x"><table class="info-table fame-table"><thead><tr><th>Player</th><th title="1 fame each">Villages</th><th title="2 fame each (count)">Towns</th><th title="2 fame">Great Trail</th><th title="2 fame">Great Patrol</th><th title="1 fame each">Landmarks</th><th>Total</th></tr></thead><tbody>${rows}</tbody></table></div>${rev}`;
  modal($("#dlg-over"));
}

export function openNewGame(cancellable = true) {
  if (G.me?.accounts && !G.me.host) return openAccount(); // sign in, or wait for the admin's approval
  const dlg = $("#dlg-new");
  if (dlg.open) return;
  $("#new-cancel").hidden = !cancellable || !S;
  const last = store("newgame", null);
  const f = $("#form-new");
  for (const [k, v] of Object.entries(last || {})) {
    if (k === "name") f.elements.name.value = v;
    else if (k === "names") f.dataset.names = JSON.stringify(v);
    else { const radio = f.querySelector(`[name="${k}"][value="${v}"]`); if (radio) radio.checked = true; }
  }
  syncNewGame();
  f.elements.seed.value = "";
  dlg.returnValue = "";
  modal(dlg);
}

// keep the form consistent: 3 to 6 players in all, seats that exist, names for everyone
export function syncNewGame() {
  const f = $("#form-new");
  const mode = f.elements.mode.value;
  const solo = mode === "solo";
  const humans = solo ? 1 : Number(f.elements.humans.value);
  $("#new-multi").hidden = solo;
  $("#new-seat").hidden = !solo;
  $("#new-name-field").hidden = mode === "hotseat";
  for (const lab of $$("#form-new .opp")) {
    const n = Number(lab.querySelector("input").value) + humans;
    lab.hidden = n < 3 || n > 6;
  }
  if (f.querySelector('[name="opponents"]:checked').closest("label").hidden) {
    const ok = $$("#form-new .opp").filter((l) => !l.hidden).map((l) => l.querySelector("input"));
    (ok.find((i) => Number(i.value) + humans === 4) || ok[0]).checked = true;
  }
  const total = humans + Number(f.elements.opponents.value);
  for (let k = 4; k <= 6; k++) f.querySelector(`.seat${k}`).hidden = total < k;
  if (Number(f.elements.seat.value) > total) f.querySelector('[name="seat"][value=""]').checked = true;
  const large = total >= 5;
  $("#new-size-note").textContent = large
    ? `${total} players: the large board (30 hexes), and after every turn the others may build in a special building phase.` : "";
  $("#new-layout").hidden = large;
  if (mode === "hotseat") {
    const saved = JSON.parse(f.dataset.names || "[]");
    const box = $("#new-names");
    const have = $$("input", box).map((i) => i.value);
    box.innerHTML = Array.from({ length: humans }, (_, i) => `<label class="field">Player ${i + 1}<input name="pname${i}" maxlength="14" value="${esc(have[i] ?? saved[i] ?? `Player ${i + 1}`)}" required></label>`).join("");
  } else {
    $("#new-names").replaceChildren();
  }
  $("#new-multi-note").textContent = mode === "hotseat" ? "One screen for everyone: it goes dark between players, so nobody sees another hand."
    : mode === "online" ? "You get a link to send the others. They need to reach this computer: start the server with --lan on a shared network." : "";
}

export function newGameOptions() {
  const f = $("#form-new");
  const mode = f.elements.mode.value;
  const opts = { mode, opponents: Number(f.elements.opponents.value), level: f.elements.level.value, name: f.elements.name.value,
    seed: f.elements.seed.value, styles: f.elements.styles.value, dice: f.elements.dice.value, layout: f.elements.layout.value };
  if (mode === "solo") opts.seat = f.elements.seat.value;
  else {
    opts.humans = Number(f.elements.humans.value);
    if (mode === "hotseat") opts.names = $$("#new-names input").map((i) => i.value);
  }
  save("newgame", { mode, opponents: opts.opponents, level: opts.level, seat: opts.seat ?? "", name: opts.name, humans: opts.humans ?? 2,
    styles: opts.styles, dice: opts.dice, layout: opts.layout, names: opts.names ?? [] });
  return opts;
}

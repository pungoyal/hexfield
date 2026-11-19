// The coach panel: move reviews, hints, evaluation.

import { act, apply } from "./app.js";
import { clearGlow, glowFor } from "./board.js";
import { openInfo } from "./dialogs.js";
import { toast } from "./effects.js";
import { setObvious } from "./keys.js";
import { G, S, api, myDecision } from "./state.js";
import { ACCURACY_NOTE, FAME_LOST, accuracy, describeStep, engineLine, human, plain, scored, short, symbol, totalLost } from "./text.js";
import { $, $$, VERDICT_COLORS, esc, pc, resIcon, sleep } from "./util.js";

// the coach panel answers one decision; it closes once the game moves on
export const decisionKey = () => `${S.turn}|${S.phase}|${S.actor}|${S.ev_total}`;
export function closeCoach() { $("#coach-out").replaceChildren(); G.hintKey = null; optSel = null; clearGlow(); }

// ------------------------------------------------------------------ coach
export function newReviews() {
  const revs = S.reviews;
  if (revs.length < G.reviewsSeen) G.reviewsSeen = revs.length;
  // only real slips get a toast; best and good moves are listed in the coach panel
  for (const r of $("dialog[open]") ? [] : scored(revs.slice(G.reviewsSeen)).filter((x) => x.delta >= 0.2)) {
    toast(`${symbol(r.verdict)} ${r.verdict}  −${r.delta.toFixed(2)}`, `You: ${short(r.label)}\nBetter: ${short(r.best)}`, VERDICT_COLORS[r.verdict], 7000);
  }
  G.reviewsSeen = revs.length;
}

export function renderReviews() {
  const revs = S.reviews;
  const ol = $("#reviews");
  const key = revs.length + ":" + (revs.at(-1)?.delta ?? "") + ":" + S.review_on;
  if (ol.dataset.sig === key) return;
  ol.dataset.sig = key;
  $("#btn-review").disabled = !revs.length;
  const real = scored(revs);
  if (!real.length) {
    ol.replaceChildren();
    $("#accuracy").innerHTML = `<p class="coach-empty">${S.review_on
      ? "Each decision you make is rated here against the engine's best line. Stuck? Ask for a <b>Hint</b>."
      : "Move reviews are off. Turn <b>Review</b> on to have every decision rated."}</p>`;
    return;
  }
  // how the decisions split by verdict, as one bar
  const QUALITY = ["best", "good", "inaccuracy", "mistake", "blunder"];
  const cnt = Object.fromEntries(QUALITY.map((q) => [q, real.filter((r) => r.verdict === q).length]));
  $("#accuracy").innerHTML = `<div class="acc-top" title="${ACCURACY_NOTE}"><b class="acc-n">${accuracy(real)}%</b><span>accuracy</span>
      <span class="acc-meta">${real.filter((r) => r.delta < 0.2).length} of ${real.length} best or good · <b>${totalLost(real).toFixed(2)}</b> fame given up</span></div>
    <div class="acc-bar" role="img" aria-label="${QUALITY.map((q) => `${cnt[q]} ${q}`).join(", ")}">${QUALITY.map((q) => cnt[q] ? `<i class="q-${q}" style="flex:${cnt[q]}" title="${cnt[q]} ${q}"></i>` : "").join("")}</div>`;
  // forced moves only appear in the full "All moves" table
  ol.innerHTML = real.slice().reverse().slice(0, 60).map(reviewItem).join("");
}

export function reviewItem(r) {
  return `<li class="rev v-${r.verdict}" ${r.action ? `data-a='${JSON.stringify(r.action)}'` : ""} ${r.best_action ? `data-b='${JSON.stringify(r.best_action)}'` : ""}>
    <span class="vd vd-${r.verdict}" title="${r.verdict}">${symbol(r.verdict)}</span>
    <span class="rev-move">${esc(short(r.label))}</span>
    <span class="loss-n">${r.delta >= 0.05 ? "−" + r.delta.toFixed(2) : ""}</span>
    ${r.delta >= 0.05 ? `<span class="best-line">${esc(engineLine(r))}</span>` : ""}</li>`;
}

export function showFullReview() {
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
    <p class="muted">Each of your decisions compared with the engine's best line. The number is the fame it gave up (1.00 is about one village). Forced moves (only one sensible choice) are listed but not scored.</p>
    <div class="vcounts">${["best", "good", "inaccuracy", "mistake", "blunder"].map((k) => `<span class="vd vd-${k}">${symbol(k)} ${k} ${counts[k] || 0}</span>`).join("")}</div>
    <p>Accuracy <b>${accuracy(real)}%</b> · <span class="term" title="${FAME_LOST}">fame given up</span> <b>${totalLost(real).toFixed(2)}</b> · ${revs.length} moves</p>
    <div class="table-scroll"><table class="info-table review-table"><thead><tr><th>Turn</th><th>Your move</th><th>Verdict</th><th>Lost</th><th>Engine's best</th></tr></thead><tbody>${rows}</tbody></table></div>`, true);
}

export async function showHint() {
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
export function playButton(o, i) {
  const steps = o.steps || [o.action];
  if (!steps.every(playableHint)) return "<span></span>";
  const what = steps.map((a) => human(describeStep(a))).join(", then ");
  return `<button class="btn do" data-i="${i}" data-key="⇧${i + 1}" title="${esc(`Plays: ${what}${o.more ? " — ask again for the rest" : ""}`)}">${o.more ? "Play step 1" : "Play"}</button>`;
}


export const onScreen = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 40 && r.top < innerHeight - 80; };

// the board spots a line would build on
export let optSel = null;
export function glowSteps(steps) {
  clearGlow();
  for (const a of steps || []) glowFor(a, true);
}
export function optSteps(i) { const o = $("#coach-out")._opts?.[i]; return o ? o.steps || [o.action] : null; }
export function selectOpt(li, scroll) {
  $$("#coach-out .opt.sel").forEach((x) => x.classList.toggle("sel", false));
  optSel = li ? Number(li.dataset.i) : null;
  if (!li) return clearGlow();
  li.classList.add("sel");
  glowSteps(optSteps(optSel));
  if (scroll && !onScreen($("#board"))) $(".board-wrap").scrollIntoView({ block: "start", behavior: "smooth" });
}

export async function playHint(i) {
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

export const CLOSE_COACH = `<button class="co-x" aria-label="Close" title="Back to your move reviews (Esc)">✕</button>`;

// fill the coach panel with what load() returns; it stays until the decision changes
export async function coachPanel(loading, load) {
  const out = $("#coach-out");
  out.innerHTML = `<p class="coach-note">${loading}</p>`;
  try {
    const html = await load(out);
    G.hintKey = decisionKey();
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<p class="coach-note">${esc(e.message)}</p>`;
  }
}

export function playableHint(a) {
  return a && !["macro", "discard", "trade", "propose"].includes(a[0]);
}

// the coach's verdict on a bot's offer, in one line; Space follows it
export async function askCoachOffer() {
  const box = $("#bo-hint");
  const offer = JSON.stringify(S.pending_offer);
  const from = S.players[S.pending_offer.from].name;
  box.className = "bo-coach";
  box.removeAttribute("title");
  box.innerHTML = `<span class="tag">Coach</span><span class="muted">thinking…</span>`;
  try {
    const st = await api("hint");
    if (JSON.stringify(S.pending_offer) !== offer) return; // answered meanwhile
    const h = st.hint;
    setObvious($("#dlg-botoffer"), h.accept ? $("#bo-yes") : $("#bo-no"));
    // the coach declines small gains (and anything that feeds a player on 8+): say which
    const fame = Math.abs(h.gain).toFixed(2);
    const why = h.accept ? `worth +${fame} fame to you`
      : h.gain <= 0 ? `costs you ${fame} fame`
      : S.players[S.pending_offer.from].fame >= 8 ? `only +${fame} fame, and ${esc(from)} is close to winning`
      : `only +${fame} fame: too small to help ${esc(from)}`;
    box.className = `bo-coach ${h.accept ? "yes" : "no"}`;
    box.title = `Net value for you, measured in fame, including what ${from} builds with the cards`;
    box.innerHTML = `<span class="tag">Coach</span><b>${h.accept ? "Accept" : "Decline"}</b><span class="muted">${why}</span>`;
  } catch (e) {
    box.textContent = e.message;
  }
}

export async function showEval() {
  if (!S) return;
  await coachPanel("Evaluating…", async () => {
    const rows = (await api("eval")).eval.rows;
    const top = Math.max(1, ...rows.map((r) => r.value));
    return `<p class="coach-note">${CLOSE_COACH}Engine evaluation from your point of view, measured in fame. Pips = expected cards per 36 rolls.</p><div class="evals">${rows.map((r) => `
      <div class="ev-row" style="--pc:${pc(r.p)}"><b>${esc(S.players[r.p].name)}</b><span class="bar"><i style="width:${Math.max(2, (100 * r.value) / top)}%"></i></span><span>${r.value.toFixed(2)}</span>
      <span class="pips">${r.pips.map((n, i) => `<span>${resIcon(i)}${Math.round(n)}</span>`).join("")}<span>Σ${Math.round(r.pips.reduce((a, b) => a + b, 0))}</span></span></div>`).join("")}</div>`;
  });
}

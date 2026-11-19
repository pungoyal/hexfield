// What the client knows: the server's view, the board, and the talk with the server.

import { save, store } from "./util.js";

// ------------------------------------------------------------------ state
// S (the latest server view) and B (the board geometry) are replaced whole, through
// setS/setB; every other value the modules share lives on G.
export let S = null;
export let B = null;
export function setS(v) { S = v; }
export function setB(v) { B = v; }
export const G = {
  evCount: 0,          // events received so far
  prev: null,          // previous S for diffing
  mode: null,          // main-phase build mode: trail | settle | town
  busy: false,
  pumping: false,
  stopPump: false,     // set by undo: the bot loop stops after its current step
  reviewsSeen: 0,
  epoch: null,         // the server's undo count: when it moves on, the log is sent whole
  tbSeen: 0,           // the last take-back outcome already announced
  logStick: true,
  holdAutoRoll: false, // set by undo so it can step back past a roll
  overShown: null,
  hintKey: null,
  lastRoller: null,    // who threw the dice on show
  readyVentures: new Set(), // ventures playable at the last render, so a card rises only as it becomes playable
  me: null,            // who this browser is on the server (account.js)
};
export const prefs = { speed: store("speed", "normal"), autoroll: store("autoroll", true), reasons: store("reasons", true),
  marks: store("marks", false), mute: store("mute", false) };

// Which game this browser is in, and the seats it may act for. A solo game has one
// token; pass-and-play holds one per human and `seat` says whose turn the screen shows.
// `replay` is set while a saved game is being stepped through instead of played.
export const session = { gid: null, tokens: {}, seat: null, replay: null, ...store("session", {}) };
session.replay = null;
export function setSession(gid, tokens, seat) {
  session.gid = gid;
  session.tokens = tokens || {};
  const seats = Object.keys(session.tokens).map(Number);
  session.seat = seat ?? (seats.includes(session.seat) ? session.seat : seats[0] ?? null);
  save("session", { gid: session.gid, tokens: session.tokens, seat: session.seat });
}
export const localSeats = () => Object.keys(session.tokens).map(Number);
export const els = {};        // svg element caches

// ------------------------------------------------------------------ api
export async function api(route, body, query = {}) {
  const q = new URLSearchParams(route === "new" ? {} : { since: G.evCount });
  if (session.gid && route !== "new") q.set("g", session.gid);
  if (G.epoch !== null && G.evCount && route !== "new") q.set("ep", G.epoch);
  // the seat key goes in a header, never the URL: proxies log URLs, and redact Authorization
  const token = session.tokens[session.seat];
  const headers = token && route !== "new" ? { Authorization: `Bearer ${token}` } : {};
  for (const [k, v] of Object.entries(query)) q.set(k, v);
  const res = await fetch(`/api/${route}?${q}`, body === undefined ? { headers } : {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.data = data;
    throw err;
  }
  return data;
}

export const myDecision = () => S && !session.replay && !S.lobby && S.phase !== "game_over" && S.actor === S.human
  && !S.pending_offer && !S.takeback && (S.waiting_on || [S.human]).includes(S.human);
// building is allowed in your main phase and in the special building phase (5-6 players)
export const building = () => myDecision() && (S.phase === "main" || S.phase === "special_build");
export const legal = () => (myDecision() ? S.legal : {});
export const can = (kind) => (legal().kinds || []).includes(kind);

// a touch-only device: no hover, no keyboard badges, "tap" in the copy
export const TOUCH = matchMedia("(hover: none) and (pointer: coarse)").matches;
export const roundName = () => (S.round ? `round ${S.round}` : "setup");

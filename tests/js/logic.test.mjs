// Unit tests for the browser client's pure logic. Run with: node --test tests/js
import assert from "node:assert/strict";
import test from "node:test";

import {
  accuracy, assignCodes, keyLabel, parseCards, windfallPicks, scored, totalLost, tradesNeeded,
} from "../../hexfield/web/static/js/logic.js";

const COSTS = { trail: [1, 1, 0, 0, 0], settle: [1, 1, 1, 1, 0], town: [0, 0, 0, 2, 3], venture: [0, 0, 1, 1, 1] };
const ORDER = ["town", "settle", "venture", "trail"];

test("parseCards reads the log's card lists", () => {
  assert.deepEqual(parseCards("Vera receives 2 grain, 1 stone"), [0, 0, 0, 2, 1]);
  assert.deepEqual(parseCards("You collect timber, timber, wool"), [2, 0, 1, 0, 0]);
  assert.deepEqual(parseCards("nothing here"), [0, 0, 0, 0, 0]);
  assert.deepEqual(parseCards("3 clay and 10 timber"), [10, 3, 0, 0, 0]);
});

test("tradesNeeded counts bank trades at your rates", () => {
  assert.equal(tradesNeeded([1, 1, 1, 1, 0], COSTS.settle, [4, 4, 4, 4, 4]), 0);
  assert.equal(tradesNeeded([5, 1, 1, 0, 0], COSTS.settle, [4, 4, 4, 4, 4]), 1);     // 4 spare timber -> grain
  assert.equal(tradesNeeded([4, 1, 1, 0, 0], COSTS.settle, [4, 4, 4, 4, 4]), Infinity); // only 3 spare
  assert.equal(tradesNeeded([3, 1, 1, 0, 0], COSTS.settle, [2, 4, 4, 4, 4]), 1);     // a 2:1 timber dock
  assert.equal(tradesNeeded([0, 0, 0, 8, 0], COSTS.town, [4, 4, 4, 3, 4]), Infinity); // 6 spare grain at 3:1 = 2 < 3
  assert.equal(tradesNeeded([0, 0, 0, 11, 0], COSTS.town, [4, 4, 4, 3, 4]), 3);
});

test("assignCodes: best spots get one letter and no code is a prefix of another", () => {
  const abc = "asfgjklqwyuiopzxcvbnm";
  for (const n of [1, 5, 21, 22, 54, 80, 109]) {
    const items = assignCodes(Array.from({ length: n }, () => ({})), abc);
    const codes = items.map((it) => it.code);
    assert.equal(new Set(codes).size, n, `${n} spots need ${n} different codes`);
    assert.equal(codes[0], "a", "the best spot is always A");
    for (const a of codes) for (const b of codes) if (a !== b) assert.ok(!b.startsWith(a), `${a} is a prefix of ${b}`);
    assert.ok(codes.every((c) => c.length <= 2));
    if (n <= abc.length) assert.ok(codes.every((c) => c.length === 1));
    // better spots never have longer codes than worse ones
    for (let i = 1; i < n; i++) assert.ok(codes[i - 1].length <= codes[i].length);
  }
});

test("keyLabel names keys the way the badges do", () => {
  const key = (o) => keyLabel({ key: "", code: "", ...o });
  assert.equal(key({ key: "Enter" }), "↵");
  assert.equal(key({ key: "Escape" }), "Esc");
  assert.equal(key({ key: "Backspace" }), "⌫");
  assert.equal(key({ key: "r", code: "KeyR" }), "R");
  assert.equal(key({ key: "R", code: "KeyR", shiftKey: true }), "⇧R");
  assert.equal(key({ key: "!", code: "Digit1", shiftKey: true }), "⇧1");
  assert.equal(key({ key: "¡", code: "Digit1", altKey: true }), "⌥1");
  assert.equal(key({ key: "å", code: "KeyA", altKey: true }), null);
  assert.equal(key({ key: "?", code: "Slash", shiftKey: true }), "?");
  assert.equal(key({ key: "ArrowLeft", code: "ArrowLeft" }), null);
});

test("review arithmetic leaves forced moves out", () => {
  const revs = [{ delta: 0 }, { delta: 0.19 }, { delta: 0.2 }, { delta: 1.5 }, { delta: 0, forced: true }];
  const real = scored(revs);
  assert.equal(real.length, 4);
  assert.equal(accuracy(real), 50);
  assert.ok(Math.abs(totalLost(real) - 1.89) < 1e-9);
  assert.equal(accuracy([]), 100);
});

test("windfallPicks completes the best build it can", () => {
  const bank = [19, 19, 19, 19, 19];
  assert.deepEqual(windfallPicks([0, 0, 0, 2, 1], bank, COSTS, ORDER), [4, 4]);          // two stone for a town
  assert.deepEqual(windfallPicks([1, 1, 0, 0, 0], bank, COSTS, ORDER), [2, 3]);          // wool + grain: village
  assert.deepEqual(windfallPicks([0, 0, 0, 2, 1], [19, 19, 19, 19, 1], COSTS, ORDER).length, 2);
  assert.notDeepEqual(windfallPicks([0, 0, 0, 2, 1], [19, 19, 19, 19, 1], COSTS, ORDER), [4, 4], "the bank has one stone");
  const picks = windfallPicks([3, 3, 3, 3, 3], [0, 0, 5, 0, 0], COSTS, ORDER);
  assert.deepEqual(picks, [2, 2], "only wool left in the bank");
});

// ---- the camera and the artwork (both DOM-free)
import { zoomBox } from "../../hexfield/web/static/js/logic.js";
import { boardDefs, hexPoints, seeded, terrain } from "../../hexfield/web/static/js/art.js";

test("zoomBox: close targets zoom in, spread ones show the whole board", () => {
  const full = { x: 0, y: 0, w: 600, h: 600 };
  const near = zoomBox(full, [{ x: 300, y: 300 }, { x: 350, y: 330 }], 100);
  assert.ok(near.w < full.w && near.w >= full.w / 1.7 - 1e-9, "zoomed, but never past the limit");
  assert.equal(near.w / near.h, 1, "keeps the board's shape");
  assert.ok(near.x <= 300 && near.x + near.w >= 350 && near.y <= 300 && near.y + near.h >= 330, "targets in view");
  assert.equal(zoomBox(full, [{ x: 20, y: 20 }, { x: 580, y: 560 }], 100), full);
  const corner = zoomBox(full, [{ x: 5, y: 5 }], 100);
  assert.ok(corner.x >= 0 && corner.y >= 0, "never looks past the edge of the board");
});

test("artwork: a hex is always painted the same way, and only from drawings that exist", () => {
  const a = seeded(42), b = seeded(42);
  assert.deepEqual([a(), a(), a()], [b(), b(), b()]);
  const defs = boardDefs();
  for (const res of ["timber", "clay", "wool", "grain", "stone", null]) {
    const hex = { res, num: res ? 8 : 0 };
    const art = terrain(hex, 3);
    assert.equal(art, terrain(hex, 3));
    assert.notEqual(art, terrain(hex, 4), "neighbouring hexes differ");
    for (const m of art.matchAll(/(?:href="#|url\(#)([\w-]+)/g)) assert.ok(defs.includes(`id="${m[1]}"`), `missing drawing ${m[1]}`);
    assert.ok(!/NaN|undefined/.test(art));
  }
  assert.equal(hexPoints(0, 0, 60).split(" ").length, 6);
});

// Unit tests for the browser client's pure logic. Run with: node --test tests/js
import assert from "node:assert/strict";
import test from "node:test";

import {
  accuracy, assignCodes, boardBox, covers, fmtWhen, handPlus, keyLabel, parseCards, plentyPicks, replayStep,
  scored, sparkPoints, totalLost, tradesNeeded,
} from "../../hexfield/web/static/js/logic.js";

const COSTS = { road: [1, 1, 0, 0, 0], settle: [1, 1, 1, 1, 0], city: [0, 0, 0, 2, 3], dev: [0, 0, 1, 1, 1] };
const ORDER = ["city", "settle", "dev", "road"];

test("parseCards reads the log's card lists", () => {
  assert.deepEqual(parseCards("Vera receives 2 wheat, 1 ore"), [0, 0, 0, 2, 1]);
  assert.deepEqual(parseCards("You collect wood, wood, sheep"), [2, 0, 1, 0, 0]);
  assert.deepEqual(parseCards("nothing here"), [0, 0, 0, 0, 0]);
  assert.deepEqual(parseCards("3 brick and 10 wood"), [10, 3, 0, 0, 0]);
});

test("covers and handPlus", () => {
  assert.equal(covers([1, 1, 1, 1, 0], COSTS.settle), true);
  assert.equal(covers([1, 1, 1, 0, 9], COSTS.settle), false);
  assert.deepEqual(handPlus([1, 0, 2, 0, 0], [-1, 2, 0, 0, 0]), [0, 2, 2, 0, 0]);
});

test("tradesNeeded counts bank trades at your rates", () => {
  assert.equal(tradesNeeded([1, 1, 1, 1, 0], COSTS.settle, [4, 4, 4, 4, 4]), 0);
  assert.equal(tradesNeeded([5, 1, 1, 0, 0], COSTS.settle, [4, 4, 4, 4, 4]), 1);     // 4 spare wood -> wheat
  assert.equal(tradesNeeded([4, 1, 1, 0, 0], COSTS.settle, [4, 4, 4, 4, 4]), Infinity); // only 3 spare
  assert.equal(tradesNeeded([3, 1, 1, 0, 0], COSTS.settle, [2, 4, 4, 4, 4]), 1);     // a 2:1 wood port
  assert.equal(tradesNeeded([0, 0, 0, 8, 0], COSTS.city, [4, 4, 4, 3, 4]), Infinity); // 6 spare wheat at 3:1 = 2 < 3
  assert.equal(tradesNeeded([0, 0, 0, 11, 0], COSTS.city, [4, 4, 4, 3, 4]), 3);
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

test("replayStep maps keys to replay moves", () => {
  assert.equal(replayStep({ key: "ArrowRight" }), "next");
  assert.equal(replayStep({ key: "ArrowLeft" }), "prev");
  assert.equal(replayStep({ key: "ArrowRight", shiftKey: true }), "last");
  assert.equal(replayStep({ key: "Home" }), "first");
  assert.equal(replayStep({ key: "]" }), "nextd");
  assert.equal(replayStep({ key: " " }), "play");
  assert.equal(replayStep({ key: "Escape" }), "close");
  assert.equal(replayStep({ key: "r" }), null);
});

test("review arithmetic leaves forced moves out", () => {
  const revs = [{ delta: 0 }, { delta: 0.19 }, { delta: 0.2 }, { delta: 1.5 }, { delta: 0, forced: true }];
  const real = scored(revs);
  assert.equal(real.length, 4);
  assert.equal(accuracy(real), 50);
  assert.ok(Math.abs(totalLost(real) - 1.89) < 1e-9);
  assert.equal(accuracy([]), 100);
});

test("fmtWhen", () => {
  const now = new Date(2026, 10, 18, 15, 0).getTime();
  const at = (y, m, d, h, mi) => new Date(y, m, d, h, mi).getTime() / 1000;
  assert.equal(fmtWhen(at(2026, 10, 18, 9, 5), now), "today 09:05");
  assert.equal(fmtWhen(at(2026, 10, 17, 23, 59), now), "yesterday 23:59");
  assert.equal(fmtWhen(at(2026, 10, 3, 12, 0), now), "3 Nov");
  assert.equal(fmtWhen(at(2025, 11, 31, 12, 0), now), "31 Dec 2025");
  assert.equal(fmtWhen(null, now), "");
});

test("sparkPoints stays inside the chart", () => {
  const pts = sparkPoints([40, 70, 100, 120, 10], 600, 150, 40, 100);
  assert.equal(pts.length, 5);
  for (const [x, y] of pts) assert.ok(x >= 12 && x <= 588 && y >= 12 && y <= 138);
  assert.ok(pts[0][1] > pts[1][1] && pts[1][1] > pts[2][1], "higher values sit higher up");
  assert.equal(pts[2][1], pts[3][1], "values above the range are clamped");
  assert.deepEqual(sparkPoints([70], 600, 150, 40, 100)[0][0], 300);
});

test("boardBox frames any board", () => {
  const box = boardBox([{ x: -260, y: -240 }, { x: 260, y: 240 }, { x: 0, y: 0 }]);
  assert.deepEqual([box.x, box.y, box.w, box.h], [-300, -294, 600, 588]);
  assert.equal(box.cx, 0);
  const off = boardBox([{ x: -364, y: -300 }, { x: 260, y: 300 }]);
  assert.equal(off.cx, -52, "an off-centre board (the large one) is centred on itself");
});

test("plentyPicks completes the best build it can", () => {
  const bank = [19, 19, 19, 19, 19];
  assert.deepEqual(plentyPicks([0, 0, 0, 2, 1], bank, COSTS, ORDER), [4, 4]);          // two ore for a city
  assert.deepEqual(plentyPicks([1, 1, 0, 0, 0], bank, COSTS, ORDER), [2, 3]);          // sheep + wheat: settlement
  assert.deepEqual(plentyPicks([0, 0, 0, 2, 1], [19, 19, 19, 19, 1], COSTS, ORDER).length, 2);
  assert.notDeepEqual(plentyPicks([0, 0, 0, 2, 1], [19, 19, 19, 19, 1], COSTS, ORDER), [4, 4], "the bank has one ore");
  const picks = plentyPicks([3, 3, 3, 3, 3], [0, 0, 5, 0, 0], COSTS, ORDER);
  assert.deepEqual(picks, [2, 2], "only sheep left in the bank");
});

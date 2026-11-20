"""Web backend: a scripted human plays full games through the HTTP API."""

import json
import random
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from hexfield.ai import AIPlayer
from hexfield.game import OVER
from hexfield.web import server
from hexfield.match import public_reason
from hexfield import store
from hexfield.web.session import Replay, WebGame


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/"

    def call(self, route, body=None, seat=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if seat is not None:
            headers["Authorization"] = f"Bearer {seat}"
        req = urllib.request.Request(self.base + route, data=data, method="POST" if body is not None else "GET",
                                     headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            with ex:
                return {"status": ex.code, **json.loads(ex.read())}


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.httpd.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def play(self, seed, opponents):
        c = self.c
        st = c.call("new", {"opponents": opponents, "level": "medium", "seed": seed})
        self.assertIn("board", st)
        self.assertEqual(len(st["board"]["vertices"]), 54)
        game = server.APP.game
        pilot = AIPlayer(game.s.board, "medium", seed=seed)
        rnd = random.Random(seed)
        undos = 0
        for _ in range(6000):
            if st["phase"] == OVER:
                break
            if st.get("pending_offer"):
                st = c.call("respond", {"accept": rnd.random() < 0.5})
                continue
            if st["actor"] != st["human"]:
                st = c.call("step", {})
                continue
            x = rnd.random()
            if x < 0.02:
                self.assertIn("options", c.call("hint")["hint"])
                continue
            if x < 0.03 and undos < 5 and st["can_undo"]:
                undos += 1
                st = c.call("undo", {})
                continue
            if x < 0.04 and st["phase"] == "main" and st["hand"][0]:
                res = c.call("offer", {"give": [1, 0, 0, 0, 0], "get": [0, 0, 0, 0, 1]})
                if res.get("my_offer", {}).get("accepted"):
                    q = res["my_offer"]["accepted"][0]
                    st = c.call("act", {"action": ["trade", q, [1, 0, 0, 0, 0], [0, 0, 0, 0, 1]]})
                    self.assertNotIn("error", st)
                continue
            # this player's own hand is the real one; opponents are beliefs (view)
            a, _ = pilot.decide(game.s)
            if a[0] == "propose":
                a = ("end",)
            if a[0] == "macro":
                a = a[1][0]
            st = c.call("act", {"action": a})
            self.assertNotIn("error", st, (a, st.get("error")))
        self.assertEqual(st["phase"], OVER)
        self.assertIn("summary", st)
        # opponents' hands never reach the browser
        self.assertNotIn("hands", st)
        game._jobs.join()
        self.assertTrue(c.call("state")["reviews"])

    def test_full_games(self):
        self.play(3, 2)
        self.play(4, 3)

    def test_illegal_and_hidden(self):
        st = self.c.call("new", {"opponents": 2, "seed": 9, "seat": 1})
        self.assertEqual(st["human"], 0)
        bad = self.c.call("act", {"action": ["town", 0]})
        self.assertEqual(bad["status"], 400)
        for p in st["players"]:
            self.assertNotIn("hand", p)

    def test_malformed_input_is_rejected(self):
        c = self.c
        st = c.call("new", {"opponents": 2, "seed": 5, "seat": 1})
        for action in (["end", 1], ["settle"], ["discard"], ["trade", 1], ["trail", "x"],
                       ["discard", 5], ["discard", [0, 0, 0, 0, 0, 4]], ["roll", 7], "x", None):
            r = c.call("act", {"action": action})
            self.assertEqual(r.get("status"), 400, action)
        for give, get in (([1], [0, 0, 0, 0, 1]), (5, 5), ([1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1])):
            self.assertEqual(c.call("offer", {"give": give, "get": get}).get("status"), 400)
        self.assertEqual(c.call("new", {"opponents": "x"}).get("status"), 400)
        self.assertEqual(c.call("state?since=abc").get("status"), 400)
        self.assertEqual(st["phase"], "setup_village")

    def test_discard_needs_five_counts(self):
        g = WebGame(2, "easy", seed=11, seat=1)
        g.s.phase = "discard"
        g.s.hands[0] = [2, 2, 2, 2, 0]
        g.s.discards = {0: 4}
        with self.assertRaises(Exception):
            g.act(["discard", [0, 0, 0, 0, 0, 4]])
        g.act(["discard", [1, 1, 1, 1, 0]])
        self.assertEqual(g.s.hands[0], [1, 1, 1, 1, 0])

    def test_undo_keeps_earlier_reviews(self):
        g = WebGame(2, "easy", seed=12, seat=1)
        for _ in range(4):  # both setup rounds for the human
            while g.s.actor() != g.human:
                g.step()
            g.act(list(g.s.legal_actions()[0]))
        g._jobs.join()
        n = len(g.reviews)
        self.assertEqual(n, 4)
        g.undo()
        g._jobs.join()
        self.assertEqual(len(g.reviews), 3)
        g.close()

    def test_bot_answers_and_plans_stay_hidden(self):
        g = WebGame(2, "easy", seed=13, seat=1)
        g.offer = {"from": 1, "give": (1, 0, 0, 0, 0), "get": (0, 1, 0, 0, 0), "answers": {0: None, 2: True},
                   "counters": {}, "tries": {}, "notes": {}, "bot": True}
        self.assertNotIn("answers", g.view()["pending_offer"])
        self.assertEqual(public_reason("plan: buy venture -> play Ranger  (eval +0.40)"),
                         "plan: buy venture  (eval +0.40)")

    def test_playtest_view_details(self):
        g = WebGame(3, "medium", seed=31, seat=1, name="Tester")
        while g.s.phase.startswith("setup"):
            if g.s.actor() == g.human:
                g.act(list(g.s.legal_actions()[0]))
            else:
                g.step()
        v = g.view()
        self.assertEqual(v["round"], 1)
        self.assertEqual(len(v["tracker"]), 5)
        msgs = [e["msg"] for e in v["events"]]
        self.assertFalse([m for m in msgs if "Tester" in m], "the human reads the log in second person")
        self.assertTrue(any(m.startswith("You build") for m in msgs))
        # a hand that needs one bank trade before a town: Play covers trade and build
        g.s.phase, g.s.rolled = "main", True
        g.s.hands[0] = [4, 1, 0, 2, 2]
        opts = g.hint()["options"]
        texts = [o["text"] for o in opts]
        self.assertEqual(len(texts), len(set(texts)))
        multi = [o for o in opts if len(o["steps"]) > 1]
        self.assertTrue(multi)
        self.assertEqual(multi[0]["steps"][0][0], "bank")
        self.assertIn(multi[0]["steps"][-1][0], ("town", "settle", "trail", "buy_venture"))
        # offers come back with a reason for every refusal
        g.s.hands[1] = [0, 0, 0, 0, 0]
        replies = g.make_offer([1, 0, 0, 0, 0], [0, 0, 0, 0, 1])["replies"]
        self.assertTrue(all(r["why"] for r in replies if not r["ok"]))
        self.assertEqual(next(r for r in replies if r["p"] == 1)["why"], "not interested")
        # undo names the decision it took back
        g.act(["end"])
        self.assertEqual(g.undo(), "end turn")
        g.close()

    def test_geometry(self):
        g = WebGame(2, "easy", seed=1, seat=1)
        geo = g.geometry
        # adjacent vertices are one hex-radius apart
        for a, b in geo["edges"]:
            va, vb = geo["vertices"][a], geo["vertices"][b]
            d = ((va["x"] - vb["x"]) ** 2 + (va["y"] - vb["y"]) ** 2) ** 0.5
            self.assertAlmostEqual(d, geo["radius"], delta=0.1)


def play_until(g, stop, limit=4000):
    """Drive a WebGame with a simple pilot (every human seat) until stop(g) is true."""
    rnd = random.Random(g.seed)
    for _ in range(limit):
        if stop(g) or g.s.phase == OVER:
            return
        waiting = g.waiting_on()
        if g.offer and any(g.offer["answers"].get(h, 0) is None for h in g.humans):
            h = next(h for h in g.humans if g.offer["answers"].get(h, 0) is None)
            g.respond(rnd.random() < 0.5, h)
            continue
        if not waiting:
            g.step()
            continue
        h = waiting[0]
        s = g.s
        if s.phase == "discard":
            k, c = s.discards[h], [0] * 5
            for r in range(5):
                t = min(k, s.hands[h][r])
                c[r], k = t, k - t
            g.act(["discard", c], h)
            continue
        acts = s.legal_actions()
        a = ("end",) if ("end",) in acts and rnd.random() < 0.5 else rnd.choice(acts)
        g.act(list(a), h)
    raise AssertionError("game did not get there")


class SessionTests(unittest.TestCase):
    def test_a_saved_game_resumes_with_its_undo_history(self):
        g = WebGame(2, "easy", seed=21, seat=1)
        play_until(g, lambda g: g.s.turn >= 6)
        g._jobs.join()
        g.save()
        before = g.view()
        r = WebGame.resume(g.gid)
        after = r.view()
        for key in ("phase", "turn", "hand", "v_owner", "e_owner", "bank", "bandit", "ev_total", "can_undo"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual([e["msg"] for e in before["events"]], [e["msg"] for e in after["events"]])
        self.assertEqual([e.get("why") for e in before["events"]], [e.get("why") for e in after["events"]],
                         "the bots' reasoning is part of the save")
        self.assertEqual(len(before["reviews"]), len(after["reviews"]))
        self.assertEqual(len(r.history), len(g.history))
        # the seat key still works, and undo steps back through decisions made before the save
        self.assertEqual(r.tokens, g.tokens)
        entries = len(r.rec.entries)
        r.undo()
        self.assertLess(len(r.rec.entries), entries)
        self.assertEqual(r.s.actor(), r.human)
        for x in (g, r):
            x.close()

    def test_play_on_from_any_point(self):
        g = WebGame(2, "easy", seed=22, seat=1)
        play_until(g, lambda g: g.s.turn >= 5)
        g.save()
        cut = g.rec.steps()[len(g.rec.steps()) // 2]
        b = WebGame.resume(g.gid, upto=cut)
        self.assertNotEqual(b.gid, g.gid)
        self.assertEqual(len(b.rec.entries), cut)
        self.assertEqual(b.rec.meta["branched_from"], g.gid)
        play_until(b, lambda b: b.s.turn >= g.s.turn + 1)   # it is a live game
        self.assertEqual(len(store.load(g.gid)[0].entries), len(g.rec.entries), "the original is untouched")
        for x in (g, b):
            x.close()

    def test_replay_steps_both_ways(self):
        g = WebGame(2, "easy", seed=23, seat=1)
        play_until(g, lambda g: g.s.turn >= 6)
        g._jobs.join()
        g.save()
        rp = Replay(g.gid)
        steps = g.rec.steps()
        self.assertEqual(rp.total, len(steps))

        def sig(s):
            return (s.phase, s.turn, s.v_owner, s.e_owner, s.hands, s.bandit)

        for k in (0, 1, 7, rp.total, 3, rp.total - 1, 45, 2):   # forward, back, forward again
            k = min(k, rp.total)
            rp.seek(k)
            # position k: the first k actions, and the notes written before the next one
            cut = steps[k] if k < len(steps) else len(g.rec.entries)
            self.assertEqual(sig(rp.s), sig(g.rec.replay(cut)), k)
        v = rp.view()
        self.assertEqual(v["legal"], {}, "a replay can't be played")
        self.assertFalse(v["can_undo"])
        marks = v["replay"]["marks"]
        self.assertTrue(marks and all(0 <= m["k"] <= rp.total for m in marks))
        self.assertTrue(any(m["verdict"] for m in marks), "decisions carry the coach's verdict")
        # hidden cards stay hidden unless asked for
        self.assertNotIn("hand", v["players"][1])
        self.assertEqual(rp.view(reveal=True)["players"][1]["hand"], rp.s.hands[1])
        # a hint is available where the player had a decision
        rp.seek(marks[0]["k"])
        self.assertTrue(rp.hint_here()["options"])
        g.close()

    def test_pass_and_play_keeps_hands_apart(self):
        g = WebGame(1, "easy", seed=24, humans=["Ann", "Bo"], mode="hotseat")
        self.assertEqual(len(g.humans), 2)
        self.assertEqual(len(g.tokens), 2)
        a, b = g.humans
        play_until(g, lambda g: g.s.turn >= 3)
        va, vb = g.view(a), g.view(b)
        self.assertEqual(va["hand"], g.s.hands[a])
        self.assertEqual(vb["hand"], g.s.hands[b])
        self.assertEqual((va["human"], vb["human"]), (a, b))
        for v in (va, vb):
            self.assertTrue(all("hand" not in pl for pl in v["players"]))
        # each sees the log from their own chair, and nobody sees the other's private lines
        self.assertTrue(any(e["msg"].startswith("You ") for e in va["events"]))
        self.assertFalse(any("Ann" in e["msg"] for e in va["events"]))
        self.assertTrue(any("Ann" in e["msg"] for e in vb["events"]))
        # only the player whose turn it is may move
        actor = g.s.actor()
        if actor in g.humans:
            other = b if actor == a else a
            with self.assertRaises(Exception):
                g.act(list(g.s.legal_actions()[0]), other)
        g.close()

    def test_pass_and_play_takes_back_your_own_last_decision(self):
        g = WebGame(1, "easy", seed=24, humans=["Ann", "Bo"], mode="hotseat")
        a, b = g.humans
        play_until(g, lambda g: g.s.turn >= 3 and g.s.phase == "main" and g.s.actor() in g.humans)
        me = g.s.actor()
        other = b if me == a else a
        before = (g.s.turn, g.s.phase, [list(h) for h in g.s.hands], len(g.s.events))
        g.act(["end"], me)
        self.assertTrue(g.view(me)["can_undo"], "your last decision, and nobody has decided since")
        self.assertFalse(g.view(other)["can_undo"], "not somebody else's decision")
        with self.assertRaises(Exception):
            g.undo(other)
        self.assertEqual(g.undo(me), "end turn")   # one screen: no vote
        self.assertIsNone(g.takeback)
        self.assertEqual((g.s.turn, g.s.phase, [list(h) for h in g.s.hands], len(g.s.events)), before)
        self.assertEqual(g.view(me)["epoch"], 1)
        g.close()

    def test_online_take_back_needs_everyone(self):
        g = WebGame(1, "easy", seed=26, humans=["Ann", "Bo", "Cy"], mode="online")
        g.open.clear()
        a, b, c = g.humans
        play_until(g, lambda g: g.s.turn >= 4 and g.s.phase == "main" and g.s.actor() in g.humans)
        me = g.s.actor()
        x, y = [q for q in g.humans if q != me]
        before = (g.s.turn, g.s.phase, len(g.s.events))
        g.act(["end"], me)
        after = (g.s.turn, g.s.phase, len(g.s.events))
        # asking changes nothing yet, and nothing else moves meanwhile
        self.assertIsNone(g.undo(me))
        self.assertEqual((g.s.turn, g.s.phase, len(g.s.events)), after)
        self.assertEqual(g.waiting_on(), sorted([x, y]))
        self.assertTrue(g.view(x)["takeback"]["mine"])
        self.assertFalse(g.view(me)["takeback"]["mine"])
        self.assertEqual(g.view(x)["takeback"]["from"], me)
        self.assertFalse(g.view(me)["can_undo"], "one request at a time")
        v = g.version
        g.step()
        self.assertEqual(g.version, v, "the bots wait for the answer")
        actor = g.s.actor()
        if actor in g.humans:
            with self.assertRaises(Exception):
                g.act(list(g.s.legal_actions()[0]), actor)
        with self.assertRaises(Exception):
            g.answer_takeback(True, me)            # you can't allow your own request
        # one refusal ends it: the move stands
        g.answer_takeback(True, x)
        self.assertEqual(g.waiting_on(), [y])
        with self.assertRaises(Exception):
            g.answer_takeback(True, x)             # one answer each
        g.answer_takeback(False, y)
        self.assertIsNone(g.takeback)
        self.assertEqual((g.s.turn, g.s.phase, len(g.s.events)), after)
        self.assertEqual(g.view(me)["takeback_result"]["ok"], False)
        self.assertEqual(g.view(me)["takeback_result"]["by"], y)
        # one request per decision: nobody can hold the game up by asking over and over
        self.assertFalse(g.view(me)["can_undo"])
        with self.assertRaises(Exception):
            g.undo(me)
        # the next decision, allowed by all: back to before it
        play_until(g, lambda g: g.s.phase == "main" and g.s.actor() in g.humans)
        me = g.s.actor()
        x, y = [q for q in g.humans if q != me]
        before = (g.s.turn, g.s.phase, len(g.s.events))
        g.act(["end"], me)
        self.assertTrue(g.view(me)["can_undo"])
        g.undo(me)
        g.answer_takeback(True, x)
        g.answer_takeback(True, y)
        self.assertIsNone(g.takeback)
        self.assertEqual((g.s.turn, g.s.phase, len(g.s.events)), before)
        self.assertTrue(g.view(x)["takeback_result"]["ok"])
        self.assertEqual(g.s.actor(), me)
        # the asker may withdraw (and that was their one request for this decision)
        g.act(["end"], me)
        self.assertTrue(g.view(me)["can_undo"])
        g.undo(me)
        g.answer_takeback(False, me)
        self.assertIsNone(g.takeback)
        self.assertEqual(g.view(x)["takeback_result"]["by"], me)
        self.assertFalse(g.view(me)["can_undo"])
        g.close()

    def test_muting_and_a_pending_take_back_survive_a_restart(self):
        g = WebGame(1, "easy", seed=26, humans=["Ann", "Bo"], mode="online")
        g.open.clear()
        play_until(g, lambda g: g.s.turn >= 4 and g.s.phase == "main" and g.s.actor() in g.humans)
        me = g.s.actor()
        other = next(q for q in g.humans if q != me)
        g.mute_offers(other, True)
        g.act(["end"], me)
        g.undo(me)
        g.close()
        r = WebGame.resume(g.gid)
        self.assertTrue(r.view(other)["mute_offers"])
        self.assertFalse(r.view(me)["mute_offers"])
        self.assertEqual(r.view(other)["takeback"]["from"], me)
        self.assertTrue(r.view(other)["takeback"]["mine"])
        r.answer_takeback(True, other)
        self.assertIsNone(r.takeback)
        self.assertEqual(r.s.actor(), me)
        r.close()

    def test_offers_between_humans(self):
        g = WebGame(1, "easy", seed=25, humans=["Ann", "Bo"], mode="hotseat")
        a, b = g.humans
        g.s.phase, g.s.current, g.s.turn = "main", a, 3
        g.s.hands[a] = [2, 0, 0, 0, 0]
        g.s.hands[b] = [0, 0, 0, 0, 2]
        mine = g.make_offer([1, 0, 0, 0, 0], [0, 0, 0, 0, 1], a)
        reply = next(r for r in mine["replies"] if r["p"] == b)
        self.assertIsNone(reply["ok"], "the other human hasn't answered yet")
        self.assertEqual(g.pending_for(b)["from"], a)
        self.assertIn(b, g.waiting_on())
        self.assertIn(a, g.waiting_on(), "the proposer isn't blocked while the offer is out")
        with self.assertRaises(Exception):
            g.act(["trade", b, [1, 0, 0, 0, 0], [0, 0, 0, 0, 1]], a)   # not agreed yet
        # Bo answers with a counter-offer: two timber for the stone
        g.respond(False, b, counter={"give": [2, 0, 0, 0, 0], "get": [0, 0, 0, 0, 1]})
        reply = next(r for r in g.my_offer_for(a)["replies"] if r["p"] == b)
        self.assertEqual(reply["counter"], {"give": [2, 0, 0, 0, 0], "get": [0, 0, 0, 0, 1]})
        g.act(["trade", b, [2, 0, 0, 0, 0], [0, 0, 0, 0, 1]], a)       # Ann takes the counter
        self.assertEqual((g.s.hands[a], g.s.hands[b]), ([0, 0, 0, 0, 1], [2, 0, 0, 0, 1]))
        self.assertIsNone(g.offer)
        g.close()

    def test_an_unanswered_offer_lapses_online(self):
        import time
        from hexfield.web.session import OFFER_SECONDS
        for mode, lapses in (("online", True), ("hotseat", False)):
            g = WebGame(1, "easy", seed=25, humans=["Ann", "Bo"], mode=mode, persist=False)
            a, b = g.humans
            g.open = set()
            g.s.phase, g.s.current, g.s.turn = "main", a, 3
            g.s.hands[a] = [2, 0, 0, 0, 0]
            g.s.hands[b] = [0, 0, 0, 0, 2]
            g.make_offer([1, 0, 0, 0, 0], [0, 0, 0, 0, 1], a)
            self.assertFalse(g.expire_offer(), "the offer has only just been made")
            self.assertEqual(g.expire_offer(time.monotonic() + OFFER_SECONDS + 1), lapses, mode)
            reply = next(r for r in g.my_offer_for(a)["replies"] if r["p"] == b)
            self.assertEqual(reply["ok"], False if lapses else None)
            self.assertEqual(b in g.waiting_on(), not lapses)
            g.close()

    def test_muting_bot_offers(self):
        g = WebGame(2, "easy", seed=27)
        h = g.human
        bot = next(p for p in range(g.s.n) if p != h)
        g.s.phase, g.s.current, g.s.turn = "main", bot, 5
        g.s.hands = [[2, 2, 2, 2, 2] for _ in range(g.s.n)]
        # an offer already on the table is declined when you mute
        g._bot_propose(bot, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1), "offers")
        self.assertEqual(g.pending_for(h)["from"], bot)
        marks, n0 = len(g.history), len(g.s.events)
        g.mute_offers(h, True)
        self.assertIsNone(g.offer)
        # ... without it counting as a decision of yours: nothing to undo, review or read
        self.assertEqual(len(g.history), marks)
        self.assertFalse(any(e["msg"].startswith("You ") for e in g.view(h, since=n0)["events"]))
        g._jobs.join()
        self.assertEqual(g.reviews, [])
        self.assertTrue(g.view(h)["mute_offers"])
        # from then on the bots don't ask you, and the game never waits for your answer
        g.s.hands = [[2, 2, 2, 2, 2] for _ in range(g.s.n)]
        n0 = len(g.s.events)
        g._bot_propose(bot, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1), "offers")
        self.assertIsNone(g.offer)
        self.assertIsNone(g.pending_for(h))
        self.assertFalse(any(e["msg"].startswith("You ") for e in g.view(h, since=n0)["events"]), "no answer in your name")
        # your own offers still go out, and unmuting brings theirs back
        g.s.current = h
        self.assertTrue(g.make_offer([1, 0, 0, 0, 0], [0, 0, 0, 0, 1], h)["replies"])
        g.offer = None
        g.mute_offers(h, False)
        g.s.current = bot
        g.s.hands = [[2, 2, 2, 2, 2] for _ in range(g.s.n)]
        g._bot_propose(bot, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1), "offers")
        self.assertEqual(g.pending_for(h)["from"], bot)
        g.close()

    def test_a_redone_move_does_not_get_the_old_moves_review(self):
        g = WebGame(2, "easy", seed=27)
        g.snapshot("first try", g.human)
        old = g.history[-1]["id"]
        g._undo()
        g.snapshot("second try", g.human)
        self.assertEqual(g.history[-1]["at"], len(g.rec.entries) - 1)
        self.assertFalse(g._live(old), "same place in the record, a different decision")
        self.assertTrue(g._live(g.history[-1]["id"]))
        g.close()

    def test_an_undone_decision_is_worded_for_whoever_reads_it(self):
        g = WebGame(1, "easy", seed=27, humans=["Ann", "Bo"], mode="online")
        ann, bo = g.humans
        g.snapshot("accept Ann's offer", bo)
        g.takeback = {"from": bo, "label": "accept Ann's offer", "answers": {ann: None}}
        self.assertEqual(g.view(ann)["takeback"]["label"], "accept your offer")
        g.answer_takeback(True, ann)
        self.assertEqual(g.view(ann)["takeback_result"]["label"], "accept your offer")
        self.assertEqual(g.view(bo)["takeback_result"]["label"], "accept Ann's offer")
        g.close()

    def test_countering_a_bots_offer(self):
        g = WebGame(2, "hard", seed=26, seat=1)
        h, bot = 0, 1
        g.s.phase, g.s.current, g.s.turn = "main", bot, 3
        g.s.hands[h] = [0, 0, 0, 0, 3]
        g.s.hands[bot] = [4, 0, 0, 0, 0]
        g._bot_propose(bot, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1), "offers")
        po = g.view()["pending_offer"]
        self.assertEqual(po["counters_left"], 2)
        for bad in ({"give": [1, 0, 0, 0, 0], "get": [0, 0, 0, 0, 1]},      # the same offer
                    {"give": [1, 0, 0, 0, 0], "get": [0, 0, 0, 0, 9]},      # cards you don't have
                    {"give": [1, 0, 0, 0, 0], "get": [1, 0, 0, 0, 0]},      # the same card both ways
                    {"give": [0] * 5, "get": [0, 0, 0, 0, 1]}, "x", {"give": [1], "get": [1]}):
            with self.assertRaises(Exception):
                g.respond(False, counter=bad)
        self.assertIsNotNone(g.offer, "a refused request leaves the offer on the table")
        # an absurd counter is turned down, and costs one of the two tries
        g.respond(False, counter={"give": [4, 0, 0, 0, 0], "get": [0, 0, 0, 0, 1]})
        po = g.view()["pending_offer"]
        if po:   # the bot said no
            self.assertEqual(po["counters_left"], 1)
            self.assertIn("turned", po["note"])
            self.assertEqual(g.s.hands[h], [0, 0, 0, 0, 3])
            g.respond(True)
            self.assertEqual(g.s.hands[h], [1, 0, 0, 0, 2])
        else:    # it took four timber for one stone
            self.assertEqual(g.s.hands[h], [4, 0, 0, 0, 2])
        self.assertIsNone(g.offer)
        g.close()

    def test_bots_name_their_price(self):
        hits = 0
        for seed in range(30, 42):
            g = WebGame(2, "hard", seed=seed, seat=1, persist=False)
            play_until(g, lambda g: g.s.turn >= 1 and g.s.actor() == g.human and g.s.phase == "main", 300)
            if g.s.phase != "main":
                g.close()
                continue
            g.s.hands[0] = [3, 3, 2, 0, 0]
            for q in (1, 2):
                g.s.hands[q] = [0, 0, 0, 3, 3]
            for viewer in range(3):
                for q in range(3):
                    g.s.belief[viewer][q] = [float(x) for x in g.s.hands[q]]
            for r in g.make_offer([1, 0, 0, 0, 0], [0, 0, 0, 0, 1])["replies"]:
                c = r["counter"]
                if not c or not all(n <= g.s.hands[0][i] for i, n in enumerate(c["give"])):
                    continue
                hits += 1
                self.assertFalse(r["ok"])
                g.act(["trade", r["p"], c["give"], c["get"]])   # trading on the bot's terms is legal
                self.assertEqual(g.s.hands[0][4], c["get"][4])
                break
            g.close()
        self.assertGreater(hits, 0, "no bot ever made a counter-offer")

    def test_six_players_with_the_special_building_phase(self):
        g = WebGame(5, "easy", seed=27, seat=1, persist=False)
        seen = set()

        def stop(g):
            seen.add(g.s.phase)
            return g.s.turn >= 14
        play_until(g, stop)
        v = g.view()
        self.assertEqual((v["n"], v["large"], v["special_build"]), (6, True, True))
        self.assertEqual(len(g.geometry["hexes"]), 30)
        self.assertEqual(len({h["label"] for h in g.geometry["hexes"]}), 30)
        self.assertIn("special_build", seen)
        self.assertTrue(all(pl["style"] for pl in v["players"] if pl["bot"]))
        g.close()


class OnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.c = Client(cls.httpd.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_lobby_join_and_seat_keys(self):
        c = self.c
        st = c.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 51, "name": "Host"})
        gid = st["gid"]
        self.assertTrue(st["lobby"])
        (host_seat, host_key), = st["tokens"].items()
        self.assertEqual(sum(pl["open"] for pl in st["players"]), 1)
        # nothing moves until everyone is in
        self.assertEqual(c.call(f"act?g={gid}", {"action": ["end"]}, seat=host_key)["status"], 400)
        c.call(f"step?g={gid}", {}, seat=host_key)
        self.assertEqual(c.call(f"state?g={gid}", seat=host_key)["ev_total"], 0)
        # a visitor without a key sees the lobby only: no board state, no hands
        peek = c.call(f"state?g={gid}")
        self.assertTrue(peek["join"])
        self.assertNotIn("hand", peek)
        self.assertEqual(c.call(f"state?g={gid}", seat="wrong")["status"], 403)
        joined = c.call(f"join?g={gid}", {"name": "Guest"})
        (guest_seat, guest_key), = joined["tokens"].items()
        self.assertNotEqual(guest_seat, host_seat)
        self.assertFalse(joined["lobby"])
        self.assertIn("board", joined)
        self.assertEqual(joined["players"][int(guest_seat)]["name"], "Guest")
        self.assertEqual(c.call(f"join?g={gid}", {"name": "Late"})["status"], 400)
        # each key acts for its own seat and sees its own hand
        host = c.call(f"state?g={gid}", seat=host_key)
        guest = c.call(f"state?g={gid}", seat=guest_key)
        self.assertEqual((host["human"], guest["human"]), (int(host_seat), int(guest_seat)))
        self.assertFalse(host["can_undo"])
        self.assertEqual(c.call(f"undo?g={gid}", {}, seat=host_key)["status"], 400)
        # the right player can move; the other can't move for them
        for _ in range(40):
            now = c.call(f"state?g={gid}", seat=host_key)
            if now["waiting_on"]:
                break
            c.call(f"step?g={gid}", {"v": now["version"]}, seat=host_key)
        seat = now["waiting_on"][0]
        key, other = (host_key, guest_key) if seat == int(host_seat) else (guest_key, host_key)
        mine = c.call(f"state?g={gid}", seat=key)
        spot = mine["legal"]["settle"][0]
        self.assertEqual(c.call(f"act?g={gid}", {"action": ["settle", spot]}, seat=other)["status"], 400)
        done = c.call(f"act?g={gid}", {"action": ["settle", spot]}, seat=key)
        self.assertNotIn("error", done)
        self.assertEqual(done["v_owner"][spot], seat)

    def test_take_back_over_http_and_the_log_after_an_undo(self):
        c = self.c
        st = c.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 54, "name": "Host"})
        gid, (host_seat, host_key) = st["gid"], next(iter(st["tokens"].items()))
        (guest_seat, guest_key), = c.call(f"join?g={gid}", {"name": "Guest"})["tokens"].items()
        keys = {int(host_seat): host_key, int(guest_seat): guest_key}
        game = server.APP.games[gid]
        play_until(game, lambda g: g.s.turn >= 3 and g.s.phase == "main" and g.s.actor() in g.humans)
        me = game.s.actor()
        other = next(q for q in keys if q != me)
        n0 = c.call(f"state?g={gid}", seat=keys[me])["ev_total"]
        c.call(f"act?g={gid}", {"action": ["end"]}, seat=keys[me])
        asked = c.call(f"undo?g={gid}", {}, seat=keys[me])
        self.assertIsNone(asked["undone"])
        self.assertEqual(asked["takeback"]["waiting"], [other])
        self.assertEqual(c.call(f"takeback?g={gid}", {"ok": True}, seat=keys[me])["status"], 400)
        done = c.call(f"takeback?g={gid}&since={n0}&ep=0", {"ok": True}, seat=keys[other])
        self.assertIsNone(done["takeback"])
        self.assertTrue(done["takeback_result"]["ok"])
        self.assertEqual((done["epoch"], done["ev_total"]), (1, n0))
        # a browser that still holds the log from before the undo gets all of it again
        self.assertTrue(done["reset_log"])
        self.assertEqual(done["events"][0]["i"], 0)
        fresh = c.call(f"state?g={gid}&since={n0}&ep=1", seat=keys[me])
        self.assertNotIn("reset_log", fresh)
        self.assertEqual(fresh["events"], [])

    def test_stale_steps_do_nothing_and_waiting_returns(self):
        c = self.c
        st = c.call("new", {"opponents": 2, "seed": 52, "seat": 3, "level": "easy"})
        v = st["version"]
        a = c.call("step", {"v": v})
        self.assertGreater(a["version"], v)
        b = c.call("step", {"v": v})            # a second browser, a moment late
        self.assertEqual(b["version"], a["version"], "a stale step must not move the game")
        t0 = time.time()
        w = c.call(f"wait?v={v}")               # already past v: answers at once
        self.assertLess(time.time() - t0, 2)
        self.assertEqual(w["version"], a["version"])

    def test_start_with_bots_and_host_routes(self):
        c = self.c
        st = c.call("new", {"mode": "online", "humans": 3, "opponents": 0, "seed": 53})
        gid, (seat, key) = st["gid"], next(iter(st["tokens"].items()))
        started = c.call(f"start?g={gid}", {}, seat=key)
        self.assertFalse(started["lobby"])
        self.assertEqual(sum(pl["bot"] for pl in started["players"]), 2)
        # saved games, export, import, replay, delete
        game = server.APP.games[gid]
        play_until(game, lambda g: g.s.turn >= 2)
        game.save()
        self.assertIn(gid, [g["id"] for g in c.call("games")["games"]])
        raw = c.call(f"export?id={gid}")
        self.assertEqual(raw["seed"], 53)
        new_id = c.call("import", {"record": raw})["id"]
        rp = c.call(f"replay?id={new_id}&k=5")
        self.assertEqual(rp["replay"]["k"], 5)
        self.assertIn("board", rp)
        self.assertEqual(c.call("import", {"record": {"version": 1}})["status"], 400)
        self.assertEqual(c.call("replay?id=nope")["status"], 400)
        self.assertIn("games", c.call("stats"))
        branch = c.call("branch", {"id": new_id, "entry": 6})
        self.assertNotIn(branch["gid"], (gid, new_id))
        self.assertTrue(c.call("delete", {"id": new_id})["deleted"])
        self.assertNotIn(new_id, [g["id"] for g in c.call("games")["games"]])

    def test_only_json_posts_are_accepted(self):
        # a form post from another web page must not be able to start or move a game
        req = urllib.request.Request(self.c.base + "new", data=b"opponents=2", method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()




class HardeningTests(unittest.TestCase):
    """What stands between the server and a network it doesn't trust."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def tearDown(self):
        server.APP.set_host_key(None)
        server.APP.buckets.clear()
        server.APP.hosts.discard("play.example.com")

    def req(self, path, body=None, method=None, **headers):
        """-> (status, headers, body bytes); redirects are not followed."""
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        c.request(method or ("POST" if data is not None else "GET"), path, body=data,
                  headers={k.replace("_", "-"): v for k, v in headers.items()})
        r = c.getresponse()
        out = r.status, r.headers, r.read()
        c.close()
        return out

    def test_security_headers_on_pages_and_api(self):
        for path in ("/", "/api/games", "/nope"):
            _, h, _ = self.req(path)
            self.assertIn("script-src 'self'", h["Content-Security-Policy"])
            self.assertEqual(h["Referrer-Policy"], "no-referrer")  # game ids are in the URL
            self.assertEqual(h["X-Frame-Options"], "DENY")
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
            self.assertNotIn("Python", h["Server"])

    def test_unknown_host_names_are_refused(self):
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1
        self.assertEqual(self.req("/api/games", Host="evil.example:8765")[0], 403)
        self.assertEqual(self.req("/", Host="evil.example")[0], 403)
        for ok in ("localhost:8765", "127.0.0.1", "[::1]:8765", "192.168.1.20:8765", "den.local:8765"):
            self.assertEqual(self.req("/healthz", Host=ok)[0], 200, ok)
        server.APP.hosts.add("play.example.com")
        self.assertEqual(self.req("/healthz", Host="Play.Example.com")[0], 200)

    def test_cross_site_posts_are_refused(self):
        self.assertEqual(self.req("/api/games", {}, Origin="https://evil.example")[0], 403)
        self.assertEqual(self.req("/api/games", {}, Origin="null")[0], 403)
        self.assertNotEqual(self.req("/api/step", {}, Origin=f"http://127.0.0.1:{self.port}")[0], 403)

    def test_static_files_revalidate_and_compress(self):
        code, h, raw = self.req("/app.css")
        self.assertEqual((code, h["Cache-Control"]), (200, "no-cache"))
        self.assertIsNone(h["Content-Encoding"])
        code, h2, gz = self.req("/app.css", Accept_Encoding="gzip, br")
        self.assertEqual(h2["Content-Encoding"], "gzip")
        import gzip
        self.assertEqual(gzip.decompress(gz), raw)
        self.assertLess(len(gz), len(raw) // 2)
        code, _, body = self.req("/app.css", If_None_Match=h["ETag"])
        self.assertEqual((code, body), (304, b""))
        code, h3, body = self.req("/js/app.js", method="HEAD")
        self.assertEqual((code, body), (200, b""))
        self.assertGreater(int(h3["Content-Length"]), 1000)
        self.assertEqual(self.req("/../server.py")[0], 404)
        self.assertEqual(self.req("/%2e%2e/server.py")[0], 404)

    def test_health_check(self):
        code, _, body = self.req("/healthz")
        self.assertEqual((code, json.loads(body)["ok"]), (200, True))

    def test_a_proxied_request_is_not_the_host(self):
        self.assertEqual(self.req("/api/games")[0], 200)
        self.assertEqual(self.req("/api/games", X_Forwarded_For="203.0.113.9")[0], 403)
        self.assertEqual(self.req("/api/new", {"seed": 1}, X_Real_IP="203.0.113.9")[0], 403)

    def test_only_an_invite_link_finds_a_game(self):
        c = Client(self.port)
        gid = c.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 9})["gid"]
        try:
            away = {"X_Forwarded_For": "203.0.113.9"}
            self.assertEqual(json.loads(self.req("/api/board", **away)[2]), {"nogame": True})
            self.assertTrue(json.loads(self.req(f"/api/board?g={gid}", **away)[2])["join"])
        finally:
            c.call("delete", {"id": gid})

    def test_host_key_is_the_only_way_to_host_once_set(self):
        key = "k" * 24
        server.APP.set_host_key(key)
        self.assertEqual(self.req("/api/games")[0], 403)          # this machine is no longer special
        self.assertEqual(self.req("/api/host", {"key": "wrong"})[0], 403)
        self.assertEqual(self.req("/api/host", {"key": 7})[0], 403)
        code, h, _ = self.req("/api/host", {"key": key}, X_Forwarded_Proto="https")
        self.assertEqual(code, 200)
        cookie = h["Set-Cookie"]
        for flag in ("HttpOnly", "SameSite=Strict", "Secure"):
            self.assertIn(flag, cookie)
        self.assertNotIn(key, cookie)
        pair = cookie.split(";")[0]
        self.assertEqual(self.req("/api/games", Cookie=pair, X_Forwarded_For="203.0.113.9")[0], 200)
        self.assertEqual(self.req("/api/games", Cookie="hexfield_host=nope")[0], 403)
        # the key in a URL logs nobody in: a proxy would have written it to its access log
        code, h, _ = self.req(f"/?host={key}")
        self.assertEqual((code, h["Set-Cookie"]), (200, None))
        # a cross-site page can't log a visitor in (or out) as the host
        self.assertEqual(self.req("/api/host", {"key": key}, Origin="https://evil.example")[0], 403)
        server.APP.set_host_key(None)
        self.assertEqual(self.req("/api/host", {"key": key})[0], 403)   # no key set: nothing to log in to

    def test_the_seat_key_travels_in_a_header_never_the_url(self):
        c = Client(self.port)
        made = c.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 9})
        gid, key = made["gid"], next(iter(made["tokens"].values()))
        try:
            away = {"X_Forwarded_For": "203.0.113.9"}
            seated = json.loads(self.req(f"/api/state?g={gid}", Authorization=f"Bearer {key}", **away)[2])
            self.assertNotIn("join", seated)
            self.assertEqual(seated["human"], int(next(iter(made["tokens"]))))
            # the old query parameter is ignored: that request is a stranger at the lobby
            self.assertTrue(json.loads(self.req(f"/api/state?g={gid}&t={key}", **away)[2])["join"])
            self.assertEqual(self.req(f"/api/state?g={gid}", Authorization="Bearer wrong", **away)[0], 403)
            self.assertTrue(json.loads(self.req(f"/api/state?g={gid}", Authorization=f"Basic {key}", **away)[2])["join"])
        finally:
            c.call("delete", {"id": gid})

    def test_remote_addresses_are_rate_limited(self):
        self.assertEqual(self.req("/healthz", X_Forwarded_For="203.0.113.7")[0], 200)
        server.APP.buckets["203.0.113.7"] = [-60 * server.RATE, time.monotonic()]   # a minute in debt
        self.assertEqual(self.req("/healthz", X_Forwarded_For="203.0.113.7")[0], 429)
        self.assertEqual(self.req("/healthz", X_Forwarded_For="203.0.113.8")[0], 200)   # others unaffected
        self.assertEqual({self.req("/healthz")[0] for _ in range(20)}, {200})          # nor this machine
        app = server.App()
        self.assertTrue(all(app.allow("a", now=0.0) for _ in range(int(server.BURST))))
        self.assertFalse(app.allow("a", now=0.0))
        self.assertTrue(app.allow("a", now=1.0))                   # it refills

    def test_errors_hide_details_from_remote_callers(self):
        c = Client(self.port)
        gid = c.call("new", {"mode": "online", "humans": 2, "opponents": 1, "seed": 9})["gid"]
        g = server.APP.games[gid]
        boom = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail"))
        g.view, old = boom, g.view
        try:
            with self.assertLogs("hexfield.web", level="ERROR"):
                code, _, body = self.req(f"/api/join?g={gid}", {"name": "Eve"}, X_Forwarded_For="203.0.113.9")
            self.assertEqual(code, 500)
            self.assertNotIn(b"secret", body)
        finally:
            g.view = old
            c.call("delete", {"id": gid})

    def test_bad_requests_get_a_clean_answer(self):
        self.assertEqual(self.req("/api/act", method="POST", Content_Length="-5")[0], 400)
        self.assertEqual(self.req("/api/state", method="HEAD")[0], 405)
        self.assertEqual(self.req("/api/act", method="POST", Content_Type="text/plain", Content_Length="2")[0], 400)

    def test_command_line_guards(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            server.main(["--host-key", "short", "--no-browser", "--no-save"])
        self.assertIn("at least", err.getvalue())
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ex:
            server.main(["--port", str(self.port), "--no-browser", "--no-save"])   # already taken
        self.assertEqual(ex.exception.code, 1)
        self.assertIn("can't listen", err.getvalue())

if __name__ == "__main__":
    unittest.main()

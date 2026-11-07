"""Web backend: a scripted human plays full games through the HTTP API."""

import json
import random
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from hexfield.ai import AIPlayer
from hexfield.game import OVER
from hexfield.web import server
from hexfield.web.session import WebGame, to_json_action


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/"

    def call(self, route, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + route, data=data, method="POST" if body is not None else "GET")
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
            st = c.call("act", {"action": to_json_action(a)})
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
        bad = self.c.call("act", {"action": ["city", 0]})
        self.assertEqual(bad["status"], 400)
        for p in st["players"]:
            self.assertNotIn("hand", p)

    def test_malformed_input_is_rejected(self):
        c = self.c
        st = c.call("new", {"opponents": 2, "seed": 5, "seat": 1})
        for action in (["end", 1], ["settle"], ["discard"], ["trade", 1], ["road", "x"],
                       ["discard", 5], ["discard", [0, 0, 0, 0, 0, 4]], ["roll", 7], "x", None):
            r = c.call("act", {"action": action})
            self.assertEqual(r.get("status"), 400, action)
        for give, get in (([1], [0, 0, 0, 0, 1]), (5, 5), ([1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1])):
            self.assertEqual(c.call("offer", {"give": give, "get": get}).get("status"), 400)
        self.assertEqual(c.call("new", {"opponents": "x"}).get("status"), 400)
        self.assertEqual(c.call("state?since=abc").get("status"), 400)
        self.assertEqual(st["phase"], "setup_settlement")

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
        g.pending_offer = {"from": 1, "give": [1, 0, 0, 0, 0], "get": [0, 1, 0, 0, 0], "answers": {"2": True}}
        self.assertNotIn("answers", g.view()["pending_offer"])
        from hexfield.web.session import public_reason
        self.assertEqual(public_reason("plan: buy development card -> play Knight  (eval +0.40)"),
                         "plan: buy development card  (eval +0.40)")

    def test_geometry(self):
        g = WebGame(2, "easy", seed=1, seat=1)
        geo = g.geometry
        # adjacent vertices are one hex-radius apart
        for a, b in geo["edges"]:
            va, vb = geo["vertices"][a], geo["vertices"][b]
            d = ((va["x"] - vb["x"]) ** 2 + (va["y"] - vb["y"]) ** 2) ** 0.5
            self.assertAlmostEqual(d, geo["radius"], delta=0.1)


if __name__ == "__main__":
    unittest.main()

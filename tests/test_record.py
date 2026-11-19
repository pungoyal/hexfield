"""Games as data: records replay exactly, and saved games survive on disk."""
import argparse
import json
import os
import unittest

from hexfield import cli, store
from hexfield.game import OVER
from hexfield.match import play_headless
from hexfield.record import Record


def bot_seats(n):
    return [{"name": f"Bot{i}", "kind": "bot", "level": "medium", "style": "balanced"} for i in range(n)]


def fingerprint(s):
    return (s.phase, s.turn, s.winner, s.hands, s.v_owner, s.e_owner, s.bank, s.bandit, s.rangers, len(s.events))


class RecordTests(unittest.TestCase):
    def game(self, seed=17, n=3, **options):
        rec = Record(seed, bot_seats(n), options)
        s, _ = play_headless(seed, ["medium"] * n, record=rec)
        return rec, s

    def test_a_record_replays_the_exact_game(self):
        rec, s = self.game()
        self.assertEqual(s.phase, OVER)
        self.assertEqual(fingerprint(rec.replay()), fingerprint(s))
        # ... and after a trip through JSON
        again = Record.loads(rec.dumps())
        self.assertEqual(fingerprint(again.replay()), fingerprint(s))
        self.assertEqual([e[3] for e in again.replay().events], [e[3] for e in s.events])

    def test_replay_does_not_depend_on_the_bots(self):
        # a record holds actions, not decisions: changing how bots think can't change a replay
        rec, s = self.game(seed=23)
        from hexfield import ai
        old = ai.PARAMS["pip_value"]
        ai.PARAMS["pip_value"] = old * 3
        try:
            self.assertEqual(fingerprint(rec.replay()), fingerprint(s))
        finally:
            ai.PARAMS["pip_value"] = old

    def test_options_are_part_of_the_record(self):
        rec, s = self.game(seed=5, dice="balanced", layout="beginner")
        t = Record.loads(rec.dumps()).replay()
        self.assertEqual((t.dice_mode, t.board.layout), ("balanced", "beginner"))
        self.assertEqual(fingerprint(t), fingerprint(s))
        rec6, s6 = self.game(seed=6, n=5)
        self.assertEqual(fingerprint(Record.loads(rec6.dumps()).replay()), fingerprint(s6))

    def test_partial_replay_copy_and_truncate(self):
        rec, s = self.game(seed=31)
        steps = rec.steps()
        half = steps[len(steps) // 2]
        mid = rec.replay(half)
        self.assertLess(mid.turn, s.turn)
        self.assertEqual(fingerprint(rec.copy(half).replay()), fingerprint(mid))
        rec.reviews = [{"at": 3, "seat": 0}, {"at": half + 5, "seat": 0}]
        rec.truncate(half)
        self.assertEqual(len(rec.entries), half)
        self.assertEqual(rec.reviews, [{"at": 3, "seat": 0}], "reviews of undone moves go with them")

    def test_bad_files_are_refused(self):
        for bad in ({}, {"version": 99}, {"version": 2, "seed": 1, "seats": []}, [1, 2]):
            with self.assertRaises((ValueError, KeyError, TypeError)):
                Record.from_dict(bad)
        with self.assertRaises(ValueError):
            Record(1, bot_seats(3), {"dice": "loaded"})


class StoreTests(unittest.TestCase):
    def test_save_load_list_delete(self):
        rec = Record(4, bot_seats(3), meta={"mode": "solo"})
        play_headless(4, ["easy"] * 3, record=rec)
        gid = store.new_id()
        store.save(gid, rec, offer=None, note="x")
        back, live = store.load(gid)
        self.assertEqual(back.entries, rec.entries)
        self.assertEqual(live["note"], "x")
        mine = [g for g in store.list_games() if g["id"] == gid]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["moves"], len(rec.steps()))
        self.assertFalse(os.path.exists(os.path.join(store.games_dir(), gid + ".json.tmp")))
        self.assertTrue(store.delete(gid))
        self.assertFalse(store.delete(gid))
        with self.assertRaises(OSError):
            store.load(gid)

    def test_ids_can_not_leave_the_games_folder(self):
        for bad in ("../../etc/passwd", "x", "", None, "20260101-000000-zzzzzz", "a/b"):
            with self.assertRaises(ValueError):
                store.load(bad)

    def test_a_broken_file_does_not_break_the_list(self):
        gid = store.new_id()
        with open(os.path.join(store.games_dir(), gid + ".json"), "w") as f:
            f.write("{not json")
        self.assertNotIn(gid, [g["id"] for g in store.list_games()])
        store.delete(gid)

    def test_a_game_from_another_version_is_counted_and_says_why(self):
        gid = store.new_id()
        old = {"version": 1, "seed": 3, "seats": bot_seats(3), "entries": []}
        with open(os.path.join(store.games_dir(), gid + ".json"), "w") as f:
            json.dump(old, f)
        before = store.other_versions()
        self.assertGreaterEqual(before, 1)
        self.assertNotIn(gid, [g["id"] for g in store.list_games()])
        with self.assertRaisesRegex(ValueError, "older version of Hexfield.*format 1.*format 2"):
            store.load(gid)
        with self.assertRaisesRegex(ValueError, "newer version"):
            Record.from_dict({**old, "version": 99})
        with self.assertRaisesRegex(ValueError, "not a Hexfield game file"):
            Record.from_dict({"anything": "else"})
        store.delete(gid)
        self.assertEqual(store.other_versions(), before - 1)

    def test_statistics(self):
        revs = [
            {"seat": 0, "phase": "setup_village", "delta": 0.0, "verdict": "best"},
            {"seat": 0, "phase": "main", "delta": 0.6, "verdict": "mistake"},
            {"seat": 0, "phase": "main", "delta": 0.1, "verdict": "good"},
            {"seat": 0, "phase": "main", "kind": "offer", "delta": 0.3, "verdict": "inaccuracy"},
            {"seat": 0, "phase": "main", "delta": 0.0, "verdict": "forced", "forced": True},
            {"seat": 1, "phase": "main", "delta": 2.0, "verdict": "blunder"},
        ]
        sm = store.review_summary(revs, 0)
        self.assertEqual(sm["decisions"], 4, "forced moves and other seats don't count")
        self.assertAlmostEqual(sm["accuracy"], 50.0)
        self.assertAlmostEqual(sm["lost"], 1.0)
        self.assertEqual(sm["groups"]["building and trading"]["n"], 2)
        self.assertEqual(sm["groups"]["trade offers"]["n"], 1)
        self.assertIsNone(store.review_summary([], 0))
        # a finished solo game shows up in the totals
        rec = Record(8, [{"name": "You", "kind": "human"}] + bot_seats(2), meta={"mode": "solo"})
        rec.meta["result"] = {"winner": 0, "fame": [10, 5, 6], "rounds": 14, "human": 0}
        rec.meta["coach"] = sm
        gid = store.new_id()
        store.save(gid, rec)
        st = store.stats()
        row = next(g for g in st["games"] if g["id"] == gid)
        self.assertTrue(row["won"])
        self.assertEqual(row["fame"], 10)
        self.assertGreaterEqual(st["groups"]["building and trading"]["n"], 2)
        store.delete(gid)


class TerminalSaveTests(unittest.TestCase):
    def args(self, **kw):
        base = dict(opponents=2, level="easy", seed=77, seat=1, name="You", no_color=True, no_review=True,
                    no_reasoning=True, fast=True, delay=0, autoroll=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_a_terminal_game_is_saved_and_resumes_where_it_stopped(self):
        game = cli.Game(self.args())
        game.out = lambda msg="": None
        script = iter(["0", "x"])

        def ask(prompt):
            # place the first village on a legal spot, then quit
            if "village" in prompt and not game.s.villages[game.human]:
                return str(game.s.legal_villages(game.human, setup=True)[0])
            raise cli.Quit()

        game.read_line = ask
        game.run()
        del script
        rec, _ = store.load(game.gid)
        self.assertEqual(len(rec.replay().villages[game.human]), 1)
        again = cli.Game(self.args(), rec, game.gid)
        self.assertEqual(fingerprint(again.s)[:8], fingerprint(game.s)[:8])
        self.assertEqual(again.human, game.human)
        self.assertEqual(json.loads(rec.dumps())["meta"]["ui"], "terminal")
        store.delete(game.gid)


if __name__ == "__main__":
    unittest.main()

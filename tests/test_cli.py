"""End-to-end test: a scripted 'human' plays a full game through the CLI prompts."""

import argparse
import contextlib
import io
import random
import unittest

from hexfield import cli
from hexfield.ai import AIPlayer
from hexfield.constants import RES_NAMES
from hexfield.game import MAIN, OVER, ROAD_BUILD, SETUP_ROAD, SETUP_SETTLE


def to_command(game, a):
    s = game.s
    k = a[0]
    bare = s.phase in (SETUP_SETTLE, SETUP_ROAD, ROAD_BUILD)
    if k == "macro":
        a, k = a[1][0], "bank"
    if k == "settle":
        return str(a[1]) if bare else f"s {a[1]}"
    if k == "road":
        return str(a[1]) if bare else f"rd {a[1]}"
    simple = {"city": f"c {a[1] if len(a) > 1 else ''}", "buy_dev": "d", "play_knight": "k",
              "play_road": "rb", "roll": "r", "end": "e", "end_road_building": "e"}
    if k in simple:
        return simple[k]
    if k == "play_mono":
        return f"mono {RES_NAMES[a[1]]}"
    if k == "play_yop":
        return f"yop {RES_NAMES[a[1]]} {RES_NAMES[a[2]]}"
    if k == "bank":
        return f"t {RES_NAMES[a[1]]} {RES_NAMES[a[2]]}"
    if k == "discard":
        return " ".join(f"{c} {RES_NAMES[r]}" for r, c in enumerate(a[1]) if c)
    if k == "robber":
        return s.board.hex_label(a[1]) + (f" {s.names[a[2]]}" if a[2] != -1 else "")
    raise ValueError(a)


def scripted_game(seed, opponents=2, color=False):
    args = argparse.Namespace(opponents=opponents, level="hard", seed=seed, seat=None, name="You",
                              no_color=not color, no_review=False, no_reasoning=False, fast=True,
                              delay=0, autoroll=seed % 2 == 0)
    game = cli.Game(args)
    pilot = AIPlayer(game.s.board, "medium", seed=seed)
    rnd = random.Random(seed)

    def ask(prompt):
        if "accept?" in prompt:
            return rnd.choice(["y", "n", "hint"])
        if "really quit" in prompt:
            return "n"
        if "whom" in prompt or "blank to cancel" in prompt:
            return ""
        x = rnd.random()
        if x < 0.03:
            return "hint"
        if x < 0.04:
            return "eval"
        if x < 0.045:
            return "undo"
        if x < 0.05:
            return "stats"
        if x < 0.06 and game.s.phase == MAIN:
            return "offer 1 wood for 1 ore"
        if x < 0.065:
            return "nonsense"
        a, _ = pilot.decide(game.s)
        if a[0] == "propose":
            a = ("end",)
        return to_command(game, a)

    game.ask = ask
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        game.run()
    return game, out.getvalue()


class CLITests(unittest.TestCase):
    def test_scripted_games_complete(self):
        for seed, opp, color in ((0, 2, False), (1, 3, True)):
            game, text = scripted_game(seed, opp, color)
            self.assertEqual(game.s.phase, OVER)
            self.assertTrue(game.reviews)
            self.assertNotIn("review unavailable", text)
            self.assertIn("Final scores", text)


if __name__ == "__main__":
    unittest.main()

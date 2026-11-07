"""End-to-end test: a scripted 'human' plays a full game through the CLI prompts."""

import argparse
import contextlib
import io
import random
import unittest

from hexfield import cli
from hexfield.ai import AIPlayer
from hexfield.constants import RES_NAMES
from hexfield.game import DISCARD, MAIN, OVER, ROAD_BUILD, ROBBER, SETUP_ROAD, SETUP_SETTLE, IllegalAction


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
        if "steal from whom?" in prompt:  # an answer is required: the first name offered
            return prompt.split("whom? ")[1].split(" ")[0]
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


def make_game(seed=7, seat=3):
    args = argparse.Namespace(opponents=3, level="hard", seed=seed, seat=seat, name="You",
                              no_color=True, no_review=False, no_reasoning=False, fast=True,
                              delay=0, autoroll=True)
    return cli.Game(args)


def scripted(game, answers):
    """Feed `answers` to the game's prompts; fail if it asks for more."""
    answers = list(answers)

    def ask(prompt):
        if not answers:
            raise AssertionError(f"unexpected prompt: {prompt}")
        return answers.pop(0)
    game.ask = ask
    return answers


def to_my_main(game):
    """Let bots play (for every seat) until it's the human's main phase."""
    s = game.s
    pilot = AIPlayer(s.board, "medium", seed=1)
    while not (s.actor() == game.human and s.phase == MAIN):
        a, _ = pilot.decide(s)
        s.apply(("end",) if a[0] == "propose" else a)
    game.printed = len(s.events)


def run_quiet(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args)
    return out.getvalue()


class CLIInputTests(unittest.TestCase):
    def test_out_of_range_road_is_rejected(self):
        game = make_game()
        to_my_main(game)
        s = game.s
        s.hands[game.human] = [5, 5, 0, 0, 0]
        before = (list(s.roads[game.human]), s.hands[game.human][:])
        scripted(game, ["rd 999", "s abc", "e"])
        text = run_quiet(game.human_turn_step)  # warns twice, then ends the turn
        self.assertIn("no edge 999", text)
        self.assertIn("isn't a vertex id", text)
        self.assertEqual((s.roads[game.human], s.hands[game.human]), before)
        with self.assertRaises(IllegalAction):  # the engine checks too
            s.clone(sim=False).apply(("road", 999))

    def test_repeated_trade_is_all_or_nothing_and_one_undo_step(self):
        game = make_game()
        to_my_main(game)
        s, me = game.s, game.human
        ratio = s.trade_ratio(me, 0)
        s.hands[me] = [2 * ratio + 1, 0, 0, 0, 0]
        start = s.hands[me][:]
        scripted(game, ["t wood ore 3", "t wood ore 2"])
        text = run_quiet(game.human_turn_step) + run_quiet(game.flush_events)
        self.assertIn(f"3 trades need {3 * ratio} wood", text)
        self.assertEqual(s.hands[me], [1, 0, 0, 0, 2])  # both trades, after nothing for 3
        self.assertEqual(len(game.reviews), 1)
        self.assertEqual(text.count("◆"), 1)
        scripted(game, ["undo"])
        run_quiet(game.human_turn_step)
        self.assertEqual(game.s.hands[me], start)
        self.assertEqual(game.s.phase, MAIN)  # back before the trades, not past the roll

    def test_discard_reads_cards_before_commands(self):
        game = make_game()
        to_my_main(game)
        s, me = game.s, game.human
        s.hands[me] = [3, 2, 1, 1, 1]
        s.phase, s.discards = DISCARD, {me: 3}
        scripted(game, ["b w w"])
        text = run_quiet(game.human_turn_step)
        self.assertEqual(s.hands[me], [1, 1, 1, 1, 1])
        self.assertNotIn("ROBBER", text)  # no board redraw

    def robber_game(self):
        game = make_game()
        s = game.s
        game.show = lambda *a, **k: None
        h = s.board.hex_from_label("U")
        vs = s.board.hex_vertices[h]
        for q, v in ((0, vs[0]), (1, vs[3])):
            s.v_owner[v], s.v_level[v] = q, 1
            s.settlements[q].append(v)
            s.hands[q] = [1, 1, 0, 0, 0]
        s.phase, s.current = ROBBER, game.human
        s.robber_return = MAIN
        return game, s

    def test_steal_prompt_needs_a_name(self):
        game, s = self.robber_game()
        second = s.names[1]
        left = scripted(game, ["U", "", "zz", "hint", second.lower()])
        text = run_quiet(game.human_turn_step)
        self.assertFalse(left)
        self.assertIn("Engine options", text)
        self.assertIn(f"steals a card from {second}", s.events[-2][3])

    def test_steal_prompt_accepts_undo(self):
        game, s = self.robber_game()
        scripted(game, ["U", "undo"])
        with self.assertRaises(cli.Undo):
            run_quiet(game.human_robber)


if __name__ == "__main__":
    unittest.main()

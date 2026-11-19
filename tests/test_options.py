"""Rule options: the large board for 5-6 players, the beginner map, balanced dice,
the special building phase, play styles and counter-offers."""
import unittest

from hexfield.ai import PARAMS, STYLES, AIPlayer, make_params
from hexfield.board import Board
from hexfield.constants import (BEGINNER_TILES, BRICK, DESERT, KNIGHT, LARGE_NUMBER_BAG, LARGE_TILE_BAG, ORE,
                                SHEEP, WHEAT, WOOD)
from hexfield.game import MAIN, OVER, PRE_ROLL, SPECIAL, GameState, IllegalAction
from hexfield.match import play_headless


class LargeBoardTests(unittest.TestCase):
    def test_topology_and_fair_numbers(self):
        for seed in range(12):
            b = Board(seed, large=True)
            self.assertEqual((b.H, b.V, b.E), (30, 80, 109))
            self.assertEqual(sorted(b.hex_resource), sorted(LARGE_TILE_BAG))
            self.assertEqual(sorted(n for n in b.hex_number if n), sorted(LARGE_NUMBER_BAG))
            self.assertEqual(len(b.deserts), 2)
            self.assertTrue(b._fair_numbers(b.hex_number), "6/8 or equal numbers touch")
            self.assertEqual(len(b.ports), 11)
            self.assertEqual(sum(1 for _, k in b.ports if k == "any"), 5)
            self.assertEqual(len({e for e, _ in b.ports}), 11)
            port_vs = [v for v in range(b.V) if b.vertex_port[v] is not None]
            self.assertEqual(len(port_vs), 22, "two harbours share a corner")
            labels = [b.hex_label(h) for h in range(b.H)]
            self.assertEqual(len(set(labels)), 30)
            self.assertTrue(all(b.hex_from_label(x.lower()) == h for h, x in enumerate(labels)))

    def test_standard_boards_are_unchanged_by_the_new_options(self):
        # a seed has always dealt this board: the new code paths must not disturb it
        b = Board(1)
        self.assertEqual((b.H, b.V, b.E), (19, 54, 72))
        self.assertEqual(b.hex_resource, Board(1, large=False, layout="random").hex_resource)
        self.assertEqual(GameState(4, seed=5).board.hex_number, GameState(4, seed=5, dice="balanced").board.hex_number)

    def test_beginner_board_is_the_rulebook_map(self):
        a, b = Board(1, layout="beginner"), Board(99, layout="beginner")
        self.assertEqual(a.hex_resource, [t for t, _ in BEGINNER_TILES])
        self.assertEqual(a.hex_number, [n for _, n in BEGINNER_TILES])
        self.assertEqual((a.ports, a.hex_resource), (b.ports, b.hex_resource), "the seed must not matter")
        self.assertEqual(a.hex_resource[9], DESERT)
        self.assertEqual([a.hex_resource.count(r) for r in (WOOD, BRICK, SHEEP, WHEAT, ORE)], [4, 3, 4, 4, 3])
        self.assertEqual(sorted(str(k) for _, k in a.ports), sorted(["any"] * 4 + [str(r) for r in range(5)]))
        self.assertTrue(a._fair_numbers(a.hex_number))
        # each harbour sits by the hex the rulebook shows, clockwise from the top left
        by = [(a.edge_hexes[e][0], kind) for e, kind in a.ports]
        self.assertEqual(by, [(0, "any"), (1, WHEAT), (6, ORE), (11, "any"), (15, SHEEP), (18, "any"),
                              (16, "any"), (12, BRICK), (3, WOOD)])


class DiceTests(unittest.TestCase):
    def rolls(self, s, n):
        out = []
        for _ in range(n):
            s.phase = PRE_ROLL
            s.hands = [[0] * 5 for _ in range(s.n)]  # nobody discards
            s._do_roll(s.current)
            out.append(s.last_roll)
        return out

    def test_balanced_dice_deal_each_combination_once_per_deck(self):
        s = GameState(4, seed=3, dice="balanced")
        first = self.rolls(s, 24)  # 36 cards, reshuffled when fewer than 13 are left
        pairs = [(a, b) for a, b, _ in first]
        self.assertEqual(len(set(pairs)), 24, "a combination came up twice inside one deck")
        self.assertTrue(all(a + b == t for a, b, t in first))
        many = self.rolls(GameState(4, seed=4, dice="balanced"), 2400)
        sevens = sum(1 for _, _, t in many if t == 7) / 2400
        self.assertAlmostEqual(sevens, 1 / 6, delta=0.02)

    def test_balanced_dice_replay_from_the_seed(self):
        a = self.rolls(GameState(3, seed=8, dice="balanced"), 60)
        b = self.rolls(GameState(3, seed=8, dice="balanced"), 60)
        self.assertEqual(a, b)
        self.assertNotEqual(a, self.rolls(GameState(3, seed=8), 60))

    def test_bad_options_are_refused(self):
        with self.assertRaises(ValueError):
            GameState(4, seed=1, dice="loaded")
        with self.assertRaises(ValueError):
            GameState(7, seed=1)


class SixPlayerTests(unittest.TestCase):
    def test_supplies(self):
        s = GameState(6, seed=2)
        self.assertEqual(s.bank, [24] * 5)
        self.assertEqual(len(s.deck), 34)
        self.assertEqual(s.deck.count(KNIGHT), 20)
        self.assertTrue(s.special_build and s.board.large)
        self.assertFalse(GameState(4, seed=2).special_build)

    def test_special_building_phase(self):
        s = GameState(5, seed=6)
        s.phase, s.current, s.turn = MAIN, 0, 1
        s.hands[2] = [0, 0, 1, 1, 1]      # player 2 can buy a development card
        s.hands[3] = [3, 0, 0, 0, 0]      # player 3 can't build anything
        s.apply(("end",))
        self.assertEqual(s.phase, SPECIAL)
        self.assertEqual(s.actor(), 2, "players who can't build are skipped")
        kinds = {a[0] for a in s.legal_actions()}
        self.assertEqual(kinds, {"buy_dev", "end"}, "no trading and no development cards in this phase")
        with self.assertRaises(IllegalAction):
            s.apply(("bank", 2, 0))
        with self.assertRaises(IllegalAction):
            s.apply(("play_knight",))
        s.apply(("buy_dev",))
        # nothing left to build with: the phase ends by itself and the next turn starts
        self.assertEqual((s.phase, s.current, s.turn), (PRE_ROLL, 1, 2))
        self.assertEqual(sum(s.new_devs[2]), 1)
        # the card becomes playable at the start of player 2's own turn
        s.phase = MAIN
        s.apply(("end",))
        self.assertEqual((s.phase, s.current), (PRE_ROLL, 2))
        self.assertEqual((sum(s.devs[2]), sum(s.new_devs[2])), (1, 0))

    def test_passing(self):
        s = GameState(5, seed=6)
        s.phase, s.current, s.turn = MAIN, 4, 1
        for q in (0, 1):
            s.hands[q] = [1, 1, 0, 0, 0]
            edge = s.board.vertex_edges[10 + 20 * q][0]
            s.v_owner[10 + 20 * q], s.v_level[10 + 20 * q] = q, 1
            s.settlements[q].append(10 + 20 * q)
            self.assertIn(edge, s.legal_roads(q))
        s.apply(("end",))
        self.assertEqual((s.phase, s.actor()), (SPECIAL, 0))
        s.apply(("end",))                 # player 0 passes
        self.assertEqual((s.phase, s.actor()), (SPECIAL, 1))
        s.apply(("road", s.legal_roads(1)[0]))
        self.assertEqual((s.phase, s.current), (PRE_ROLL, 0), "player 1 has nothing left: on to the next turn")

    def test_full_games_finish(self):
        for n, seed in ((5, 21), (6, 22)):
            s, _ = play_headless(seed, ["medium"] * n)
            self.assertEqual(s.phase, OVER, f"{n}-player game did not finish")
            self.assertGreaterEqual(s.vp(s.winner), 10)
            self.assertEqual(sum(s.bank) + sum(map(sum, s.hands)), 5 * 24, "cards appeared or vanished")

    def test_options_in_full_games(self):
        s, _ = play_headless(31, ["medium"] * 3, dice="balanced", layout="beginner")
        self.assertEqual(s.phase, OVER)
        self.assertEqual(s.board.layout, "beginner")


class StyleTests(unittest.TestCase):
    def test_styles_only_change_known_weights(self):
        for name, changes in STYLES.items():
            self.assertLessEqual(set(changes), set(PARAMS), name)
            P = make_params(name)
            self.assertEqual(set(P), set(PARAMS))
        self.assertEqual(make_params("balanced"), PARAMS)
        self.assertGreater(make_params("builder")["road_race"], PARAMS["road_race"])
        with self.assertRaises(KeyError):
            make_params("balanced", {"no_such_weight": 1.0})

    def test_every_style_plays_a_whole_game(self):
        names = sorted(STYLES)
        s, _ = play_headless(40, ["medium"] * 4, styles=names[:4])
        self.assertEqual(s.phase, OVER)
        s, _ = play_headless(41, ["medium"] * 3, styles=names[-3:])
        self.assertEqual(s.phase, OVER)


class CounterOfferTests(unittest.TestCase):
    def position(self):
        s = GameState(3, seed=9)
        while s.phase.startswith("setup"):
            s.apply(s.legal_actions()[0])
        s.phase, s.current = MAIN, 0
        return s

    def test_a_counter_is_a_deal_the_responder_would_take(self):
        found = 0
        for seed in range(12):
            s = GameState(3, seed=seed)
            while s.phase.startswith("setup"):
                s.apply(s.legal_actions()[0])
            s.phase, s.current = MAIN, 0
            s.hands[0] = [3, 3, 2, 0, 0]
            s.hands[1] = [0, 0, 0, 3, 3]
            for viewer in range(3):       # everyone has seen these cards arrive
                for q in range(3):
                    s.belief[viewer][q] = [float(x) for x in s.hands[q]]
            bot = AIPlayer(s.board, "hard", seed=seed)
            give, get = (1, 0, 0, 0, 0), (0, 0, 0, 0, 2)   # one wood for two ore: cheeky
            ok, _ = bot.evaluate_offer(s, 1, 0, give, get)
            c = bot.counter_offer(s, 1, 0, give, get)
            if ok or c is None:
                continue
            found += 1
            g2, t2, gain = c
            self.assertNotEqual((g2, t2), (give, get))
            self.assertTrue(bot.evaluate_offer(s, 1, 0, g2, t2)[0], "the bot must accept its own counter")
            self.assertTrue(s.can_afford(1, t2))
            self.assertTrue(sum(g2) >= sum(give) and sum(t2) <= sum(get), "a counter asks for more or gives less")
            self.assertGreater(gain, 0)
        self.assertGreater(found, 0, "no position produced a counter-offer")

    def test_no_counter_for_a_player_about_to_win_or_without_the_cards(self):
        s = self.position()
        bot = AIPlayer(s.board, "hard", seed=1)
        s.hands[1] = [0] * 5
        self.assertIsNone(bot.counter_offer(s, 1, 0, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1)))


if __name__ == "__main__":
    unittest.main()

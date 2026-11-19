import unittest

from hexfield.board import Board
from hexfield.constants import (CLAY, COSTS, BADLANDS, RANGER, HOARD, STONE, PATHFINDER,
                             VP_CARD, TIMBER, WINDFALL)
from hexfield.game import (DISCARD, MAIN, OVER, PRE_ROLL, ROAD_BUILD, BANDIT, SETUP_ROAD,
                        SETUP_SETTLE, GameState, IllegalAction, great_road)
from hexfield.ai import AIPlayer
from hexfield.match import play_headless
from hexfield.cli import parse_cards


def put_settlement(s, p, v, city=False):
    s.v_owner[v] = p
    s.v_level[v] = 2 if city else 1
    (s.cities if city else s.settlements)[p].append(v)


def put_road(s, p, e):
    s.e_owner[e] = p
    s.roads[p].append(e)


def to_main(s, p=0):
    """Skip setup: empty board, player p to act in MAIN."""
    s.phase = MAIN
    s.current = p
    s.turn = 1


def path_edges(b, start, n):
    """A simple path of n edges starting at vertex `start`."""
    edges, seen, v = [], {start}, start
    for _ in range(n):
        for e in b.vertex_edges[v]:
            a, c = b.edge_vertices[e]
            w = c if a == v else a
            if w not in seen:
                edges.append(e)
                seen.add(w)
                v = w
                break
    return edges


class BoardTests(unittest.TestCase):
    def test_topology(self):
        for seed in range(20):
            b = Board(seed)
            self.assertEqual((b.H, b.V, b.E), (19, 54, 72))
            self.assertEqual(sum(1 for e in range(b.E) if len(b.edge_hexes[e]) == 1), 30)
            for v in range(b.V):
                self.assertIn(len(b.vertex_neighbors[v]), (2, 3))
                self.assertIn(len(b.vertex_hexes[v]), (1, 2, 3))
            self.assertEqual(len(b.ports), 9)
            port_vs = [v for v in range(b.V) if b.vertex_port[v] is not None]
            self.assertEqual(len(port_vs), 18)
            self.assertEqual(b.hex_resource.count(BADLANDS), 1)
            self.assertEqual(b.hex_number[b.badlands], 0)

    def test_no_adjacent_red_numbers(self):
        for seed in range(50):
            b = Board(seed)
            for h in range(b.H):
                if b.hex_number[h] in (6, 8):
                    for n in b.hex_neighbors[h]:
                        self.assertNotIn(b.hex_number[n], (6, 8))


class SetupTests(unittest.TestCase):
    def test_snake_and_starting_resources(self):
        s = GameState(3, seed=1)
        order = []
        while s.phase in (SETUP_SETTLE, SETUP_ROAD):
            p = s.actor()
            if s.phase == SETUP_SETTLE:
                order.append(p)
                v = s.legal_settlements(p, setup=True)[0]
                s.apply(("settle", v))
            else:
                s.apply(("road", s.legal_setup_roads(p)[0]))
        self.assertEqual(order, [0, 1, 2, 2, 1, 0])
        self.assertEqual(s.phase, PRE_ROLL)
        self.assertEqual(s.current, 0)
        for p in range(3):
            second = s.settlements[p][1]
            expected = sum(1 for h in s.board.vertex_hexes[second] if s.board.hex_resource[h] != BADLANDS)
            self.assertEqual(s.hand_size(p), expected)

    def test_distance_rule(self):
        s = GameState(3, seed=1)
        v = 20
        s.apply(("settle", v))
        s.apply(("road", s.legal_setup_roads(0)[0]))
        for w in s.board.vertex_neighbors[v]:
            with self.assertRaises(IllegalAction):
                s.apply(("settle", w))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.s = GameState(3, seed=3)
        to_main(self.s)
        self.b = self.s.board

    def test_road_must_connect_and_is_blocked_by_opponents(self):
        s, b = self.s, self.b
        put_settlement(s, 0, 20)
        e1 = b.vertex_edges[20][0]
        s.hands[0] = [5, 5, 0, 0, 0]
        far = [e for e in range(b.E) if e not in s.legal_roads(0)][0]
        with self.assertRaises(IllegalAction):
            s.apply(("road", far))
        s.apply(("road", e1))
        self.assertEqual(s.hands[0], [4, 4, 0, 0, 0])
        a, c = b.edge_vertices[e1]
        tip = c if a == 20 else a
        put_settlement(s, 1, tip)  # opponent sits on the end of our road
        beyond = [e for e in b.vertex_edges[tip] if e != e1]
        for e in beyond:
            self.assertNotIn(e, s.legal_roads(0))

    def test_settlement_needs_road_and_cost(self):
        s, b = self.s, self.b
        put_settlement(s, 0, 20)
        path = path_edges(b, 20, 2)
        for e in path:
            put_road(s, 0, e)
        a, c = b.edge_vertices[path[1]]
        end = [x for x in (a, c) if x not in b.edge_vertices[path[0]]][0]
        self.assertIn(end, s.legal_settlements(0))
        with self.assertRaises(IllegalAction):
            s.apply(("settle", end))  # can't afford
        s.hands[0] = list(COSTS["settlement"])
        s.apply(("settle", end))
        self.assertEqual(s.vp(0), 2)
        self.assertEqual(s.hand_size(0), 0)

    def test_city(self):
        s = self.s
        put_settlement(s, 0, 20)
        s.hands[0] = [0, 0, 0, 2, 3]
        s.apply(("city", 20))
        self.assertEqual(s.vp(0), 2)
        self.assertIn(20, s.cities[0])
        self.assertNotIn(20, s.settlements[0])

    def test_bank_trade_ratios(self):
        s, b = self.s, self.b
        self.assertEqual(s.trade_ratio(0, TIMBER), 4)
        any_v = next(v for v in range(b.V) if b.vertex_port[v] == "any")
        put_settlement(s, 0, any_v)
        self.assertEqual(s.trade_ratio(0, TIMBER), 3)
        res_v = next(v for v in range(b.V) if b.vertex_port[v] not in (None, "any"))
        r = b.vertex_port[res_v]
        put_settlement(s, 0, res_v)
        self.assertEqual(s.trade_ratio(0, r), 2)
        s.hands[0][r] = 2
        other = (r + 1) % 5
        s.apply(("bank", r, other))
        self.assertEqual(s.hands[0][r], 0)
        self.assertEqual(s.hands[0][other], 1)


class LongestRoadTests(unittest.TestCase):
    def test_award_and_break(self):
        s = GameState(3, seed=5)
        to_main(s)
        b = s.board
        start = 0
        put_settlement(s, 0, start)
        path = path_edges(b, start, 6)
        self.assertEqual(len(path), 6)
        s.hands[0] = [6, 6, 0, 0, 0]
        for e in path[:5]:
            s.apply(("road", e))
        self.assertEqual(s.road_len[0], 5)
        self.assertEqual(s.longest, 0)
        self.assertEqual(s.vp(0), 3)
        # opponent builds in the middle of the road -> broken
        a, c = b.edge_vertices[path[2]]
        mid = c if c in b.edge_vertices[path[3]] else a
        put_settlement(s, 1, mid)
        s._update_longest()
        self.assertLess(s.road_len[0], 5)
        self.assertEqual(s.longest, -1)

    def test_cycle(self):
        s = GameState(3, seed=5)
        b = s.board
        h = 9
        vs = b.hex_vertices[h]
        for i in range(6):
            a, c = vs[i], vs[(i + 1) % 6]
            put_road(s, 0, b.edge_id[(min(a, c), max(a, c))])
        self.assertEqual(great_road(s, 0), 6)


class DiceTests(unittest.TestCase):
    def test_production_bandit_and_bank_shortage(self):
        s = GameState(3, seed=9)
        b = s.board
        h = next(i for i in range(b.H) if b.hex_number[i] and i != s.bandit)
        r = b.hex_resource[h]
        v0, v1 = b.hex_vertices[h][0], b.hex_vertices[h][3]
        put_settlement(s, 0, v0, city=True)
        put_settlement(s, 1, v1)
        s.phase = PRE_ROLL
        s.apply(("roll", b.hex_number[h]))
        self.assertGreaterEqual(s.hands[0][r], 2)
        self.assertGreaterEqual(s.hands[1][r], 1)
        # bandit blocks
        s.hands = [[0] * 5 for _ in range(3)]
        s.bandit = h
        s.phase = PRE_ROLL
        s.apply(("roll", b.hex_number[h]))
        self.assertEqual(s.hands[0][r], 0)
        # bank shortage with two takers -> nobody
        s.bandit = b.badlands
        s.bank[r] = 2
        s.phase = PRE_ROLL
        before = [s.hands[q][r] for q in range(3)]
        s.apply(("roll", b.hex_number[h]))
        other_hexes = [x for x in range(b.H) if b.hex_number[x] == b.hex_number[h] and x != h
                       and b.hex_resource[x] == r]
        if not other_hexes:
            self.assertEqual([s.hands[q][r] for q in range(3)], before)

    def test_seven_discard_bandit_steal(self):
        s = GameState(3, seed=9)
        b = s.board
        to_main(s)
        h = next(i for i in range(b.H) if i != s.bandit)
        put_settlement(s, 1, b.hex_vertices[h][0])
        s.hands[0] = [2, 2, 2, 2, 2]   # 10 -> discard 5
        s.hands[1] = [1, 0, 0, 0, 0]
        s.hands[2] = [3, 3, 1, 0, 0]   # 7 -> no discard
        s.phase = PRE_ROLL
        s.apply(("roll", 7))
        self.assertEqual(s.phase, DISCARD)
        self.assertEqual(s.discards, {0: 5})
        with self.assertRaises(IllegalAction):
            s.apply(("discard", (1, 1, 1, 1, 0)))
        s.apply(("discard", (1, 1, 1, 1, 1)))
        self.assertEqual(s.phase, BANDIT)
        with self.assertRaises(IllegalAction):
            s.apply(("bandit", s.bandit, -1))
        s.apply(("bandit", h, 1))
        self.assertEqual(s.bandit, h)
        self.assertEqual(s.hands[1], [0, 0, 0, 0, 0])
        self.assertEqual(s.hands[0][TIMBER], 2)
        self.assertEqual(s.phase, MAIN)


class DevCardTests(unittest.TestCase):
    def setUp(self):
        self.s = GameState(3, seed=11)
        to_main(self.s)

    def test_cannot_play_card_bought_this_turn(self):
        s = self.s
        s.deck = [RANGER]
        s.hands[0] = [0, 0, 1, 1, 1]
        s.apply(("buy_dev",))
        self.assertEqual(s.new_devs[0][RANGER], 1)
        with self.assertRaises(IllegalAction):
            s.apply(("play_ranger",))
        s.apply(("end",))
        self.assertEqual(s.devs[0][RANGER], 1)

    def test_one_dev_per_turn_and_hoard_windfall(self):
        s = self.s
        s.devs[0][HOARD] = 1
        s.devs[0][WINDFALL] = 1
        s.hands[1] = [0, 0, 0, 0, 3]
        s.hands[2] = [0, 0, 0, 0, 2]
        s.apply(("play_hoard", STONE))
        self.assertEqual(s.hands[0][STONE], 5)
        self.assertEqual(s.hands[1][STONE] + s.hands[2][STONE], 0)
        with self.assertRaises(IllegalAction):
            s.apply(("play_windfall", TIMBER, CLAY))
        s.dev_played = False
        s.apply(("play_windfall", TIMBER, CLAY))
        self.assertEqual(s.hands[0][TIMBER] + s.hands[0][CLAY], 2)

    def test_largest_patrol_and_vp_cards(self):
        s = self.s
        b = s.board
        for k in range(3):
            s.devs[0][RANGER] += 1
            s.dev_played = False
            s.apply(("play_ranger",))
            self.assertEqual(s.phase, BANDIT)
            h = next(i for i in range(b.H) if i != s.bandit)
            s.apply(("bandit", h, -1) if not s.bandit_targets(0, h) else ("bandit", h, s.bandit_targets(0, h)[0]))
        self.assertEqual(s.patrol, 0)
        self.assertEqual(s.vp(0), 2)
        s.devs[0][VP_CARD] = 2
        self.assertEqual(s.vp(0), 4)
        self.assertEqual(s.public_vp(0), 2)

    def test_road_building(self):
        s = self.s
        put_settlement(s, 0, 20)
        s.devs[0][PATHFINDER] = 1
        s.apply(("play_road",))
        self.assertEqual(s.phase, ROAD_BUILD)
        s.apply(("road", s.legal_roads(0)[0]))
        s.apply(("road", s.legal_roads(0)[0]))
        self.assertEqual(s.phase, MAIN)
        self.assertEqual(len(s.roads[0]), 2)
        self.assertEqual(s.hand_size(0), 0)

    def test_win(self):
        s = self.s
        for v in (0, 2, 9, 20, 30):
            put_settlement(s, 0, v)
        s.devs[0][VP_CARD] = 4
        s.hands[0] = [0, 0, 0, 2, 3]
        s.apply(("city", 0))
        self.assertEqual(s.phase, OVER)
        self.assertEqual(s.winner, 0)


class TradeTests(unittest.TestCase):
    def test_player_trade(self):
        s = GameState(3, seed=2)
        to_main(s)
        s.hands[0] = [2, 0, 0, 0, 0]
        s.hands[1] = [0, 0, 0, 0, 1]
        s.apply(("trade", 1, (2, 0, 0, 0, 0), (0, 0, 0, 0, 1)))
        self.assertEqual(s.hands[0], [0, 0, 0, 0, 1])
        self.assertEqual(s.hands[1], [2, 0, 0, 0, 0])

    def test_invalid_trades_are_rejected(self):
        s = GameState(3, seed=2)
        to_main(s)
        s.hands[0] = [2, 0, 0, 0, 0]
        s.hands[1] = [0, 0, 0, 0, 1]
        for partner, give, get in ((1, (-3, 0, 0, 0, 0), (0, 0, 0, 0, 0)),   # cards from nothing
                                   (1, (1, 0, 0, 0, 0), (0, 0, 0, 0, 0)),    # a gift
                                   (0, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1)),    # yourself
                                   (5, (1, 0, 0, 0, 0), (0, 0, 0, 0, 1))):   # nobody
            with self.assertRaises(IllegalAction):
                s.apply(("trade", partner, give, get))
        self.assertEqual(s.hands[0], [2, 0, 0, 0, 0])

    def test_parse_cards(self):
        self.assertEqual(parse_cards("2 timber stone".split()), [2, 0, 0, 0, 1])
        self.assertEqual(parse_cards("t t s".split()), [2, 0, 0, 0, 1])
        self.assertEqual(parse_cards("2t 1s g".split()), [2, 0, 0, 1, 1])
        self.assertEqual(parse_cards("wool2".split()), [0, 0, 2, 0, 0])
        self.assertIsNone(parse_cards("banana".split()))


class CloneTests(unittest.TestCase):
    def test_sim_clone_is_independent_and_hides_deck(self):
        s = GameState(3, seed=4)
        to_main(s)
        s.hands[0] = [0, 0, 1, 1, 1]
        t = s.clone()
        self.assertIsNone(t.deck)
        t.apply(("buy_dev",))
        self.assertEqual(s.hands[0], [0, 0, 1, 1, 1])
        self.assertEqual(len(s.deck), 25)
        self.assertEqual(t.deck_size(), 24)


class AITests(unittest.TestCase):
    def test_full_games_finish(self):
        for seed, n in ((1, 3), (2, 4)):
            s, _ = play_headless(seed, ["hard"] * n)
            self.assertEqual(s.phase, OVER)
            self.assertGreaterEqual(s.vp(s.winner), 10)
            # bank conservation: 19 of each resource in total
            for r in range(5):
                self.assertEqual(s.bank[r] + sum(s.hands[q][r] for q in range(n)), 19)

    def test_ai_takes_winning_city(self):
        s = GameState(3, seed=6)
        to_main(s)
        for v in (0, 2, 9, 20, 30):
            put_settlement(s, 0, v)
        s.devs[0][VP_CARD] = 4
        s.hands[0] = [0, 0, 0, 2, 3]
        a, _ = AIPlayer(s.board).decide(s)
        self.assertEqual(a[0], "city")

    def _road_win_position(self):
        """8 VP, a 3-road chain and exactly the cards for 2 more roads: Great Road wins."""
        s = GameState(3, seed=6)
        to_main(s)
        b = s.board
        start = next(v for v in range(b.V) if len(path_edges(b, v, 6)) == 6)
        path = path_edges(b, start, 6)
        put_settlement(s, 0, start)
        for e in path[:3]:
            put_road(s, 0, e)
        put_settlement(s, 0, next(v for v in range(b.V) if s.vertex_free(v)))
        s._update_longest()
        s.devs[0][VP_CARD] = 6
        s.hands[0] = [2, 2, 0, 0, 0]
        return s

    def test_win_search_finds_two_road_great_road(self):
        s = self._road_win_position()
        ai = AIPlayer(s.board, "hard", seed=1)
        line = ai.find_win(ai.view(s, 0), 0)
        self.assertEqual([a[0] for a in line], ["road", "road"])
        for level in ("easy", "medium", "hard"):
            t = s.clone(sim=False)
            bot = AIPlayer(t.board, level, seed=2)
            for _ in range(2):
                a, why = bot.decide(t)
                self.assertEqual(a[0], "road")
                self.assertTrue(why.startswith("winning line"), why)
                t.apply(a)
            self.assertEqual(t.winner, 0, level)

    def test_win_search_plays_windfall(self):
        s = GameState(3, seed=6)
        to_main(s)
        b = s.board
        put_settlement(s, 0, 0)
        e = next(e for e in b.vertex_edges[0])
        w = [v for v in b.edge_vertices[e] if v != 0][0]
        e2 = next(x for x in b.vertex_edges[w] if x != e)
        put_road(s, 0, e)
        put_road(s, 0, e2)
        s.devs[0][VP_CARD] = 8
        s.devs[0][WINDFALL] = 1
        s.hands[0] = [0, 0, 1, 1, 0]           # missing timber + clay for the settlement
        self.assertTrue(s.legal_settlements(0))
        a, why = AIPlayer(s.board, "hard", seed=1).decide(s)
        self.assertEqual(a, ("play_windfall", TIMBER, CLAY), why)
        s.apply(a)
        a, _ = AIPlayer(s.board, "hard", seed=1).decide(s)
        s.apply(a)
        self.assertEqual(s.winner, 0)

    def test_win_search_does_not_count_on_a_steal(self):
        """A ranger's steal is random: a line that needs the stolen card is not a forced win."""
        s = GameState(3, seed=6)
        to_main(s)
        put_settlement(s, 0, 0)
        e = s.board.vertex_edges[0][0]
        w = [v for v in s.board.edge_vertices[e] if v != 0][0]
        put_road(s, 0, e)
        put_road(s, 0, next(x for x in s.board.vertex_edges[w] if x != e))
        s.devs[0][VP_CARD] = 8
        s.devs[0][RANGER] = 1
        s.hands[0] = [1, 0, 1, 1, 0]           # one clay short
        s.hands[1] = [0, 5, 0, 0, 0]
        put_settlement(s, 1, next(v for v in range(s.board.V) if s.vertex_free(v)
                                  and s.bandit not in s.board.vertex_hexes[v]))
        s.belief = [[[float(x) for x in s.hands[q]] for q in range(3)] for _ in range(3)]
        ai = AIPlayer(s.board, "hard", seed=1)
        self.assertIsNone(ai.find_win(ai.view(s, 0), 0))

    def test_coach_rates_missing_a_win_as_blunder(self):
        s = self._road_win_position()
        coach = AIPlayer(s.board, "hard", seed=1)
        best, best_a, why = coach.best_value(s)
        self.assertEqual(best_a[0], "road")
        self.assertIn("wins the game", why)
        self.assertGreaterEqual(best - coach.value_of_action(s, ("end",)), 1.0)
        self.assertAlmostEqual(coach.value_of_action(s, best_a), best)
        self.assertLess(best, 100)             # VP-eq scale, not the internal 1000
        self.assertEqual(coach.analyze(s, top=1)[0][1][0], "road")

    def test_rolling_before_a_ranger_is_not_a_mistake(self):
        """The ranger can still be played after the roll; rolling first only risks the
        production the bandit blocks this roll."""
        for seed in range(6):
            s = GameState(3, seed=seed)
            to_main(s)
            s.phase = PRE_ROLL
            b = s.board
            h = next(h for h in range(b.H) if b.hex_resource[h] != BADLANDS and h != s.bandit)
            put_settlement(s, 1, b.hex_vertices[h][0])
            put_settlement(s, 0, next(v for v in range(b.V) if s.vertex_free(v) and s.bandit not in b.vertex_hexes[v]))
            s.hands[1] = [2, 2, 2, 2, 2]
            s.belief = [[[float(x) for x in s.hands[q]] for q in range(3)] for _ in range(3)]
            s.devs[0][RANGER] = 1
            coach = AIPlayer(s.board, "hard", seed=1)
            ranked = {a[0]: sc for sc, a, _ in coach.rank_preroll(coach.view(s, 0), 0)}
            self.assertLess(ranked["play_ranger"] - ranked["roll"], 0.05)
            self.assertAlmostEqual(coach.value_of_action(s, ("roll",)), ranked["roll"])

    def test_setup_scores_are_vp_equivalents(self):
        """A different setup road is not a blunder; settlement order is unchanged by scaling."""
        spreads = []
        for seed in range(8):
            s = GameState(3, seed=seed)
            ai = AIPlayer(s.board, "hard", seed=1)
            a, _ = ai.decide(s)
            s.apply(a)
            ranked = ai.rank_setup_roads(s, s.actor())
            spreads.append(ranked[0][0] - ranked[-1][0])
        self.assertLess(sorted(spreads)[len(spreads) // 2], 0.5)


class HiddenInfoTests(unittest.TestCase):
    def test_offer_judged_without_the_proposers_hidden_cards(self):
        """A bot judging an offer sees only how many dev cards the proposer holds."""
        gains = []
        for hidden in (VP_CARD, RANGER):
            s = GameState(3, seed=5)
            to_main(s, 0)
            for v in (0, 9, 20, 30):
                put_settlement(s, 0, v)
            s.devs[0][hidden] = 4          # 5 public VP + 4 VP cards would be a win
            s.hands[0] = [0, 0, 1, 2, 2]
            s.hands[1] = [0, 0, 0, 0, 2]
            s.belief = [[[float(x) for x in s.hands[q]] for q in range(3)] for _ in range(3)]
            ai = AIPlayer(s.board, "hard", seed=1)
            gains.append(ai.evaluate_offer(s, 1, 0, (0, 0, 1, 0, 0), (0, 0, 0, 0, 1))[1])
        self.assertAlmostEqual(gains[0], gains[1], places=6)

    def test_public_events_are_tracked_exactly(self):
        s, _ = play_headless(3, ["hard"] * 3, max_turns=30)
        # beliefs always sum to the (public) hand size
        for v in range(3):
            for q in range(3):
                self.assertAlmostEqual(sum(s.belief[v][q]), s.hand_size(q), places=6)
            self.assertEqual(s.belief[v][v], [float(x) for x in s.hands[v]])

    def test_steal_is_hidden_from_bystanders(self):
        s = GameState(3, seed=9)
        b = s.board
        to_main(s, p=1)
        h = next(i for i in range(b.H) if i != s.bandit)
        put_settlement(s, 2, b.hex_vertices[h][0])
        s.hands[2] = [1, 0, 0, 0, 1]
        s.belief = [[[float(x) for x in s.hands[q]] for q in range(3)] for _ in range(3)]
        s.phase = BANDIT
        s.bandit_return = MAIN
        s.apply(("bandit", h, 2))
        stolen = 0 if s.hands[1][0] else 4
        # thief and victim know exactly; player 0 only knows "one of timber/stone"
        self.assertEqual(s.belief[1][2], [float(x) for x in s.hands[2]])
        self.assertEqual(s.belief[2][1][stolen], 1.0)
        self.assertAlmostEqual(s.belief[0][2][0], 0.5)
        self.assertAlmostEqual(s.belief[0][2][4], 0.5)
        self.assertAlmostEqual(s.belief[0][1][0] + s.belief[0][1][4], 1.0)

    def test_ai_view_uses_beliefs_not_real_hands(self):
        s = GameState(3, seed=9)
        to_main(s)
        s.hands[1] = [0, 0, 0, 0, 7]
        s.belief[0][1] = [1.0, 1.0, 1.0, 2.0, 2.0]
        v = AIPlayer.view(s, 0)
        self.assertEqual(v.hands[1], [1.0, 1.0, 1.0, 2.0, 2.0])
        self.assertEqual(v.hands[0], s.hands[0])

    def test_panel_hides_opponent_cards(self):
        from hexfield.render import Renderer, ANSI_RE
        s = GameState(3, seed=9, names=["You", "Ada", "Bo"])
        to_main(s)
        s.hands[1] = [3, 0, 0, 0, 0]
        s.hands[0] = [0, 2, 0, 0, 0]
        text = ANSI_RE.sub("", "\n".join(Renderer(s, 0, color=False).panel_lines()))
        self.assertIn("Cl:2", text)
        self.assertNotIn("Wo:3", text)


if __name__ == "__main__":
    unittest.main()

import unittest

from hexfield.board import Board
from hexfield.constants import (BRICK, COSTS, DESERT, KNIGHT, MONOPOLY, ORE, ROAD_BUILDING,
                             VP_CARD, WOOD, YEAR_OF_PLENTY)
from hexfield.game import (DISCARD, MAIN, OVER, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD,
                        SETUP_SETTLE, GameState, IllegalAction, longest_road)
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
    s.rolled = True


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
            self.assertEqual(b.hex_resource.count(DESERT), 1)
            self.assertEqual(b.hex_number[b.desert], 0)

    def test_no_adjacent_red_numbers(self):
        for seed in range(50):
            b = Board(seed)
            for h in range(b.H):
                if b.hex_number[h] in (6, 8):
                    for n in b.hex_neighbors[h]:
                        self.assertNotIn(b.hex_number[n], (6, 8))

    def test_seed_reproducible(self):
        a, b = GameState(3, seed=7), GameState(3, seed=7)
        self.assertEqual(a.board.hex_resource, b.board.hex_resource)
        self.assertEqual(a.board.hex_number, b.board.hex_number)
        self.assertEqual(a.deck, b.deck)


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
            expected = sum(1 for h in s.board.vertex_hexes[second] if s.board.hex_resource[h] != DESERT)
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
        self.assertEqual(s.trade_ratio(0, WOOD), 4)
        any_v = next(v for v in range(b.V) if b.vertex_port[v] == "any")
        put_settlement(s, 0, any_v)
        self.assertEqual(s.trade_ratio(0, WOOD), 3)
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
        s.v_owner[mid] = 1
        s.v_level[mid] = 1
        s.settlements[1].append(mid)
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
        self.assertEqual(longest_road(s, 0), 6)


class DiceTests(unittest.TestCase):
    def test_production_robber_and_bank_shortage(self):
        s = GameState(3, seed=9)
        b = s.board
        h = next(i for i in range(b.H) if b.hex_number[i] not in (0,) and i != s.robber)
        r = b.hex_resource[h]
        v0, v1 = b.hex_vertices[h][0], b.hex_vertices[h][3]
        put_settlement(s, 0, v0, city=True)
        put_settlement(s, 1, v1)
        s.phase = PRE_ROLL
        s.apply(("roll", b.hex_number[h]))
        self.assertGreaterEqual(s.hands[0][r], 2)
        self.assertGreaterEqual(s.hands[1][r], 1)
        # robber blocks
        s.hands = [[0] * 5 for _ in range(3)]
        s.robber = h
        s.phase = PRE_ROLL
        s.apply(("roll", b.hex_number[h]))
        self.assertEqual(s.hands[0][r], 0)
        # bank shortage with two takers -> nobody
        s.robber = b.desert
        s.bank[r] = 2
        s.phase = PRE_ROLL
        before = [s.hands[q][r] for q in range(3)]
        s.apply(("roll", b.hex_number[h]))
        other_hexes = [x for x in range(b.H) if b.hex_number[x] == b.hex_number[h] and x != h
                       and b.hex_resource[x] == r]
        if not other_hexes:
            self.assertEqual([s.hands[q][r] for q in range(3)], before)

    def test_seven_discard_robber_steal(self):
        s = GameState(3, seed=9)
        b = s.board
        to_main(s)
        h = next(i for i in range(b.H) if i != s.robber)
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
        self.assertEqual(s.phase, ROBBER)
        with self.assertRaises(IllegalAction):
            s.apply(("robber", s.robber, -1))
        s.apply(("robber", h, 1))
        self.assertEqual(s.robber, h)
        self.assertEqual(s.hands[1], [0, 0, 0, 0, 0])
        self.assertEqual(s.hands[0][WOOD], 2)
        self.assertEqual(s.phase, MAIN)


class DevCardTests(unittest.TestCase):
    def setUp(self):
        self.s = GameState(3, seed=11)
        to_main(self.s)

    def test_cannot_play_card_bought_this_turn(self):
        s = self.s
        s.deck = [KNIGHT]
        s.hands[0] = [0, 0, 1, 1, 1]
        s.apply(("buy_dev",))
        self.assertEqual(s.new_devs[0][KNIGHT], 1)
        with self.assertRaises(IllegalAction):
            s.apply(("play_knight",))
        s.apply(("end",))
        self.assertEqual(s.devs[0][KNIGHT], 1)

    def test_one_dev_per_turn_and_monopoly_yop(self):
        s = self.s
        s.devs[0][MONOPOLY] = 1
        s.devs[0][YEAR_OF_PLENTY] = 1
        s.hands[1] = [0, 0, 0, 0, 3]
        s.hands[2] = [0, 0, 0, 0, 2]
        s.apply(("play_mono", ORE))
        self.assertEqual(s.hands[0][ORE], 5)
        self.assertEqual(s.hands[1][ORE] + s.hands[2][ORE], 0)
        with self.assertRaises(IllegalAction):
            s.apply(("play_yop", WOOD, BRICK))
        s.dev_played = False
        s.apply(("play_yop", WOOD, BRICK))
        self.assertEqual(s.hands[0][WOOD] + s.hands[0][BRICK], 2)

    def test_largest_army_and_vp_cards(self):
        s = self.s
        b = s.board
        for k in range(3):
            s.devs[0][KNIGHT] += 1
            s.dev_played = False
            s.apply(("play_knight",))
            self.assertEqual(s.phase, ROBBER)
            h = next(i for i in range(b.H) if i != s.robber)
            s.apply(("robber", h, -1) if not s.robber_targets(0, h) else ("robber", h, s.robber_targets(0, h)[0]))
        self.assertEqual(s.army, 0)
        self.assertEqual(s.vp(0), 2)
        s.devs[0][VP_CARD] = 2
        self.assertEqual(s.vp(0), 4)
        self.assertEqual(s.public_vp(0), 2)

    def test_road_building(self):
        s = self.s
        put_settlement(s, 0, 20)
        s.devs[0][ROAD_BUILDING] = 1
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

    def test_parse_cards(self):
        self.assertEqual(parse_cards("2 wood ore".split()), [2, 0, 0, 0, 1])
        self.assertEqual(parse_cards("w w o".split()), [2, 0, 0, 0, 1])
        self.assertEqual(parse_cards("2w 1o wh".split()), [2, 0, 0, 1, 1])
        self.assertEqual(parse_cards("sheep2".split()), [0, 0, 2, 0, 0])
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


class HiddenInfoTests(unittest.TestCase):
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
        h = next(i for i in range(b.H) if i != s.robber)
        put_settlement(s, 2, b.hex_vertices[h][0])
        s.hands[2] = [1, 0, 0, 0, 1]
        s.belief = [[[float(x) for x in s.hands[q]] for q in range(3)] for _ in range(3)]
        s.phase = ROBBER
        s.robber_return = MAIN
        s.apply(("robber", h, 2))
        stolen = 0 if s.hands[1][0] else 4
        # thief and victim know exactly; player 0 only knows "one of wood/ore"
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
        self.assertIn("Br:2", text)
        self.assertNotIn("Wo:3", text)


if __name__ == "__main__":
    unittest.main()

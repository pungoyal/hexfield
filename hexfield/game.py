"""Rules engine: game state, legal moves and state transitions.

Actions are plain tuples:
    ('settle', v) ('road', e) ('city', v) ('buy_dev',)
    ('play_knight',) ('play_road',) ('play_mono', r) ('play_yop', r1, r2)
    ('roll',) ('roll', total)            # forced total used by AI simulations / tests
    ('discard', (w, b, s, wh, o))
    ('robber', hex, victim)              # victim = -1 when nobody can be robbed
    ('bank', give_res, get_res)          # at the player's best ratio
    ('trade', partner, give(5), get(5))  # already agreed player trade
    ('end',)                             # also "pass" in the special building phase

Options (all off by default, so a seed replays the classic game):
    dice="balanced"     the 36 dice combinations are dealt from a shuffled deck
    layout="beginner"   the fixed starting map
    5 or 6 players      play on the large board with the special building phase
"""

import random

from .board import Board
from .constants import (BANK_START, COSTS, DESERT, DEV_DECK_COUNTS, KNIGHT, LARGE_BANK_START,
                        LARGE_DEV_DECK_COUNTS, LARGE_FROM, MAX_CITIES, MAX_ROADS, MAX_SETTLEMENTS,
                        MONOPOLY, RES_NAMES, ROAD_BUILDING, UNKNOWN_DEV, VP_CARD, WIN_VP,
                        YEAR_OF_PLENTY, DEV_NAMES)

SETUP_SETTLE, SETUP_ROAD = "setup_settlement", "setup_road"
PRE_ROLL, MAIN, DISCARD, ROBBER, ROAD_BUILD, OVER = (
    "pre_roll", "main", "discard", "move_robber", "road_building", "game_over")
SPECIAL = "special_build"  # 5-6 players: after each turn the others may build (no trading)

DICE_MODES = ("random", "balanced")
LAYOUTS = ("random", "beginner")
# balanced dice: all 36 combinations in a deck; it is reshuffled when this few are left,
# so the last rolls of a deck can't be worked out by counting
BALANCED_RESHUFFLE = 13


class IllegalAction(Exception):
    pass


class GameState:
    def __init__(self, n_players=4, seed=None, board=None, names=None, dice="random",
                 layout="random", special_build=None):
        if not 2 <= n_players <= 6:
            raise ValueError("2 to 6 players")
        if dice not in DICE_MODES or layout not in LAYOUTS:
            raise ValueError("unknown dice mode or layout")
        master = random.Random(seed)
        large = n_players >= LARGE_FROM
        self.board = board or Board(master.randrange(1 << 30), large=large, layout=layout)
        b = self.board
        self.n = n_players
        self.dice_mode = dice
        self.dice_bag = [] if dice == "balanced" else None
        self.special_build = large if special_build is None else bool(special_build)
        self.special_queue = []
        self.dev_counts = LARGE_DEV_DECK_COUNTS if large else DEV_DECK_COUNTS
        self.bank_start = LARGE_BANK_START if large else BANK_START
        self.names = names or [f"P{i}" for i in range(n_players)]
        # independent random streams so a replay with the same seed gets the same
        # dice / deck even if decisions differ
        self.dice_rng = random.Random(master.randrange(1 << 30))
        self.steal_rng = random.Random(master.randrange(1 << 30))
        deck_rng = random.Random(master.randrange(1 << 30))
        self.deck = []
        for t, c in enumerate(self.dev_counts):
            self.deck += [t] * c
        deck_rng.shuffle(self.deck)

        self.hands = [[0] * 5 for _ in range(n_players)]
        self.devs = [[0] * 6 for _ in range(n_players)]      # playable (owned before this turn)
        self.new_devs = [[0] * 6 for _ in range(n_players)]  # bought this turn
        self.knights = [0] * n_players
        self.v_owner = [-1] * b.V
        self.v_level = [0] * b.V
        self.e_owner = [-1] * b.E
        self.settlements = [[] for _ in range(n_players)]
        self.cities = [[] for _ in range(n_players)]
        self.roads = [[] for _ in range(n_players)]
        self.road_len = [0] * n_players
        self.bank = [self.bank_start] * 5
        self.robber = b.desert
        self.longest = -1
        self.army = -1

        self.setup_order = list(range(n_players)) + list(reversed(range(n_players)))
        self.setup_idx = 0
        self.last_settlement = -1
        self.current = 0
        self.phase = SETUP_SETTLE
        self.turn = 0              # increments every time a turn ends
        self.dev_played = False
        self.discards = {}         # player -> cards owed
        self.robber_return = MAIN
        self.free_roads = 0
        self.last_roll = None
        self.winner = -1
        self.sim = False           # True inside AI simulations (hides deck)
        self.events = []           # human readable log for the UI
        self.roll_hist = [0] * 13
        # belief[viewer][target]: what `viewer` can know about `target`'s hand from
        # public information (production, builds, trades, discards). Steals between
        # two *other* players are tracked in expectation, like a card-counting human.
        self.belief = [[[0.0] * 5 for _ in range(n_players)] for _ in range(n_players)]
        self._stolen = None
        self.trades_this_turn = 0

    # ----------------------------------------------------------------- copy
    def clone(self, sim=True):
        c = GameState.__new__(GameState)
        c.__dict__.update(self.__dict__)
        c.hands = [h[:] for h in self.hands]
        c.devs = [d[:] for d in self.devs]
        c.new_devs = [d[:] for d in self.new_devs]
        c.knights = self.knights[:]
        c.v_owner = self.v_owner[:]
        c.v_level = self.v_level[:]
        c.e_owner = self.e_owner[:]
        c.settlements = [s[:] for s in self.settlements]
        c.cities = [s[:] for s in self.cities]
        c.roads = [s[:] for s in self.roads]
        c.road_len = self.road_len[:]
        c.bank = self.bank[:]
        c.discards = dict(self.discards)
        c.roll_hist = self.roll_hist[:]
        c.special_queue = self.special_queue[:]
        if self.dice_bag is not None:
            c.dice_bag = self.dice_bag[:]
        if sim:
            c.sim = True
            c.belief = None
            c.events = None
            c.deck = None
            c.deck_len = self.deck_size()
            c.dice_rng = None
            c.steal_rng = None
        else:
            c.belief = [[b[:] for b in row] for row in self.belief]
            c.deck = self.deck[:]
            c.events = self.events[:]
            c.dice_rng = random.Random()
            c.dice_rng.setstate(self.dice_rng.getstate())
            c.steal_rng = random.Random()
            c.steal_rng.setstate(self.steal_rng.getstate())
        return c

    def deck_size(self):
        return len(self.deck) if self.deck is not None else self.deck_len

    # ------------------------------------------------------------- helpers
    def log(self, msg, kind="info", player=None):
        if self.events is not None:
            self.events.append((self.turn, player, kind, msg))

    def actor(self):
        """The player who must make the next decision."""
        if self.phase == DISCARD:
            for i in range(self.n):
                p = (self.current + i) % self.n
                if self.discards.get(p, 0) > 0:
                    return p
        if self.phase in (SETUP_SETTLE, SETUP_ROAD):
            return self.setup_order[self.setup_idx]
        if self.phase == SPECIAL:
            return self.special_queue[0]
        return self.current

    def hand_size(self, p):
        return sum(self.hands[p])

    def dev_count(self, p):
        return sum(self.devs[p]) + sum(self.new_devs[p])

    def vp_cards(self, p):
        return self.devs[p][VP_CARD] + self.new_devs[p][VP_CARD]

    def public_vp(self, p):
        vp = len(self.settlements[p]) + 2 * len(self.cities[p])
        if self.longest == p:
            vp += 2
        if self.army == p:
            vp += 2
        return vp

    def vp(self, p):
        return self.public_vp(p) + self.vp_cards(p)

    def can_afford(self, p, cost):
        h = self.hands[p]
        return all(h[i] >= cost[i] for i in range(5))

    def pay(self, p, cost):
        h = self.hands[p]
        for i in range(5):
            h[i] -= cost[i]
            self.bank[i] += cost[i]

    def trade_ratio(self, p, r):
        best = 4
        for v in self.settlements[p] + self.cities[p]:
            port = self.board.vertex_port[v]
            if port == r:
                return 2
            if port == "any":
                best = 3
        return best

    def ratios(self, p):
        return [self.trade_ratio(p, r) for r in range(5)]

    # ------------------------------------------------------- placement rules
    def vertex_free(self, v):
        """Unoccupied and satisfies the distance rule."""
        return self.v_owner[v] == -1 and all(self.v_owner[w] == -1
                                             for w in self.board.vertex_neighbors[v])

    def touches_own_road(self, p, v):
        return any(self.e_owner[e] == p for e in self.board.vertex_edges[v])

    def legal_settlements(self, p, setup=False):
        if len(self.settlements[p]) >= MAX_SETTLEMENTS:
            return []
        out = []
        for v in range(self.board.V):
            if self.vertex_free(v) and (setup or self.touches_own_road(p, v)):
                out.append(v)
        return out

    def legal_roads(self, p):
        if len(self.roads[p]) >= MAX_ROADS:
            return []
        b = self.board
        out = []
        for e in range(b.E):
            if self.e_owner[e] != -1:
                continue
            for v in b.edge_vertices[e]:
                o = self.v_owner[v]
                if o == p:
                    out.append(e)
                    break
                if o == -1 and any(self.e_owner[x] == p for x in b.vertex_edges[v] if x != e):
                    out.append(e)
                    break
        return out

    def legal_setup_roads(self, p):
        return [e for e in self.board.vertex_edges[self.last_settlement] if self.e_owner[e] == -1]

    def legal_cities(self, p):
        if len(self.cities[p]) >= MAX_CITIES:
            return []
        return list(self.settlements[p])

    def robber_targets(self, p, h):
        vics = set()
        for v in self.board.hex_vertices[h]:
            o = self.v_owner[v]
            if o != -1 and o != p and self.hand_size(o) > 0:
                vics.add(o)
        return sorted(vics)

    def _bank_has_pair(self, r1, r2):
        """Can the bank hand out one r1 and one r2 (Year of Plenty)?"""
        return self.bank[r1] >= 1 + (r1 == r2) and self.bank[r2] >= 1

    def playable(self, p, card):
        return (not self.dev_played) and self.devs[p][card] > 0 and card != VP_CARD

    # ------------------------------------------------------------ legality
    def legal_actions(self, include_trades=True):
        """Enumerate legal actions (discards excluded - they're combinatorial)."""
        p = self.actor()
        ph = self.phase
        if ph == SETUP_SETTLE:
            return [("settle", v) for v in self.legal_settlements(p, setup=True)]
        if ph == SETUP_ROAD:
            return [("road", e) for e in self.legal_setup_roads(p)]
        if ph == ROBBER:
            acts = []
            for h in range(self.board.H):
                if h == self.robber:
                    continue
                vics = self.robber_targets(p, h)
                if vics:
                    acts += [("robber", h, v) for v in vics]
                else:
                    acts.append(("robber", h, -1))
            return acts
        if ph == ROAD_BUILD:
            acts = [("road", e) for e in self.legal_roads(p)]
            return acts or [("end_road_building",)]
        if ph in (DISCARD, OVER):
            return []
        acts = [] if ph == SPECIAL else self._dev_actions(p)
        if ph == PRE_ROLL:
            acts.append(("roll",))
            return acts
        # MAIN
        if self.can_afford(p, COSTS["settlement"]):
            acts += [("settle", v) for v in self.legal_settlements(p)]
        if self.can_afford(p, COSTS["city"]):
            acts += [("city", v) for v in self.legal_cities(p)]
        if self.can_afford(p, COSTS["road"]):
            acts += [("road", e) for e in self.legal_roads(p)]
        if self.can_afford(p, COSTS["dev"]) and self.deck_size() > 0:
            acts.append(("buy_dev",))
        if include_trades and ph == MAIN:
            for give in range(5):
                ratio = self.trade_ratio(p, give)
                if self.hands[p][give] >= ratio:
                    for get in range(5):
                        if get != give and self.bank[get] > 0:
                            acts.append(("bank", give, get))
        acts.append(("end",))
        return acts

    def _dev_actions(self, p):
        acts = []
        if self.dev_played:
            return acts
        d = self.devs[p]
        if d[KNIGHT]:
            acts.append(("play_knight",))
        if d[ROAD_BUILDING] and self.legal_roads(p):
            acts.append(("play_road",))
        if d[MONOPOLY]:
            acts += [("play_mono", r) for r in range(5)]
        if d[YEAR_OF_PLENTY]:
            acts += [("play_yop", a, b) for a in range(5) for b in range(a, 5)
                     if self._bank_has_pair(a, b)]
        return acts

    # --------------------------------------------------------------- apply
    def apply(self, a):
        kind = a[0]
        p = self.actor()
        fn = getattr(self, "_do_" + kind, None)
        if fn is None:
            raise IllegalAction(f"unknown action {a}")
        if self.sim:
            fn(p, *a[1:])
        else:
            before = [h[:] for h in self.hands]
            self._stolen = None
            fn(p, *a[1:])
            self._track(before)
        if self.phase == SPECIAL and kind != "end":
            self._special_next()  # nothing left to build with: the phase moves on
        if self.phase != OVER and p == self.current and self.phase not in (SETUP_SETTLE, SETUP_ROAD):
            self._check_win(p)

    def _track(self, before):
        n = self.n
        delta = [[self.hands[q][r] - before[q][r] for r in range(5)] for q in range(n)]
        steal = self._stolen
        for v in range(n):
            row = self.belief[v]
            for q in range(n):
                if q == v:
                    row[q] = [float(x) for x in self.hands[q]]
                    continue
                if steal and v not in steal and q in steal:
                    continue  # v didn't see which card moved
                for r in range(5):
                    row[q][r] += delta[q][r]
            if steal and v not in steal:
                thief, victim = steal
                vb = row[victim]
                # stolen card ~ composition of the victim's hand as v believes it
                tot = sum(vb)
                frac = [x / tot for x in vb] if tot > 0 else [0.2] * 5
                for r in range(5):
                    vb[r] -= frac[r]
                    row[thief][r] += frac[r]
            for q in range(n):
                if q != v:
                    self._normalise(row[q], sum(self.hands[q]))

    @staticmethod
    def _normalise(b, total):
        for r in range(5):
            if b[r] < 1e-9:
                b[r] = 0.0
        s = sum(b)
        if total == 0:
            b[:] = [0.0] * 5
        elif s <= 0:
            b[:] = [total / 5.0] * 5
        elif abs(s - total) > 1e-9:
            k = total / s
            for r in range(5):
                b[r] *= k

    def _check_win(self, p):
        if self.vp(p) >= WIN_VP:
            self.winner = p
            self.phase = OVER
            self.log(f"{self.names[p]} WINS with {self.vp(p)} victory points!", "win", p)

    # setup + building -----------------------------------------------------
    def _do_settle(self, p, v):
        setup = self.phase == SETUP_SETTLE
        if setup:
            if not self.vertex_free(v):
                raise IllegalAction("spot not available (distance rule)")
        else:
            if self.phase not in (MAIN, SPECIAL):
                raise IllegalAction("can only build during your main phase")
            if v not in self.legal_settlements(p):
                raise IllegalAction("not a legal settlement spot (needs your road + distance rule)")
            if not self.can_afford(p, COSTS["settlement"]):
                raise IllegalAction("can't afford a settlement (wood, brick, sheep, wheat)")
            self.pay(p, COSTS["settlement"])
        self.v_owner[v] = p
        self.v_level[v] = 1
        self.settlements[p].append(v)
        self.log(f"{self.names[p]} builds a settlement at {v}", "build", p)
        if setup:
            self.last_settlement = v
            if self.setup_idx >= self.n:  # second settlement: collect resources
                got = []
                for h in self.board.vertex_hexes[v]:
                    r = self.board.hex_resource[h]
                    if r != DESERT and self.bank[r] > 0:
                        self.hands[p][r] += 1
                        self.bank[r] -= 1
                        got.append(RES_NAMES[r])
                if got:
                    self.log(f"{self.names[p]} collects {', '.join(got)}", "produce", p)
            self.phase = SETUP_ROAD
        else:
            self._update_longest()

    def _do_road(self, p, e):
        if not 0 <= e < self.board.E:
            raise IllegalAction(f"there is no edge {e}")
        if self.e_owner[e] != -1:
            raise IllegalAction("edge already has a road")
        if self.phase == SETUP_ROAD:
            if e not in self.legal_setup_roads(p):
                raise IllegalAction("setup road must touch the settlement you just placed")
        elif self.phase in (ROAD_BUILD, MAIN, SPECIAL):
            if e not in self.legal_roads(p):
                raise IllegalAction("road must connect to your network")
            if self.phase != ROAD_BUILD:
                if not self.can_afford(p, COSTS["road"]):
                    raise IllegalAction("can't afford a road (wood, brick)")
                self.pay(p, COSTS["road"])
        else:
            raise IllegalAction("can't build a road now")
        self.e_owner[e] = p
        self.roads[p].append(e)
        self.log(f"{self.names[p]} builds a road at {e}", "build", p)
        self._update_longest()
        if self.phase == SETUP_ROAD:
            self.setup_idx += 1
            if self.setup_idx >= len(self.setup_order):
                self.phase = PRE_ROLL
                self.current = 0
                self.turn = 1
                self.log(f"Setup complete. {self.names[0]} starts.", "turn")
            else:
                self.phase = SETUP_SETTLE
        elif self.phase == ROAD_BUILD:
            self.free_roads -= 1
            if self.free_roads <= 0 or not self.legal_roads(p):
                self.phase = self.robber_return

    def _do_end_road_building(self, p):
        if self.phase != ROAD_BUILD:
            raise IllegalAction("not building free roads")
        self.phase = self.robber_return

    def _do_city(self, p, v):
        if self.phase not in (MAIN, SPECIAL):
            raise IllegalAction("can only build during your main phase")
        if v not in self.settlements[p]:
            raise IllegalAction("cities upgrade one of your settlements")
        if len(self.cities[p]) >= MAX_CITIES:
            raise IllegalAction("no cities left")
        if not self.can_afford(p, COSTS["city"]):
            raise IllegalAction("can't afford a city (2 wheat, 3 ore)")
        self.pay(p, COSTS["city"])
        self.settlements[p].remove(v)
        self.cities[p].append(v)
        self.v_level[v] = 2
        self.log(f"{self.names[p]} upgrades {v} to a city", "build", p)

    def _do_buy_dev(self, p):
        if self.phase not in (MAIN, SPECIAL):
            raise IllegalAction("buy during your main phase")
        if not self.can_afford(p, COSTS["dev"]):
            raise IllegalAction("can't afford a development card (sheep, wheat, ore)")
        if self.deck_size() == 0:
            raise IllegalAction("development deck is empty")
        self.pay(p, COSTS["dev"])
        if self.sim:
            self.deck_len -= 1
            self.new_devs[p][UNKNOWN_DEV] += 1
        else:
            card = self.deck.pop()
            self.new_devs[p][card] += 1
            self.log(f"{self.names[p]} buys a development card", "dev", p)
            self.log(f"(you drew: {DEV_NAMES[card]})", "private", p)

    # dice -----------------------------------------------------------------
    def _do_roll(self, p, forced=None):
        if self.phase != PRE_ROLL:
            raise IllegalAction("already rolled")
        if forced is not None:
            total = forced
            d1 = d2 = None
        elif self.dice_bag is not None and self.dice_rng is not None:
            if len(self.dice_bag) < BALANCED_RESHUFFLE:
                self.dice_bag = [(a, b) for a in range(1, 7) for b in range(1, 7)]
                self.dice_rng.shuffle(self.dice_bag)
            d1, d2 = self.dice_bag.pop()
            total = d1 + d2
        else:
            rng = self.dice_rng or random
            d1 = rng.randint(1, 6)
            d2 = rng.randint(1, 6)
            total = d1 + d2
        self.last_roll = (d1, d2, total)
        self.roll_hist[total] += 1
        self.log(f"{self.names[p]} rolls {total}", "roll", p)
        if total == 7:
            self.discards = {}
            for q in range(self.n):
                hs = self.hand_size(q)
                if hs > 7:
                    self.discards[q] = hs // 2
            self.robber_return = MAIN
            self.phase = DISCARD if self.discards else ROBBER
        else:
            self._produce(total)
            self.phase = MAIN

    def _produce(self, total):
        b = self.board
        gains = [[0] * 5 for _ in range(self.n)]
        for h in range(b.H):
            if b.hex_number[h] != total or h == self.robber:
                continue
            r = b.hex_resource[h]
            for v in b.hex_vertices[h]:
                o = self.v_owner[v]
                if o != -1:
                    gains[o][r] += self.v_level[v]
        for r in range(5):
            want = sum(g[r] for g in gains)
            if want == 0:
                continue
            if want > self.bank[r]:
                takers = [q for q in range(self.n) if gains[q][r]]
                if len(takers) == 1:
                    gains[takers[0]][r] = self.bank[r]
                else:
                    for q in takers:
                        gains[q][r] = 0
                    self.log(f"Bank is short of {RES_NAMES[r]} - nobody receives it", "info")
            for q in range(self.n):
                self.hands[q][r] += gains[q][r]
                self.bank[r] -= gains[q][r]
        for q in range(self.n):
            if any(gains[q]):
                txt = ", ".join(f"{gains[q][r]} {RES_NAMES[r]}" for r in range(5) if gains[q][r])
                self.log(f"{self.names[q]} receives {txt}", "produce", q)

    def _do_discard(self, p, counts):
        if self.phase != DISCARD or self.discards.get(p, 0) == 0:
            raise IllegalAction("no discard owed")
        counts = list(counts)
        if sum(counts) != self.discards[p]:
            raise IllegalAction(f"must discard exactly {self.discards[p]} cards")
        if any(counts[i] < 0 or counts[i] > self.hands[p][i] for i in range(5)):
            raise IllegalAction("you don't have those cards")
        self.pay(p, counts)
        del self.discards[p]
        txt = ", ".join(f"{counts[r]} {RES_NAMES[r]}" for r in range(5) if counts[r])
        self.log(f"{self.names[p]} discards {txt}", "discard", p)
        if not self.discards:
            self.phase = ROBBER

    def _do_robber(self, p, h, victim=-1):
        if self.phase != ROBBER:
            raise IllegalAction("not moving the robber now")
        if h == self.robber:
            raise IllegalAction("robber must move to a different hex")
        vics = self.robber_targets(p, h)
        if vics and victim not in vics:
            raise IllegalAction("must steal from a player adjacent to that hex")
        self.robber = h
        self.log(f"{self.names[p]} moves the robber to {self.board.hex_label(h)}", "robber", p)
        if victim != -1 and vics:
            self._steal(p, victim)
        self.phase = self.robber_return

    def _steal(self, p, victim):
        hand = self.hands[victim]
        tot = sum(hand)
        if tot == 0:
            return
        if self.sim:
            # deterministic expectation proxy: take the victim's most common card
            r = max(range(5), key=lambda i: hand[i])
        else:
            k = self.steal_rng.randrange(tot)
            r = 0
            while k >= hand[r]:
                k -= hand[r]
                r += 1
        hand[r] -= 1
        self.hands[p][r] += 1
        self._stolen = (p, victim)
        self.log(f"{self.names[p]} steals a card from {self.names[victim]}", "steal", p)
        self.log(f"({self.names[p]} stole {RES_NAMES[r]} from {self.names[victim]})", "private_steal", (p, victim))

    # dev cards ------------------------------------------------------------
    def _start_dev(self, p, card):
        if self.phase not in (PRE_ROLL, MAIN):
            raise IllegalAction("can't play development cards now")
        if self.dev_played:
            raise IllegalAction("only one development card per turn")
        if self.devs[p][card] <= 0:
            if self.new_devs[p][card] > 0:
                raise IllegalAction("can't play a card on the turn you bought it")
            raise IllegalAction(f"you have no {DEV_NAMES[card]} card")
        self.devs[p][card] -= 1
        self.dev_played = True

    def _do_play_knight(self, p):
        self._start_dev(p, KNIGHT)
        self.knights[p] += 1
        self.log(f"{self.names[p]} plays a Knight ({self.knights[p]} total)", "dev", p)
        self._update_army()
        self.robber_return = self.phase
        self.phase = ROBBER

    def _do_play_road(self, p):
        if not self.legal_roads(p):
            raise IllegalAction("nowhere to build a road")
        self._start_dev(p, ROAD_BUILDING)
        self.log(f"{self.names[p]} plays Road Building", "dev", p)
        self.robber_return = self.phase
        self.free_roads = min(2, MAX_ROADS - len(self.roads[p]))
        self.phase = ROAD_BUILD

    def _do_play_mono(self, p, r):
        self._start_dev(p, MONOPOLY)
        total = 0
        took = []  # the cards are handed over in the open, so the log says who gave what
        for q in range(self.n):
            if q != p:
                if self.hands[q][r] and not self.sim:
                    took.append(f"{self.hands[q][r]} from {self.names[q]}")
                total += self.hands[q][r]
                self.hands[q][r] = 0
        if self.sim:
            total = int(round(total))  # simulations run on estimated (fractional) hands
        self.hands[p][r] += total
        self.log(f"{self.names[p]} plays Monopoly on {RES_NAMES[r]} and takes {total}"
                 + (f" ({', '.join(took)})" if took else ""), "dev", p)

    def _do_play_yop(self, p, r1, r2):
        if not self._bank_has_pair(r1, r2):
            raise IllegalAction("bank doesn't have those resources")
        self._start_dev(p, YEAR_OF_PLENTY)
        for r in (r1, r2):
            self.hands[p][r] += 1
            self.bank[r] -= 1
        self.log(f"{self.names[p]} plays Year of Plenty: {RES_NAMES[r1]} + {RES_NAMES[r2]}", "dev", p)

    # trading --------------------------------------------------------------
    def _do_bank(self, p, give, get):
        if self.phase != MAIN:
            raise IllegalAction("trade after rolling")
        ratio = self.trade_ratio(p, give)
        if self.hands[p][give] < ratio:
            raise IllegalAction(f"need {ratio} {RES_NAMES[give]} to trade")
        if give == get:
            raise IllegalAction("pointless trade")
        if self.bank[get] <= 0:
            raise IllegalAction(f"bank has no {RES_NAMES[get]}")
        self.hands[p][give] -= ratio
        self.bank[give] += ratio
        self.hands[p][get] += 1
        self.bank[get] -= 1
        self.log(f"{self.names[p]} trades {ratio} {RES_NAMES[give]} -> 1 {RES_NAMES[get]} with the bank", "trade", p)

    def _do_trade(self, p, partner, give, get):
        if self.phase != MAIN:
            raise IllegalAction("trade after rolling")
        if partner == p or not 0 <= partner < self.n:
            raise IllegalAction("pick another player to trade with")
        if min(*give, *get) < 0 or not any(give) or not any(get):
            raise IllegalAction("a trade needs cards on both sides")
        if not self.can_afford(p, give):
            raise IllegalAction("you don't have those cards")
        if not self.can_afford(partner, get):
            raise IllegalAction(f"{self.names[partner]} doesn't have those cards")
        for i in range(5):
            self.hands[p][i] += get[i] - give[i]
            self.hands[partner][i] += give[i] - get[i]
        self.trades_this_turn += 1
        self.log(f"{self.names[p]} trades {fmt_cards(give)} for {fmt_cards(get)} with {self.names[partner]}", "trade", p)

    def _do_end(self, p):
        if self.phase == SPECIAL:  # p is done building
            self.special_queue.pop(0)
            self._special_next()
            return
        if self.phase != MAIN:
            raise IllegalAction("roll first")
        self._age_devs(p)
        if self.special_build:
            self.special_queue = [(p + i) % self.n for i in range(1, self.n)]
            self.phase = SPECIAL
            self._special_next()
        else:
            self._next_turn()

    def _age_devs(self, p):
        """Cards bought earlier become playable."""
        for i in range(6):
            self.devs[p][i] += self.new_devs[p][i]
            self.new_devs[p][i] = 0

    def can_special_build(self, p):
        """Does p hold the cards for anything it has room to build?"""
        return ((self.can_afford(p, COSTS["road"]) and bool(self.legal_roads(p)))
                or (self.can_afford(p, COSTS["settlement"]) and bool(self.legal_settlements(p)))
                or (self.can_afford(p, COSTS["city"]) and bool(self.legal_cities(p)))
                or (self.can_afford(p, COSTS["dev"]) and self.deck_size() > 0))

    def _special_next(self):
        """Move on to the next player who can build something; then to the next turn."""
        while self.special_queue and not self.can_special_build(self.special_queue[0]):
            self.special_queue.pop(0)
        if not self.special_queue:
            self._next_turn()

    def _next_turn(self):
        self.current = (self.current + 1) % self.n
        self.turn += 1
        self.phase = PRE_ROLL
        self.dev_played = False
        self.trades_this_turn = 0
        self._age_devs(self.current)  # bought while others were building
        self.log(f"--- {self.names[self.current]}'s turn ---", "turn", self.current)
        # a player can only win on their own turn (e.g. longest road handed over)
        self._check_win(self.current)

    # awards ---------------------------------------------------------------
    def _update_army(self):
        best = self.army
        for q in range(self.n):
            k = self.knights[q]
            if k >= 3 and (best == -1 or k > self.knights[best]):
                best = q
        if best != self.army:
            self.army = best
            self.log(f"{self.names[best]} takes LARGEST ARMY", "award", best)

    def _update_longest(self):
        for q in range(self.n):
            self.road_len[q] = longest_road(self, q)
        holder = self.longest
        mx = max(self.road_len)
        if holder != -1 and self.road_len[holder] >= 5 and self.road_len[holder] >= mx:
            return
        cands = [q for q in range(self.n) if self.road_len[q] == mx and mx >= 5]
        new = cands[0] if len(cands) == 1 else -1
        if new != holder:
            self.longest = new
            if new != -1:
                self.log(f"{self.names[new]} takes LONGEST ROAD ({mx})", "award", new)
            elif holder != -1:
                self.log(f"{self.names[holder]} loses LONGEST ROAD", "award", holder)


def longest_road(s, p):
    edges = s.roads[p]
    if not edges:
        return 0
    b = s.board
    adj = {}
    for i, e in enumerate(edges):
        a, c = b.edge_vertices[e]
        adj.setdefault(a, []).append((i, c))
        adj.setdefault(c, []).append((i, a))
    owner = s.v_owner
    best = 0

    def dfs(v, used, length):
        nonlocal best
        if length > best:
            best = length
        if length and owner[v] != -1 and owner[v] != p:
            return
        for i, w in adj[v]:
            bit = 1 << i
            if not used & bit:
                dfs(w, used | bit, length + 1)

    for v in adj:
        dfs(v, 0, 0)
        if best == len(edges):
            break
    return best


def fmt_cards(c):
    parts = [f"{c[i]} {RES_NAMES[i]}" for i in range(5) if c[i]]
    return ", ".join(parts) if parts else "nothing"

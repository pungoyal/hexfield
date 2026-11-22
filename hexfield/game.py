"""Rules engine: game state, legal moves and state transitions.

Actions are plain tuples:
    ('settle', v) ('trail', e) ('town', v) ('buy_venture',)
    ('play_ranger',) ('play_pathfinder',) ('play_hoard', r) ('play_windfall', r1, r2)
    ('roll',) ('roll', total)            # forced total used by AI simulations / tests
    ('discard', (t, c, w, g, s))
    ('bandit', hex, victim)              # victim = -1 when nobody can be robbed
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
from .constants import (
    BADLANDS,
    BANK_START,
    COSTS,
    HOARD,
    LANDMARK,
    LARGE_BANK_START,
    LARGE_FROM,
    LARGE_VENTURE_DECK_COUNTS,
    MAX_TOWNS,
    MAX_TRAILS,
    MAX_VILLAGES,
    PATHFINDER,
    RANGER,
    RES_NAMES,
    UNKNOWN_VENTURE,
    VENTURE_DECK_COUNTS,
    VENTURE_NAMES,
    WIN_FAME,
    WINDFALL,
)

SETUP_SETTLE, SETUP_TRAIL = "setup_village", "setup_trail"
PRE_ROLL, MAIN, DISCARD, BANDIT, FREE_TRAILS, OVER = (
    "pre_roll",
    "main",
    "discard",
    "move_bandit",
    "free_trails",
    "game_over",
)
SPECIAL = "special_build"  # 5-6 players: after each turn the others may build (no trading)

DICE_MODES = ("random", "balanced")
LAYOUTS = ("random", "beginner")
# balanced dice: all 36 combinations in a deck; it is reshuffled when this few are left,
# so the last rolls of a deck can't be worked out by counting
BALANCED_RESHUFFLE = 13


class IllegalAction(Exception):
    pass


class GameState:
    def __init__(
        self, n_players=4, seed=None, board=None, names=None, dice="random", layout="random", special_build=None
    ):
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
        self.venture_counts = LARGE_VENTURE_DECK_COUNTS if large else VENTURE_DECK_COUNTS
        self.bank_start = LARGE_BANK_START if large else BANK_START
        self.names = names or [f"P{i}" for i in range(n_players)]
        # independent random streams so a replay with the same seed gets the same
        # dice / deck even if decisions differ
        self.dice_rng = random.Random(master.randrange(1 << 30))
        self.steal_rng = random.Random(master.randrange(1 << 30))
        deck_rng = random.Random(master.randrange(1 << 30))
        self.deck = []
        for t, c in enumerate(self.venture_counts):
            self.deck += [t] * c
        deck_rng.shuffle(self.deck)

        self.hands = [[0] * 5 for _ in range(n_players)]
        self.ventures = [[0] * 6 for _ in range(n_players)]  # playable (owned before this turn)
        self.new_ventures = [[0] * 6 for _ in range(n_players)]  # bought this turn
        self.rangers = [0] * n_players
        self.v_owner = [-1] * b.V
        self.v_level = [0] * b.V
        self.e_owner = [-1] * b.E
        self.villages = [[] for _ in range(n_players)]
        self.towns = [[] for _ in range(n_players)]
        self.trails = [[] for _ in range(n_players)]
        self.trail_len = [0] * n_players
        self.bank = [self.bank_start] * 5
        self.bandit = b.badlands
        self.longest = -1
        self.patrol = -1

        self.setup_order = list(range(n_players)) + list(reversed(range(n_players)))
        self.setup_idx = 0
        self.last_village = -1
        self.current = 0
        self.phase = SETUP_SETTLE
        self.turn = 0  # increments every time a turn ends
        self.venture_played = False
        self.discards = {}  # player -> cards owed
        self.bandit_return = MAIN
        self.free_trails = 0
        self.last_roll = None
        self.winner = -1
        self.sim = False  # True inside AI simulations (hides deck)
        self.events = []  # human readable log for the UI
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
        c.ventures = [d[:] for d in self.ventures]
        c.new_ventures = [d[:] for d in self.new_ventures]
        c.rangers = self.rangers[:]
        c.v_owner = self.v_owner[:]
        c.v_level = self.v_level[:]
        c.e_owner = self.e_owner[:]
        c.villages = [s[:] for s in self.villages]
        c.towns = [s[:] for s in self.towns]
        c.trails = [s[:] for s in self.trails]
        c.trail_len = self.trail_len[:]
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
        if self.phase in (SETUP_SETTLE, SETUP_TRAIL):
            return self.setup_order[self.setup_idx]
        if self.phase == SPECIAL:
            return self.special_queue[0]
        return self.current

    def hand_size(self, p):
        return sum(self.hands[p])

    def venture_count(self, p):
        return sum(self.ventures[p]) + sum(self.new_ventures[p])

    def landmarks(self, p):
        return self.ventures[p][LANDMARK] + self.new_ventures[p][LANDMARK]

    def public_fame(self, p):
        fame = len(self.villages[p]) + 2 * len(self.towns[p])
        if self.longest == p:
            fame += 2
        if self.patrol == p:
            fame += 2
        return fame

    def fame(self, p):
        return self.public_fame(p) + self.landmarks(p)

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
        for v in self.villages[p] + self.towns[p]:
            dock = self.board.vertex_dock[v]
            if dock == r:
                return 2
            if dock == "any":
                best = 3
        return best

    def ratios(self, p):
        return [self.trade_ratio(p, r) for r in range(5)]

    # ------------------------------------------------------- placement rules
    def vertex_free(self, v):
        """Unoccupied and satisfies the distance rule."""
        return self.v_owner[v] == -1 and all(self.v_owner[w] == -1 for w in self.board.vertex_neighbors[v])

    def touches_own_trail(self, p, v):
        return any(self.e_owner[e] == p for e in self.board.vertex_edges[v])

    def legal_villages(self, p, setup=False):
        if len(self.villages[p]) >= MAX_VILLAGES:
            return []
        out = []
        for v in range(self.board.V):
            if self.vertex_free(v) and (setup or self.touches_own_trail(p, v)):
                out.append(v)
        return out

    def legal_trails(self, p):
        if len(self.trails[p]) >= MAX_TRAILS:
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

    def legal_setup_trails(self, p):
        return [e for e in self.board.vertex_edges[self.last_village] if self.e_owner[e] == -1]

    def legal_towns(self, p):
        if len(self.towns[p]) >= MAX_TOWNS:
            return []
        return list(self.villages[p])

    def bandit_targets(self, p, h):
        vics = set()
        for v in self.board.hex_vertices[h]:
            o = self.v_owner[v]
            if o != -1 and o != p and self.hand_size(o) > 0:
                vics.add(o)
        return sorted(vics)

    def _bank_has_pair(self, r1, r2):
        """Can the bank hand out one r1 and one r2 (Windfall)?"""
        return self.bank[r1] >= 1 + (r1 == r2) and self.bank[r2] >= 1

    def playable(self, p, card):
        return (not self.venture_played) and self.ventures[p][card] > 0 and card != LANDMARK

    # ------------------------------------------------------------ legality
    def legal_actions(self, include_trades=True):
        """Enumerate legal actions (discards excluded - they're combinatorial)."""
        p = self.actor()
        ph = self.phase
        if ph == SETUP_SETTLE:
            return [("settle", v) for v in self.legal_villages(p, setup=True)]
        if ph == SETUP_TRAIL:
            return [("trail", e) for e in self.legal_setup_trails(p)]
        if ph == BANDIT:
            acts = []
            for h in range(self.board.H):
                if h == self.bandit:
                    continue
                vics = self.bandit_targets(p, h)
                if vics:
                    acts += [("bandit", h, v) for v in vics]
                else:
                    acts.append(("bandit", h, -1))
            return acts
        if ph == FREE_TRAILS:
            acts = [("trail", e) for e in self.legal_trails(p)]
            return acts or [("end_free_trails",)]
        if ph in (DISCARD, OVER):
            return []
        acts = [] if ph == SPECIAL else self._venture_actions(p)
        if ph == PRE_ROLL:
            acts.append(("roll",))
            return acts
        # MAIN
        if self.can_afford(p, COSTS["village"]):
            acts += [("settle", v) for v in self.legal_villages(p)]
        if self.can_afford(p, COSTS["town"]):
            acts += [("town", v) for v in self.legal_towns(p)]
        if self.can_afford(p, COSTS["trail"]):
            acts += [("trail", e) for e in self.legal_trails(p)]
        if self.can_afford(p, COSTS["venture"]) and self.deck_size() > 0:
            acts.append(("buy_venture",))
        if include_trades and ph == MAIN:
            for give in range(5):
                ratio = self.trade_ratio(p, give)
                if self.hands[p][give] >= ratio:
                    for get in range(5):
                        if get != give and self.bank[get] > 0:
                            acts.append(("bank", give, get))
        acts.append(("end",))
        return acts

    def _venture_actions(self, p):
        acts = []
        if self.venture_played:
            return acts
        d = self.ventures[p]
        if d[RANGER]:
            acts.append(("play_ranger",))
        if d[PATHFINDER] and self.legal_trails(p):
            acts.append(("play_pathfinder",))
        if d[HOARD]:
            acts += [("play_hoard", r) for r in range(5)]
        if d[WINDFALL]:
            acts += [("play_windfall", a, b) for a in range(5) for b in range(a, 5) if self._bank_has_pair(a, b)]
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
        if self.phase != OVER and p == self.current and self.phase not in (SETUP_SETTLE, SETUP_TRAIL):
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
        if self.fame(p) >= WIN_FAME:
            self.winner = p
            self.phase = OVER
            self.log(f"{self.names[p]} WINS with {self.fame(p)} fame!", "win", p)

    # setup + building -----------------------------------------------------
    def _do_settle(self, p, v):
        setup = self.phase == SETUP_SETTLE
        if setup:
            if not self.vertex_free(v):
                raise IllegalAction("spot not available (distance rule)")
        else:
            if self.phase not in (MAIN, SPECIAL):
                raise IllegalAction("can only build during your main phase")
            if v not in self.legal_villages(p):
                raise IllegalAction("not a legal village spot (needs your trail + distance rule)")
            if not self.can_afford(p, COSTS["village"]):
                raise IllegalAction("can't afford a village (timber, clay, wool, grain)")
            self.pay(p, COSTS["village"])
        self.v_owner[v] = p
        self.v_level[v] = 1
        self.villages[p].append(v)
        self.log(f"{self.names[p]} builds a village at {v}", "build", p)
        if setup:
            self.last_village = v
            if self.setup_idx >= self.n:  # second village: collect resources
                got = []
                for h in self.board.vertex_hexes[v]:
                    r = self.board.hex_resource[h]
                    if r != BADLANDS and self.bank[r] > 0:
                        self.hands[p][r] += 1
                        self.bank[r] -= 1
                        got.append(RES_NAMES[r])
                if got:
                    self.log(f"{self.names[p]} collects {', '.join(got)}", "produce", p)
            self.phase = SETUP_TRAIL
        else:
            self._update_longest()

    def _do_trail(self, p, e):
        if not 0 <= e < self.board.E:
            raise IllegalAction(f"there is no edge {e}")
        if self.e_owner[e] != -1:
            raise IllegalAction("edge already has a trail")
        if self.phase == SETUP_TRAIL:
            if e not in self.legal_setup_trails(p):
                raise IllegalAction("setup trail must touch the village you just placed")
        elif self.phase in (FREE_TRAILS, MAIN, SPECIAL):
            if e not in self.legal_trails(p):
                raise IllegalAction("trail must connect to your network")
            if self.phase != FREE_TRAILS:
                if not self.can_afford(p, COSTS["trail"]):
                    raise IllegalAction("can't afford a trail (timber, clay)")
                self.pay(p, COSTS["trail"])
        else:
            raise IllegalAction("can't build a trail now")
        self.e_owner[e] = p
        self.trails[p].append(e)
        self.log(f"{self.names[p]} builds a trail at {e}", "build", p)
        self._update_longest()
        if self.phase == SETUP_TRAIL:
            self.setup_idx += 1
            if self.setup_idx >= len(self.setup_order):
                self.phase = PRE_ROLL
                self.current = 0
                self.turn = 1
                self.log(f"Setup complete. {self.names[0]} starts.", "turn")
            else:
                self.phase = SETUP_SETTLE
        elif self.phase == FREE_TRAILS:
            self.free_trails -= 1
            if self.free_trails <= 0 or not self.legal_trails(p):
                self.phase = self.bandit_return

    def _do_end_free_trails(self, p):
        if self.phase != FREE_TRAILS:
            raise IllegalAction("not building free trails")
        self.phase = self.bandit_return

    def _do_town(self, p, v):
        if self.phase not in (MAIN, SPECIAL):
            raise IllegalAction("can only build during your main phase")
        if v not in self.villages[p]:
            raise IllegalAction("towns upgrade one of your villages")
        if len(self.towns[p]) >= MAX_TOWNS:
            raise IllegalAction("no towns left")
        if not self.can_afford(p, COSTS["town"]):
            raise IllegalAction("can't afford a town (2 grain, 3 stone)")
        self.pay(p, COSTS["town"])
        self.villages[p].remove(v)
        self.towns[p].append(v)
        self.v_level[v] = 2
        self.log(f"{self.names[p]} upgrades {v} to a town", "build", p)

    def _do_buy_venture(self, p):
        if self.phase not in (MAIN, SPECIAL):
            raise IllegalAction("buy during your main phase")
        if not self.can_afford(p, COSTS["venture"]):
            raise IllegalAction("can't afford a venture (wool, grain, stone)")
        if self.deck_size() == 0:
            raise IllegalAction("venture deck is empty")
        self.pay(p, COSTS["venture"])
        if self.sim:
            self.deck_len -= 1
            self.new_ventures[p][UNKNOWN_VENTURE] += 1
        else:
            card = self.deck.pop()
            self.new_ventures[p][card] += 1
            self.log(f"{self.names[p]} buys a venture", "venture", p)
            self.log(f"(you drew: {VENTURE_NAMES[card]})", "private", p)

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
            self.bandit_return = MAIN
            self.phase = DISCARD if self.discards else BANDIT
        else:
            self._produce(total)
            self.phase = MAIN

    def _produce(self, total):
        b = self.board
        gains = [[0] * 5 for _ in range(self.n)]
        for h in range(b.H):
            if b.hex_number[h] != total or h == self.bandit:
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
            self.phase = BANDIT

    def _do_bandit(self, p, h, victim=-1):
        if self.phase != BANDIT:
            raise IllegalAction("not moving the bandit now")
        if h == self.bandit:
            raise IllegalAction("bandit must move to a different hex")
        vics = self.bandit_targets(p, h)
        if vics and victim not in vics:
            raise IllegalAction("must steal from a player adjacent to that hex")
        self.bandit = h
        self.log(f"{self.names[p]} moves the bandit to {self.board.hex_label(h)}", "bandit", p)
        if victim != -1 and vics:
            self._steal(p, victim)
        self.phase = self.bandit_return

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

    # ventures ------------------------------------------------------------
    def _start_venture(self, p, card):
        if self.phase not in (PRE_ROLL, MAIN):
            raise IllegalAction("can't play ventures now")
        if self.venture_played:
            raise IllegalAction("only one venture per turn")
        if self.ventures[p][card] <= 0:
            if self.new_ventures[p][card] > 0:
                raise IllegalAction("can't play a card on the turn you bought it")
            raise IllegalAction(f"you have no {VENTURE_NAMES[card]} card")
        self.ventures[p][card] -= 1
        self.venture_played = True

    def _do_play_ranger(self, p):
        self._start_venture(p, RANGER)
        self.rangers[p] += 1
        self.log(f"{self.names[p]} plays a Ranger ({self.rangers[p]} total)", "venture", p)
        self._update_patrol()
        self.bandit_return = self.phase
        self.phase = BANDIT

    def _do_play_pathfinder(self, p):
        if not self.legal_trails(p):
            raise IllegalAction("nowhere to build a trail")
        self._start_venture(p, PATHFINDER)
        self.log(f"{self.names[p]} plays Pathfinder", "venture", p)
        self.bandit_return = self.phase
        self.free_trails = min(2, MAX_TRAILS - len(self.trails[p]))
        self.phase = FREE_TRAILS

    def _do_play_hoard(self, p, r):
        self._start_venture(p, HOARD)
        total = 0
        took = []  # the cards are handed over in the open, so the log says who gave what
        for q in range(self.n):
            if q != p:
                if self.hands[q][r] and not self.sim:
                    took.append(f"{self.hands[q][r]} from {self.names[q]}")
                total += self.hands[q][r]
                self.hands[q][r] = 0
        if self.sim:
            total = round(total)  # simulations run on estimated (fractional) hands
        self.hands[p][r] += total
        self.log(
            f"{self.names[p]} plays Hoard on {RES_NAMES[r]} and takes {total}"
            + (f" ({', '.join(took)})" if took else ""),
            "venture",
            p,
        )

    def _do_play_windfall(self, p, r1, r2):
        if not self._bank_has_pair(r1, r2):
            raise IllegalAction("bank doesn't have those resources")
        self._start_venture(p, WINDFALL)
        for r in (r1, r2):
            self.hands[p][r] += 1
            self.bank[r] -= 1
        self.log(f"{self.names[p]} plays Windfall: {RES_NAMES[r1]} + {RES_NAMES[r2]}", "venture", p)

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
        self.log(
            f"{self.names[p]} trades {fmt_cards(give)} for {fmt_cards(get)} with {self.names[partner]}", "trade", p
        )

    def _do_end(self, p):
        if self.phase == SPECIAL:  # p is done building
            self.special_queue.pop(0)
            self._special_next()
            return
        if self.phase != MAIN:
            raise IllegalAction("roll first")
        self._age_ventures(p)
        if self.special_build:
            self.special_queue = [(p + i) % self.n for i in range(1, self.n)]
            self.phase = SPECIAL
            self._special_next()
        else:
            self._next_turn()

    def _age_ventures(self, p):
        """Cards bought earlier become playable."""
        for i in range(6):
            self.ventures[p][i] += self.new_ventures[p][i]
            self.new_ventures[p][i] = 0

    def can_special_build(self, p):
        """Does p hold the cards for anything it has room to build?"""
        return (
            (self.can_afford(p, COSTS["trail"]) and bool(self.legal_trails(p)))
            or (self.can_afford(p, COSTS["village"]) and bool(self.legal_villages(p)))
            or (self.can_afford(p, COSTS["town"]) and bool(self.legal_towns(p)))
            or (self.can_afford(p, COSTS["venture"]) and self.deck_size() > 0)
        )

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
        self.venture_played = False
        self.trades_this_turn = 0
        self._age_ventures(self.current)  # bought while others were building
        self.log(f"--- {self.names[self.current]}'s turn ---", "turn", self.current)
        # a player can only win on their own turn (e.g. great trail handed over)
        self._check_win(self.current)

    # awards ---------------------------------------------------------------
    def _update_patrol(self):
        best = self.patrol
        for q in range(self.n):
            k = self.rangers[q]
            if k >= 3 and (best == -1 or k > self.rangers[best]):
                best = q
        if best != self.patrol:
            self.patrol = best
            self.log(f"{self.names[best]} takes LARGEST PATROL", "award", best)

    def _update_longest(self):
        for q in range(self.n):
            self.trail_len[q] = great_trail(self, q)
        holder = self.longest
        mx = max(self.trail_len)
        if holder != -1 and self.trail_len[holder] >= 5 and self.trail_len[holder] >= mx:
            return
        cands = [q for q in range(self.n) if self.trail_len[q] == mx and mx >= 5]
        new = cands[0] if len(cands) == 1 else -1
        if new != holder:
            self.longest = new
            if new != -1:
                self.log(f"{self.names[new]} takes GREAT TRAIL ({mx})", "award", new)
            elif holder != -1:
                self.log(f"{self.names[holder]} loses GREAT TRAIL", "award", holder)


def great_trail(s, p):
    edges = s.trails[p]
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
        best = max(best, length)
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

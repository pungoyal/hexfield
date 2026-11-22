"""Machine opponents.

The engine is an *expert evaluation function* plus *search*:

* `Evaluator.values()` scores every player's position in "fame
  equivalents": real fame, expected production (bandit-aware, with diminishing
  returns, dock conversion and resource diversity), expansion potential
  (reachable open spots, contested-spot discount), hand quality (cards that
  complete builds, 7-roll exposure), ventures and the race for
  Great Trail / Great Patrol.
* `score()` turns that into a decision score: my value minus a weighted share
  of opponents' values, with weights that grow for the leader and anyone close
  to winning (so the bots gang up on whoever is ahead - including you).
* `plan_turn()` runs a beam search over sequences of builds, bank/dock trades
  and venture plays inside the current turn.
* Opening placement uses a snake-draft lookahead: for every candidate first
  village it simulates the opponents' greedy picks until the bot's second
  pick, then takes the best pairing.
* Bandit, discards, rangers-before-roll and player trades are all decided by
  the same evaluation, so the bots never accept a trade that helps you more
  than it helps them.

Hidden information: every decision is made on a *view* of the game in which
opponents' resource hands are replaced by what the deciding player can know
from public events (see GameState.belief) - production, builds, trades and
discards are exact, steals between other players are tracked in expectation.
Other players' ventures and the deck order are never visible.
"""

import math
import random
from collections import deque

from .constants import (
    BADLANDS,
    COSTS,
    HOARD,
    LANDMARK,
    MAX_TOWNS,
    MAX_VILLAGES,
    PATHFINDER,
    RANGER,
    RES_NAMES,
    UNKNOWN_VENTURE,
    WIN_FAME,
    WINDFALL,
)
from .game import BANDIT, DISCARD, FREE_TRAILS, MAIN, PRE_ROLL, SETUP_SETTLE, SETUP_TRAIL, SPECIAL, fmt_cards

SETUP_W = (1.10, 1.10, 0.80, 1.00, 0.87)
DIST_DISC = (0.55, 0.38, 0.22)
TRAIL_SETTLE = (2, 2, 1, 1, 0)

# Every weight of the evaluation, in one place: tools/tune.py searches over these and the
# play styles below are small changes to them. Values are fame-equivalents unless noted.
PARAMS = {
    "pip_value": 0.105,  # one pip of production (a card per 36 rolls)
    "w_timber_early": 1.15,
    "w_clay_early": 1.15,
    "w_wool_early": 0.80,
    "w_grain_early": 1.00,
    "w_stone_early": 0.80,
    "w_timber_late": 0.70,
    "w_clay_late": 0.70,
    "w_wool_late": 0.70,
    "w_grain_late": 1.15,
    "w_stone_late": 1.25,
    "missing_res": 3.2,  # pips docked for a resource you don't produce
    "expansion": 1.0,  # scale of the open-spots term
    "dock_any": 1.2,  # a 3:1 dock on an open spot (pips)
    "hand_card": 0.09,  # a loose card, divided by its trade ratio
    "hand_build": 0.40,  # scale of the "close to a build" bonus
    "util_village": 1.5,
    "util_trail_settle": 1.25,
    "util_town": 1.45,
    "util_venture": 0.5,
    "util_trail": 0.3,
    "seven_risk": 0.13,  # per card a 7 would cost, times the chance of a 7
    "venture_ranger": 0.33,
    "venture_trail": 0.42,
    "venture_hoard": 0.45,
    "venture_windfall": 0.55,
    "venture_hidden": 0.30,  # an opponent's face-down card
    "patrol_ranger": 0.1,
    "patrol_reach": 0.55,
    "patrol_near": 0.2,
    "award_threat": 0.3,
    "trail_race": 0.75,
    "opp_base": 0.28,
    "opp_lead": 0.25,
    "opp_8": 0.35,
    "opp_9": 0.4,  # how much rivals' values count against you
    "trade_margin": 0.05,  # a trade must gain the responder this much
    "propose_gain": 0.25,  # a trade must gain the proposer this much to be offered
}

# Play styles: the same engine with different tastes. Factors multiply PARAMS.
STYLES = {
    "balanced": {},
    "builder": {  # trails and villages, the Great Trail
        "w_timber_early": 1.12,
        "w_clay_early": 1.12,
        "w_timber_late": 1.2,
        "w_clay_late": 1.2,
        "expansion": 1.35,
        "trail_race": 1.6,
        "util_village": 1.15,
        "util_trail": 1.6,
    },
    "citizen": {  # grain and stone: towns first
        "w_grain_early": 1.15,
        "w_stone_early": 1.25,
        "w_grain_late": 1.08,
        "w_stone_late": 1.08,
        "util_town": 1.25,
        "expansion": 0.85,
    },
    "general": {  # ventures and the Great Patrol
        "w_wool_early": 1.15,
        "w_wool_late": 1.25,
        "util_venture": 2.0,
        "venture_ranger": 1.3,
        "venture_hoard": 1.2,
        "venture_windfall": 1.2,
        "patrol_ranger": 1.6,
        "patrol_reach": 1.5,
        "patrol_near": 1.5,
    },
    "trader": {  # docks and deals
        "dock_any": 2.2,
        "hand_card": 1.25,
        "trade_margin": 0.4,
        "propose_gain": 0.5,
        "missing_res": 0.8,
    },
}
STYLE_NOTES = {
    "balanced": "plays the position",
    "builder": "expands fast and races for the Great Trail",
    "citizen": "wants grain and stone, and builds towns early",
    "general": "buys ventures and goes for the Great Patrol",
    "trader": "settles on docks and trades often",
}


def make_params(style="balanced", overrides=None):
    P = dict(PARAMS)
    for k, f in STYLES[style].items():
        P[k] *= f
    if overrides:
        unknown = set(overrides) - set(P)
        if unknown:
            raise KeyError(f"unknown evaluation parameters: {sorted(unknown)}")
        P.update(overrides)
    return P


BUILD_ACTION = {"town": "town", "village": "settle", "trail": "trail", "venture": "buy_venture"}

LEVELS = {
    # beam width, depth, score noise, setup lookahead
    "easy": {"beam": 1, "depth": 1, "noise": 0.9, "lookahead": False},
    "medium": {"beam": 2, "depth": 2, "noise": 0.35, "lookahead": False},
    "hard": {"beam": 5, "depth": 4, "noise": 0.0, "lookahead": True},
}


def clamp(x, lo, hi):
    return lo if x < lo else min(x, hi)


class Evaluator:
    def __init__(self, board, params=None):
        self.b = board
        self.P = P = params or PARAMS
        self.w_early = tuple(P[f"w_{r}_early"] for r in RES_NAMES)
        self.w_late = tuple(P[f"w_{r}_late"] for r in RES_NAMES)
        self.venture_value = {
            RANGER: P["venture_ranger"],
            PATHFINDER: P["venture_trail"],
            HOARD: P["venture_hoard"],
            WINDFALL: P["venture_windfall"],
        }

    # ------------------------------------------------------------- helpers
    def production(self, s, q):
        b = self.b
        prod = [0.0] * 5
        for lvl, verts in ((1, s.villages[q]), (2, s.towns[q])):
            for v in verts:
                for h in b.vertex_hexes[v]:
                    if h == s.bandit:
                        continue
                    r = b.hex_resource[h]
                    if r != BADLANDS:
                        prod[r] += b.hex_pips[h] * lvl
        return prod

    def blocked_pips(self, s, q):
        h = s.bandit
        b = self.b
        if b.hex_resource[h] == BADLANDS:
            return 0
        tot = 0
        for v in b.hex_vertices[h]:
            if s.v_owner[v] == q:
                tot += b.hex_pips[h] * s.v_level[v]
        return tot

    def reach(self, s, q):
        """dist[v] = trails needed to reach v from q's network (0..2), else 9."""
        b = self.b
        dist = [9] * b.V
        dq = deque()
        for e in s.trails[q]:
            for v in b.edge_vertices[e]:
                if dist[v]:
                    dist[v] = 0
                    dq.append(v)
        for v in s.villages[q] + s.towns[q]:
            if dist[v]:
                dist[v] = 0
                dq.append(v)
        own = s.v_owner
        eown = s.e_owner
        while dq:
            v = dq.popleft()
            d = dist[v]
            if d >= 2:
                continue
            if own[v] != -1 and own[v] != q:
                continue
            for e in b.vertex_edges[v]:
                if eown[e] != -1:
                    continue
                a, c = b.edge_vertices[e]
                w = c if a == v else a
                if dist[w] > d + 1:
                    dist[w] = d + 1
                    dq.append(w)
        return dist

    def vertex_quality(self, s, v, prod, weights):
        b = self.b
        val = 0.0
        for h in b.vertex_hexes[v]:
            if h == s.bandit:
                continue
            r = b.hex_resource[h]
            if r == BADLANDS:
                continue
            pip = b.hex_pips[h]
            val += weights[r] * b.scarcity[r] * pip
            if prod[r] == 0:
                val += 1.8
        dock = b.vertex_dock[v]
        if dock == "any":
            val += self.P["dock_any"]
        elif dock is not None and prod[dock] >= 5:
            val += 0.35 * prod[dock]
        return val

    # -------------------------------------------------------------- values
    def values(self, s, viewer):
        """Position value of every player as seen by `viewer`."""
        n = s.n
        b = self.b
        P = self.P
        W_EARLY, W_LATE, VENTURE_VALUE = self.w_early, self.w_late, self.venture_value
        PIP_VALUE = P["pip_value"]
        counts = s.venture_counts
        left = sum(counts) - counts[LANDMARK]
        # an unseen card: a landmark or, in proportion, a ranger or a progress card
        unknown_venture = (
            counts[LANDMARK] * 1.0
            + counts[RANGER] * VENTURE_VALUE[RANGER]
            + counts[PATHFINDER] * VENTURE_VALUE[PATHFINDER]
            + counts[HOARD] * VENTURE_VALUE[HOARD]
            + counts[WINDFALL] * VENTURE_VALUE[WINDFALL]
        ) / sum(counts)
        p_ranger = counts[RANGER] / float(left + counts[LANDMARK])
        if s.winner != -1:
            return [1000.0 if q == s.winner else 0.0 for q in range(n)]
        # hidden-information estimates
        own_landmarks = s.landmarks(viewer)
        unknown = s.deck_size() + sum(s.venture_count(q) for q in range(n) if q != viewer)
        p_fame = (counts[LANDMARK] - own_landmarks) / unknown if unknown else 0.0
        p_fame = clamp(p_fame, 0.0, 1.0)

        est_fame = []
        for q in range(n):
            if q == viewer:
                est_fame.append(s.fame(q))
            else:
                est_fame.append(s.public_fame(q) + p_fame * s.venture_count(q))
        top = max(est_fame)
        horizon = clamp((WIN_FAME - top) / 8.0, 0.2, 1.0)

        free = [s.vertex_free(v) for v in range(b.V)]
        reaches = [self.reach(s, q) for q in range(n)]
        p7 = clamp(0.155 * (n - 1), 0.0, 0.6)

        out = []
        for q in range(n):
            t = clamp((s.public_fame(q) - 2) / 6.0, 0.0, 1.0)
            w = [W_EARLY[r] * (1 - t) + W_LATE[r] * t for r in range(5)]
            sett_left = MAX_VILLAGES - len(s.villages[q])
            town_left = MAX_TOWNS - len(s.towns[q])
            prod = self.production(s, q)
            ratios = s.ratios(q)

            # --- expansion potential
            dist = reaches[q]
            spots = []
            if sett_left > 0:
                for v in range(b.V):
                    if free[v] and dist[v] <= 2:
                        qv = self.vertex_quality(s, v, prod, w)
                        disc = DIST_DISC[dist[v]]
                        for o in range(n):
                            if o != q:
                                od = reaches[o][v]
                                if od < dist[v]:
                                    disc *= 0.45
                                    break
                                if od == dist[v] and od <= 1:
                                    disc *= 0.75
                        spots.append((qv * disc, dist[v]))
                spots.sort(reverse=True)
            exp_val = P["expansion"] * PIP_VALUE * horizon * sum(x for x, _ in spots[: min(2, sett_left)])
            has_spot0 = any(d == 0 for _, d in spots[:3])
            has_spot = bool(spots)
            if not has_spot:
                w[0] *= 0.55
                w[1] *= 0.55

            # --- production with diminishing returns
            pv = 0.0
            for r in range(5):
                x = prod[r]
                cap = 7.0 + (5.0 * t if r in (3, 4) else 0.0)
                if x > cap:
                    k = 0.92 if ratios[r] == 2 else 0.72 if ratios[r] == 3 else 0.58
                    x = cap + (x - cap) * k
                pv += w[r] * b.scarcity[r] * x
                if prod[r] == 0:
                    pv -= P["missing_res"] * w[r] * (0.5 if 2 in ratios else 1.0)
            prod_val = PIP_VALUE * horizon * pv
            if town_left == 0 and sett_left == 0:
                prod_val *= 0.6

            # --- hand: raw card value + bonus for being close to a build
            h = s.hands[q]
            hs = sum(h)
            hv = 0.0
            for r in range(5):
                hv += h[r] * max(0.03, P["hand_card"] / ratios[r])
            targets = []
            if sett_left > 0 and has_spot0:
                targets.append((COSTS["village"], P["util_village"]))
            elif sett_left > 0 and has_spot:
                targets.append((TRAIL_SETTLE, P["util_trail_settle"]))
            if town_left > 0 and s.villages[q]:
                targets.append((COSTS["town"], P["util_town"]))
            if s.deck_size() > 0:
                targets.append((COSTS["venture"], P["util_venture"]))
            if has_spot and not has_spot0:
                targets.append((COSTS["trail"], P["util_trail"]))
            bonuses = []
            for cost, util in targets:
                have = 0
                deficit = 0
                trade_units = 0
                for r in range(5):
                    c = cost[r]
                    if h[r] >= c:
                        have += c
                        trade_units += (h[r] - c) // ratios[r]
                    else:
                        have += h[r]
                        deficit += c - h[r]
                have += min(deficit, trade_units)
                frac = have / float(sum(cost))
                bonuses.append(util * frac * frac * P["hand_build"])
            if bonuses:
                bonuses.sort(reverse=True)
                hv += bonuses[0] + (0.3 * bonuses[1] if len(bonuses) > 1 else 0.0)
            if hs > 7:
                hv -= p7 * (hs // 2) * P["seven_risk"]

            # --- ventures
            dv = 0.0
            if q == viewer:
                for card, val in VENTURE_VALUE.items():
                    c = s.ventures[q][card] + s.new_ventures[q][card]
                    if c:
                        if card == PATHFINDER and not has_spot:
                            val *= 0.5
                        if card == RANGER:
                            val += 0.012 * self.blocked_pips(s, q)
                        dv += c * val
                unk = s.new_ventures[q][UNKNOWN_VENTURE] + s.ventures[q][UNKNOWN_VENTURE]
                dv += unk * unknown_venture
                rangers_in_hand = s.ventures[q][RANGER] + s.new_ventures[q][RANGER] + p_ranger * unk
            else:
                hidden = s.venture_count(q)
                dv += hidden * P["venture_hidden"]
                rangers_in_hand = hidden * p_ranger

            # --- great patrol race
            av = 0.0
            best_other = max((s.rangers[o] for o in range(n) if o != q), default=0)
            if s.patrol != q:
                k = s.rangers[q]
                needk = max(3, best_other + 1)
                av += P["patrol_ranger"] * k
                if k + rangers_in_hand >= needk:
                    av += P["patrol_reach"]
                elif k + rangers_in_hand + 1 >= needk:
                    av += P["patrol_near"]
            elif best_other >= s.rangers[q] - 1:
                av -= P["award_threat"]

            # --- great trail race
            rv = 0.0
            L = s.trail_len[q]
            if s.longest != q:
                holder_len = s.trail_len[s.longest] if s.longest != -1 else 4
                target = max(5, holder_len + 1)
                if L >= 3:
                    rv += P["trail_race"] * (min(L, target) / target) ** 3
            elif max((s.trail_len[o] for o in range(n) if o != q), default=0) >= L - 1:
                rv -= P["award_threat"]

            out.append(est_fame[q] + prod_val + exp_val + hv + dv + av + rv)
        return out

    def score(self, s, p):
        if s.winner != -1:
            return 1000.0 if s.winner == p else -1000.0
        vals = self.values(s, p)
        opp = [q for q in range(s.n) if q != p]
        lead = max(opp, key=lambda q: vals[q])
        P = self.P
        sc = vals[p]
        # the same total pressure at any table size (the weights were set for four players)
        k = 3.0 / len(opp) if len(opp) > 3 else 1.0
        for q in opp:
            wgt = P["opp_base"] * k
            if q == lead:
                wgt += P["opp_lead"]
            pv = s.public_fame(q)
            if pv >= 8:
                wgt += P["opp_8"]
            if pv >= 9:
                wgt += P["opp_9"]
            sc -= wgt * vals[q]
        return sc


# ====================================================================== AI
class AIPlayer:
    def __init__(self, board, level="hard", seed=None, style="balanced", params=None):
        self.level, self.style = level, style
        self.P = make_params(style, params)
        self.ev = Evaluator(board, self.P)
        self.b = board
        self.cfg = LEVELS[level]
        self.rng = random.Random(seed)
        self._proposed = None
        self._no_win = None  # ((turn, player), sigs from which a full win search failed)
        self._bandit_plan = None  # bandit move chosen together with a pre-roll ranger

    # ---------------------------------------------------------- scoring
    def score(self, s, p):
        """Evaluation plus this level's noise (exact scores use self.ev.score)."""
        sc = self.ev.score(s, p)
        if self.cfg["noise"]:
            sc += self.rng.uniform(-self.cfg["noise"], self.cfg["noise"])
        return sc

    def _score_after(self, s, a, p):
        t = s.clone()
        t.apply(a)
        return self.ev.score(t, p)

    @staticmethod
    def view(s, viewer):
        """Simulation copy of `s` as `viewer` sees it: opponents' hands are beliefs and
        their ventures are face down (only the count is known)."""
        t = s.clone()
        for q in range(s.n):
            if q == viewer:
                continue
            if s.belief is not None:
                t.hands[q] = s.belief[viewer][q][:]
            for ventures in (t.ventures, t.new_ventures):
                n = sum(ventures[q])
                ventures[q] = [0] * len(ventures[q])
                ventures[q][UNKNOWN_VENTURE] = n
        return t

    # ---------------------------------------------------------- dispatch
    def decide(self, real):
        """Return (action, reason) for the acting player."""
        p = real.actor()
        ph = real.phase
        s = self.view(real, p)
        if ph == DISCARD:
            counts = self.choose_discard(s, p)
            return ("discard", tuple(counts)), "keeps the cards closest to completing a build"
        if ph in (PRE_ROLL, MAIN, FREE_TRAILS) and real.fame(p) >= WIN_FAME - 3:
            line = self.win_line(s, p)
            if line:
                return line[0], self.win_reason(s, line)
        if ph in (MAIN, SPECIAL):
            offer = self.maybe_propose(s, p) if ph == MAIN else None
            if offer is not None:
                return offer
            _, plan, why = self.plan_turn(s, p)
            first = plan[0]
            if first[0] == "macro":
                first = first[1][0]
            return first, why
        ranked = self._rank(s, p)
        if ph == BANDIT:
            plan, self._bandit_plan = self._bandit_plan, None
            # beliefs may think an empty-handed player still holds a card
            legal = set(real.legal_actions())
            if plan is not None and plan[0] == (real.turn, p) and plan[1] in legal:
                return plan[1], plan[2]
            ranked = [x for x in ranked if x[1] in legal]
        if ph in (SETUP_SETTLE, SETUP_TRAIL):
            ranked = self._noisy(ranked, self._setup_scale(s) / 0.1)
        elif ph == BANDIT:
            ranked = self._noisy(ranked)
        if ph == PRE_ROLL and ranked[0][1][0] == "play_ranger":
            # pick the bandit now so the stated reason is the move that follows
            t = s.clone()
            t.apply(("play_ranger",))
            rr = self._noisy(self.rank_bandit(t, p))
            if rr:
                self._bandit_plan = ((real.turn, p), rr[0][1], rr[0][2])
                return ranked[0][1], "ranger before rolling: " + rr[0][2]
        return ranked[0][1:]

    def win_reason(self, s, line):
        return "winning line: " + " -> ".join(describe(s, a) for a in line)

    def _rank(self, s, p):
        """Ranked (score, action, why) options in the single-choice phases."""
        ph = s.phase
        if ph == SETUP_SETTLE:
            return self.rank_setup_villages(s, p)
        if ph == SETUP_TRAIL:
            return self.rank_setup_trails(s, p)
        if ph == BANDIT:
            return self.rank_bandit(s, p)
        if ph == PRE_ROLL:
            return self.rank_preroll(s, p)
        if ph == FREE_TRAILS:
            return self.rank_simple(s, p, s.legal_actions())
        return []

    def _noisy(self, ranked, scale=1.0):
        """Re-rank with this level's noise (easier bots misjudge). `scale` keeps the noise
        the same relative size for opening scores (see _setup_scale)."""
        if self.cfg["noise"] and len(ranked) > 1:
            noise = self.cfg["noise"] * scale
            noisy = [(sc + self.rng.uniform(-1, 1) * noise, a, why) for sc, a, why in ranked]
            noisy.sort(key=lambda x: -x[0])
            return noisy
        return ranked

    # =================================================== opening placement
    def pair_value(self, s, verts):
        """Opening quality of a set of village spots (pip units)."""
        b = self.b
        prod = [0] * 5
        numbers = []
        for v in verts:
            for h in b.vertex_hexes[v]:
                r = b.hex_resource[h]
                if r == BADLANDS:
                    continue
                prod[r] += b.hex_pips[h]
                numbers.append(b.hex_number[h])
        val = 0.0
        cap = 7.0 if len(verts) > 1 else 5.0
        for r in range(5):
            x = prod[r]
            if x > cap:
                x = cap + 0.6 * (x - cap)
            val += SETUP_W[r] * b.scarcity[r] * x
        val += 1.7 * sum(1 for r in range(5) if prod[r] > 0)
        if prod[0] and prod[1]:
            val += 1.4
        if prod[3] and prod[4]:
            val += 1.4
        if prod[2] and prod[3] and prod[4]:
            val += 0.6
        # variance: duplicate numbers concentrate risk
        val -= 0.4 * (len(numbers) - len(set(numbers)))
        for v in verts:
            dock = b.vertex_dock[v]
            if dock == "any":
                val += 1.3
            elif dock is not None:
                val += max(0.0, 0.55 * (prod[dock] - 3))
        return val, prod

    def expansion_room(self, s, v, p):
        """Best open spot two trails away from v (where the next village goes)."""
        b = self.b
        best = 0.0
        for w in b.vertex_neighbors[v]:
            if s.v_owner[w] != -1:
                continue
            for x in b.vertex_neighbors[w]:
                if x == v or not s.vertex_free(x):
                    continue
                # someone already sitting next to it can grab it first
                contested = any(s.v_owner[y] not in (-1, p) for y in b.vertex_neighbors[w])
                q = b.vertex_total_pips[x] * (0.6 if contested else 1.0)
                best = max(best, q)
        return best

    def _spot_value(self, s, p, v, existing):
        val, _ = self.pair_value(s, existing + [v])
        base, _ = self.pair_value(s, existing) if existing else (0.0, None)
        return val - base + 0.22 * self.expansion_room(s, v, p)

    # Opening scores are computed in pip units and scaled to fame-equivalents so the review
    # verdicts mean the same as later in the game. The factors are fitted to how much
    # Evaluator.score changes after the placement (tools: regress score deltas on raw deltas).
    SETTLE_SCALE = 0.1  # second village
    SETTLE_SCALE_1 = 0.066  # first village (the lookahead total also counts the pairing)
    TRAIL_SCALE = 0.03

    def _setup_scale(self, s):
        if s.phase == SETUP_TRAIL:
            return self.TRAIL_SCALE
        return self.SETTLE_SCALE if s.setup_idx >= s.n else self.SETTLE_SCALE_1

    def rank_setup_villages(self, s, p, extra=None):
        legal = s.legal_villages(p, setup=True)
        k = self._setup_scale(s)
        mine = list(s.villages[p])
        b = self.b
        ranked = []
        if s.setup_idx >= s.n:  # second round
            for v in legal:
                val = self._spot_value(s, p, v, mine)
                # starting hand: timber+clay lets you trail immediately
                res = [b.hex_resource[h] for h in b.vertex_hexes[v] if b.hex_resource[h] != BADLANDS]
                val += 0.35 * len(res)
                if 0 in res and 1 in res:
                    val += 0.6
                ranked.append((val * k, ("settle", v), self._settle_reason(s, p, v, mine)))
        else:
            pre = sorted(((self._spot_value(s, p, v, []), v) for v in legal), reverse=True)
            if self.cfg["lookahead"]:
                cand = [v for _, v in pre[:12]]
                if extra is not None and extra not in cand and extra in legal:
                    cand.append(extra)
                for v in cand:
                    total, partner = self._lookahead_first(s, p, v)
                    why = self._settle_reason(s, p, v, [])
                    if partner is not None:
                        why += f"; plans to pair it with vertex {partner} ({b.describe_vertex(partner)})"
                    ranked.append((total * k, ("settle", v), why))
                for val, v in pre[12:]:
                    if v not in cand:
                        # not deep-searched: estimate with the single-spot value
                        ranked.append(((val + 6.0) * k, ("settle", v), self._settle_reason(s, p, v, [])))
            else:
                for val, v in pre:
                    ranked.append((val * k, ("settle", v), self._settle_reason(s, p, v, [])))
        ranked.sort(key=lambda x: -x[0])
        return ranked

    def _lookahead_first(self, s, p, v):
        """Place v, simulate everyone's greedy picks until our second pick."""

        def place(q, x):
            t.v_owner[x] = q
            t.v_level[x] = 1
            t.villages[q].append(x)

        t = s.clone()
        place(p, v)
        order = t.setup_order
        i = t.setup_idx + 1
        while i < len(order) and order[i] != p:
            q = order[i]
            legal = t.legal_villages(q, setup=True)
            if not legal:
                break
            place(q, max(legal, key=lambda x: self._spot_value(t, q, x, t.villages[q])))
            i += 1
        legal = t.legal_villages(p, setup=True)
        if not legal:
            return self._spot_value(s, p, v, []), None
        best2 = max(legal, key=lambda x: self._spot_value(t, p, x, [v]))
        val, _ = self.pair_value(t, [v, best2])
        val += 0.22 * (self.expansion_room(t, v, p) + self.expansion_room(t, best2, p))
        return val, best2

    def _settle_reason(self, s, p, v, existing):
        b = self.b
        pips = b.vertex_total_pips[v]
        txt = f"{b.describe_vertex(v)} = {pips} pips"
        if existing:
            _, before = self.pair_value(s, existing)
            _, after = self.pair_value(s, existing + [v])
            new = [RES_NAMES[r] for r in range(5) if after[r] and not before[r]]
            if new:
                txt += f", adds {'/'.join(new)}"
            missing = [RES_NAMES[r] for r in range(5) if not after[r]]
            if missing:
                txt += f", still lacks {'/'.join(missing)}"
        return txt

    def rank_setup_trails(self, s, p):
        b = self.b
        v = s.last_village
        ranked = []
        for e in s.legal_setup_trails(p):
            a, c = b.edge_vertices[e]
            w = c if a == v else a
            best, target = 0.0, None
            for x in b.vertex_neighbors[w]:
                if x == v or not s.vertex_free(x):
                    continue
                q = self._spot_value(s, p, x, list(s.villages[p]))
                contested = sum(
                    1 for y in b.vertex_neighbors[x] for z in b.vertex_neighbors[y] if s.v_owner[z] not in (-1, p)
                )
                q *= 1.0 / (1 + 0.25 * contested)
                if q > best:
                    best, target = q, x
            # second-ring options keep the trail flexible
            ring = 0.0
            for x in b.vertex_neighbors[w]:
                if x == v:
                    continue
                for y in b.vertex_neighbors[x]:
                    if y != w and s.vertex_free(y):
                        ring = max(ring, b.vertex_total_pips[y])
            if target is None:
                val, why = best + 0.15 * ring, "no open spot that way"
            else:
                val = best + 0.15 * ring + (0.5 if b.vertex_dock[target] is not None else 0.0)
                why = f"points toward vertex {target} ({b.describe_vertex(target)})"
            ranked.append((val * self.TRAIL_SCALE, ("trail", e), why))
        ranked.sort(key=lambda x: -x[0])
        return ranked

    # ======================================================== bandit/discard
    def rank_bandit(self, s, p):
        ranked = []
        for a in s.legal_actions():
            _, h, victim = a
            hand = s.hands[victim] if victim != -1 else [0] * 5
            tot = sum(hand)
            # expected value over the card that might be stolen
            outcomes = [(hand[r] / tot, r) for r in range(5) if hand[r] > 0] if tot > 0 else [(1.0, None)]
            sc = 0.0
            for prob, r in outcomes:
                u = s.clone()
                u.bandit = h
                u.phase = s.bandit_return
                if r is not None:
                    take = min(1.0, u.hands[victim][r])
                    u.hands[victim][r] -= take
                    u.hands[p][r] += take
                sc += prob * self.ev.score(u, p)
            ranked.append((sc, a, self._bandit_reason(s, p, h, victim)))
        ranked.sort(key=lambda x: -x[0])
        return ranked

    def _bandit_reason(self, s, p, h, victim):
        b = self.b
        r = b.hex_resource[h]
        label = b.hex_label(h)
        if r == BADLANDS:
            what = f"{label} (badlands)"
        else:
            what = f"{label} ({b.hex_number[h]} {RES_NAMES[r]})"
        hit = sorted({s.names[o] for o in (s.v_owner[v] for v in b.hex_vertices[h]) if o not in (-1, p)})
        txt = f"bandit on {what}"
        if hit:
            txt += f" blocks {', '.join(hit)}"
        if victim != -1:
            txt += f"; steals from {s.names[victim]} ({round(s.hand_size(victim))} cards, {s.public_fame(victim)} fame)"
        return txt

    def choose_discard(self, s, p):
        n = s.discards.get(p, 0)
        t = s.clone()
        counts = [0] * 5
        for _ in range(n):
            best, br = None, None
            for r in range(5):
                if t.hands[p][r] > 0:
                    t.hands[p][r] -= 1
                    v = self.ev.values(t, p)[p]
                    t.hands[p][r] += 1
                    if best is None or v > best:
                        best, br = v, r
            t.hands[p][br] -= 1
            counts[br] += 1
        return counts

    def rank_discards(self, s, p, human_counts=None):
        """Value of the AI discard vs. an alternative one (for reviews)."""
        s = self.view(s, p)
        best = self.choose_discard(s, p)
        out = []
        for counts in [best] + ([human_counts] if human_counts else []):
            t = s.clone()
            for r in range(5):
                t.hands[p][r] -= counts[r]
            out.append((self.ev.values(t, p)[p], tuple(counts)))
        return out

    # ================================================================ turns
    CARD_VALUE = 0.2  # fame-eq of one resource card, for this-roll production estimates
    SEVEN_RISK = 0.02  # small edge lost by waiting with a ranger until after the roll

    def rank_preroll(self, s, p):
        base = self.ev.score(s, p)
        ranked = [None]  # roll goes first so it wins ties
        if s.playable(p, RANGER):
            sc, why = self._ranger_value(s, p)
            blocked = self.ev.blocked_pips(s, p)
            # a ranger played now also protects this roll's production
            sc += 0.03 * blocked
            t = s.clone()
            t.apply(("play_ranger",))
            if t.patrol == p and t.fame(p) >= WIN_FAME:
                sc = max(sc, base + self.WIN_BONUS)
                why = "takes Great Patrol and wins"
            ranked.append((sc, ("play_ranger",), "ranger before rolling: " + why))
            # rolling first keeps the ranger for after the roll: it only loses what the
            # bandit blocks on this roll and a small edge
            roll = max(base, sc - blocked / 36.0 * self.CARD_VALUE - self.SEVEN_RISK)
        else:
            roll = base
        ranked[0] = (roll, ("roll",), "roll the dice")
        ranked.sort(key=lambda x: -x[0])
        return ranked

    def _ranger_value(self, s, p):
        t = s.clone()
        t.apply(("play_ranger",))
        rr = self.rank_bandit(t, p)
        if not rr:
            return self.ev.score(t, p), "ranger"
        return rr[0][0], rr[0][2]

    def rank_simple(self, s, p, actions):
        out = [(self._score_after(s, a, p), a, describe(s, a)) for a in actions]
        out.sort(key=lambda x: -x[0])
        return out

    MAX_NEED = (1, 1, 1, 2, 3)  # no bank trade for a resource we already hold this many of

    def _candidates(self, s, p):
        h = s.hands[p]
        out = [
            a
            for a in s.legal_actions(include_trades=True)
            if a[0] != "end" and not (a[0] == "bank" and h[a[2]] >= self.MAX_NEED[a[2]])
        ]
        if s.phase == MAIN:
            out += self._macros(s, p)
        return out

    def _macros(self, s, p):
        """Bank/dock trades that complete a build, bundled with the build so the
        search sees 'trade, trade, town' as one step."""
        h = s.hands[p]
        ratios = s.ratios(p)
        out = []
        for kind in ("town", "village", "venture", "trail"):
            if kind == "town" and not s.legal_towns(p):
                continue
            if kind == "village" and not s.legal_villages(p):
                continue
            if kind == "trail" and not s.legal_trails(p):
                continue
            if kind == "venture" and s.deck_size() == 0:
                continue
            cost = COSTS[kind]
            deficit = [max(0, math.ceil(cost[r] - h[r])) for r in range(5)]
            if sum(deficit) == 0 or sum(deficit) > 3:
                continue
            spare = [max(0, h[r] - cost[r]) for r in range(5)]
            trades = []
            for want in range(5):
                for _ in range(deficit[want]):
                    opts = [r for r in range(5) if r != want and spare[r] >= ratios[r]]
                    if opts:
                        give = max(opts, key=lambda r: (spare[r] - ratios[r], -ratios[r]))
                        spare[give] -= ratios[give]
                        trades.append(("bank", give, want))
            if len(trades) == sum(deficit):
                out.append(("macro", tuple(trades), kind))
        return out

    def _apply_compound(self, s, a, p):
        """Apply an action; resolve follow-up sub-decisions greedily."""
        if a[0] == "macro":
            for tr in a[1]:
                s.apply(tr)
            # build the target where it scores best; ValueError if none is legal
            kind = BUILD_ACTION[a[2]]
            builds = [b for b in s.legal_actions(include_trades=False) if b[0] == kind]
            s.apply(max(builds, key=lambda b: self._score_after(s, b, p)))
            return
        s.apply(a)
        # ranger -> bandit, pathfinder -> free trails: resolve greedily
        for _ in range(4):
            if s.winner != -1 or s.phase not in (BANDIT, FREE_TRAILS) or s.actor() != p:
                break
            s.apply(max(s.legal_actions(), key=lambda b: self._score_after(s, b, p)))

    def _sig(self, s, p):
        return (
            tuple(s.hands[p]),
            tuple(sorted(s.trails[p])),
            tuple(sorted(s.villages[p])),
            tuple(sorted(s.towns[p])),
            tuple(s.ventures[p]),
            tuple(s.new_ventures[p]),
            s.bandit,
            s.venture_played,
            tuple(s.rangers),
        )

    # ======================================================== win-now search
    def _winning_ventures(self, s, p):
        """(patrol_possible, wildcard_cards, free_trails) for each way to use this turn's venture play."""
        free = s.free_trails if s.phase == FREE_TRAILS else 0
        opts = [(False, 0, free)]
        if s.venture_played or s.phase == FREE_TRAILS:
            return opts
        d = s.ventures[p]
        if d[RANGER] and s.patrol != p:
            k = s.rangers[p] + 1
            if k >= 3 and (s.patrol == -1 or k > s.rangers[s.patrol]):
                opts.append((True, 0, 0))
        if d[WINDFALL]:
            opts.append((False, 2, 0))
        if d[HOARD]:
            opts.append((False, max(sum(int(s.hands[q][r]) for q in range(s.n) if q != p) for r in range(5)), 0))
        if d[PATHFINDER]:
            opts.append((False, 0, 2))
        return opts

    def _win_possible(self, s, p):
        """Optimistic bound: could p still reach WIN_FAME this turn from s? (False = certainly not)."""
        need = WIN_FAME - s.fame(p)
        if need <= 0:
            return True
        b = self.b
        h = s.hands[p]
        ratios = s.ratios(p)
        sett_left = MAX_VILLAGES - len(s.villages[p])
        town_left = MAX_TOWNS - len(s.towns[p])
        trail_left = 15 - len(s.trails[p])
        # open spots by the number of trails needed to reach them
        spots = []
        if sett_left:
            dist = self.ev.reach(s, p)
            open_spots = [v for v in range(b.V) if dist[v] <= 2 and s.vertex_free(v)]
            spots = sorted(dist[v] for v in open_spots)
            for v in open_spots:  # a new dock village would improve the trade ratios
                dock = b.vertex_dock[v]
                if dock == "any":
                    ratios = [min(x, 3) for x in ratios]
                elif dock is not None:
                    ratios[dock] = min(ratios[dock], 2)
        lr_trails = None
        if s.longest != p:
            target = max(5, max(s.trail_len[o] for o in range(s.n) if o != p) + 1)
            # a trail can join two branches, so only the total trail count bounds the length
            k = max(1, target - len(s.trails[p]))
            if k <= trail_left:
                lr_trails = k
        for patrol, wild, free in self._winning_ventures(s, p):
            for lr in (0, 1) if lr_trails is not None else (0,):
                for ns in range(min(sett_left, len(spots), need) + 1):
                    for nc in range(min(town_left, len(s.villages[p]) + ns) + 1):
                        if ns + nc + 2 * (patrol + lr) < need:
                            continue
                        trails = max(lr * (lr_trails or 0), sum(spots[:ns]))
                        if trails > trail_left:
                            continue
                        paid = max(0, trails - free)
                        cost = [
                            ns * COSTS["village"][r] + nc * COSTS["town"][r] + paid * COSTS["trail"][r]
                            for r in range(5)
                        ]
                        short = sum(max(0, cost[r] - h[r]) for r in range(5))
                        spare = sum(int(max(0, h[r] - cost[r]) // ratios[r]) for r in range(5))
                        if short <= wild + spare:
                            return True
        return False

    def _win_moves(self, s, p):
        """Goal-directed move list for the win search (dead ends are cut by _win_possible)."""
        if s.phase == PRE_ROLL:
            return [("play_ranger",)] if any(x[0] for x in self._winning_ventures(s, p)) else []
        if s.phase == FREE_TRAILS:
            return s.legal_actions()
        trades, trails, ventures, builds = [], [], [], []
        lr_open = s.longest != p
        patrol = any(x[0] for x in self._winning_ventures(s, p))
        for a in s.legal_actions(include_trades=True):
            k = a[0]
            if k in ("end", "buy_venture"):
                continue
            if k == "bank":
                trades.append(a)
            elif k == "trail":
                trails.append(a)
            elif k == "play_ranger":
                if patrol:
                    ventures.append(a)
            elif k.startswith("play_"):
                ventures.append(a)
            else:
                builds.append(a)
        if not lr_open and s.legal_villages(p):
            trails = []  # a trail can't win by itself and a spot is already open
        return builds + ventures + trails + trades

    def find_win(self, s, p, cap=4000, depth=10):
        """Breadth-first (so the shortest line is found) search for a sequence of builds, bank/dock trades and venture plays
        that wins *this turn*, on the view `s` (own cards exact, opponents' hands as tracked).
        Only certain gains count: stolen cards are ignored and Hoard counts whole cards only.
        Returns the action list or None."""
        if s.winner == p:
            return []
        if s.current != p or s.phase not in (PRE_ROLL, MAIN, FREE_TRAILS) or s.fame(p) < WIN_FAME - 3:
            return None
        key = (s.turn, p)
        root_sig = self._sig(s, p)
        cache = self._no_win
        if cache is not None and cache[0] == key and root_sig in cache[1]:
            return None
        root = s.clone()
        for q in range(s.n):
            if q != p:
                root.hands[q] = [math.floor(x + 1e-9) for x in root.hands[q]]
        if not self._win_possible(root, p):
            return None
        queue = deque([(root, [])])
        seen = {root_sig}
        nodes = 0
        capped = False
        while queue:
            st, path = queue.popleft()
            if len(path) >= depth:
                capped = True
                continue
            for a in self._win_moves(st, p):
                nodes += 1
                if nodes > cap:
                    return None
                u = st.clone()
                try:
                    u.apply(a)
                    if u.phase == BANDIT and u.winner == -1:
                        keep = u.hands[p][:]
                        legal = u.legal_actions()
                        u.apply(next((x for x in legal if x[2] == -1), legal[0]))
                        u.hands[p] = keep  # a steal is never certain
                except Exception:
                    continue
                if u.winner == p:
                    return path + [a]
                if u.phase not in (MAIN, FREE_TRAILS):
                    continue
                sig = self._sig(u, p)
                if sig in seen:
                    continue
                seen.add(sig)
                if self._win_possible(u, p):
                    queue.append((u, path + [a]))
        if not capped:
            if cache is None or cache[0] != key:
                self._no_win = cache = (key, set())
            cache[1].update(seen)
        return None

    def win_line(self, s, p):
        """find_win guarded so it can never break a decision."""
        try:
            return self.find_win(s, p)
        except Exception:
            return None

    def plan_turn(self, s, p, beam=None, depth=None):
        """Beam search over in-turn action sequences. Returns (score, plan, why)."""
        beam = beam or self.cfg["beam"]
        depth = depth or self.cfg["depth"]
        root = self.score(s, p)
        best = (root, [("end",)])
        frontier = [(root, [], s)]
        seen = {self._sig(s, p)}
        for _ in range(depth):
            nxt = []
            for _, seq, st in frontier:
                if st.winner != -1 or st.actor() != p or st.turn != s.turn:
                    continue  # won, or (special building) the turn has moved on
                for a in self._candidates(st, p):
                    t = st.clone()
                    try:
                        self._apply_compound(t, a, p)
                    except Exception:
                        continue
                    sig = self._sig(t, p)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    sc = self.score(t, p)
                    nxt.append((sc, seq + [a], t))
                    if sc > best[0] + 1e-9:
                        best = (sc, seq + [a] + ([("end",)] if t.phase in (MAIN, SPECIAL) else []))
            if not nxt:
                break
            nxt.sort(key=lambda x: -x[0])
            frontier = nxt[:beam]
        plan = best[1]
        why = self.plan_reason(s, p, plan, best[0] - root)
        return best[0], plan, why

    def plan_reason(self, s, p, plan, gain):
        if plan[0][0] == "end":
            return "nothing improves the position - keeps cards"
        steps = [describe(s, a) for a in plan if a[0] != "end"]
        return "plan: " + " -> ".join(steps) + f"  (eval +{gain:.2f})"

    # ========================================================= main analysis
    WIN_BONUS = 3.0  # coach scale: winning now, in fame-eq over the current position

    def _coach_win(self, s, p):
        """(value, line) if p can force a win this turn from view s, else None."""
        if s.phase not in (MAIN, FREE_TRAILS) or s.fame(p) < WIN_FAME - 3:
            return None
        line = self.win_line(s, p)
        if not line:
            return None
        return self.ev.score(s, p) + self.WIN_BONUS, line

    def _wins_after(self, s, a, p):
        """Does taking action a in view s keep a forced win this turn?"""
        t = s.clone()
        try:
            self._apply_compound(t, a, p)
        except Exception:
            return False
        return t.winner == p or bool(self.win_line(t, p))

    def _win_text(self, s, line):
        return " -> ".join(describe(s, x) for x in line) + "  (wins the game)"

    def _rescale_win(self, ranked, s, p, win):
        """Coach rankings: a win is worth WIN_BONUS over the position (not the internal
        1000), and every non-winning option ranks at least 1 fame-eq below it."""
        W = self.ev.score(s, p) + self.WIN_BONUS if win is None else win[0]
        out = []
        for sc, a, why in ranked:
            if sc >= 500 or (win is not None and a == win[1][0]):
                sc = W
            elif win is not None:
                sc = min(sc, W - 1.0)
            out.append((sc, a, why))
        if win is not None:
            out = [x for x in out if x[1] != win[1][0]]
            out.append((W, win[1][0], self._win_text(s, win[1])))
        out.sort(key=lambda x: -x[0])
        return out

    def analyze(self, s, top=5):
        """Ranked options for the acting player (used by hint / review)."""
        p = s.actor()
        s = self.view(s, p)
        if s.phase == DISCARD:
            c = self.choose_discard(s, p)
            return [(0.0, ("discard", tuple(c)), "discard " + fmt_cards(c))]
        if s.phase in (MAIN, SPECIAL):
            return self.rank_main(s, p, top)
        ranked = self._rank(s, p)
        if s.phase == FREE_TRAILS:
            ranked = self._rescale_win(ranked, s, p, self._coach_win(s, p))
        return ranked[:top]

    def rank_main(self, s, p, top=5):
        base = self.ev.score(s, p)
        win = self._coach_win(s, p)
        firsts = []
        for a in self._candidates(s, p):
            t = s.clone()
            try:
                self._apply_compound(t, a, p)
            except Exception:
                continue
            firsts.append((self.ev.score(t, p), a, t))
        firsts.sort(key=lambda x: -x[0])
        out = [(base, ("end",), "pass, keep cards" if s.phase == SPECIAL else "end turn, keep cards")]
        for sc0, a, t in firsts[:8]:
            if t.phase in (MAIN, SPECIAL) and t.winner == -1 and t.actor() == p and t.turn == s.turn:
                sc, plan, _ = self.plan_turn(t, p, beam=3, depth=3)
            else:
                sc, plan = sc0, [("end",)]
            plan = [a] + [x for x in plan if x[0] != "end"]
            out.append((max(sc, sc0), a, " -> ".join(describe(s, x) for x in plan)))
        return self._rescale_win(out, s, p, win)[:top]

    def value_of_action(self, s, a):
        """Engine value of taking action a now (continuation searched)."""
        p = s.actor()
        ph = s.phase
        s = self.view(s, p)
        sc = self._value_of_action(s, p, ph, a)
        if ph in (MAIN, FREE_TRAILS):
            win = self._coach_win(s, p)
            if win is not None:
                return win[0] if a == win[1][0] or self._wins_after(s, a, p) else min(sc, win[0] - 1.0)
            if sc >= 500:
                return self.ev.score(s, p) + self.WIN_BONUS
        return sc

    def _value_of_action(self, s, p, ph, a):
        if ph in (SETUP_SETTLE, SETUP_TRAIL):
            ranked = self.rank_setup_villages(s, p, extra=a[1]) if ph == SETUP_SETTLE else self.rank_setup_trails(s, p)
            for sc, b, _ in ranked:
                if b == a:
                    return sc
            if ph == SETUP_SETTLE:
                return (self._spot_value(s, p, a[1], list(s.villages[p])) - 10.0) * self._setup_scale(s)
            return ranked[-1][0] if ranked else 0.0
        if ph == BANDIT:
            for sc, b, _ in self.rank_bandit(s, p):
                if b == a:
                    return sc
        if ph == PRE_ROLL:
            for sc, b, _ in self.rank_preroll(s, p):
                if b[0] == a[0]:
                    return sc
            t = s.clone()
            self._apply_compound(t, a, p)
            return self.ev.score(t, p)
        if ph in (MAIN, SPECIAL):
            if a[0] == "end":
                return self.ev.score(s, p)
            t = s.clone()
            self._apply_compound(t, a, p)
            if t.phase in (MAIN, SPECIAL) and t.winner == -1 and t.actor() == p and t.turn == s.turn:
                sc, _, _ = self.plan_turn(t, p, beam=max(4, self.cfg["beam"]), depth=3)  # matches best_value
                return max(sc, self.ev.score(t, p))
            return self.ev.score(t, p)
        t = s.clone()
        t.apply(a)
        return self.ev.score(t, p)

    def best_value(self, s):
        p = s.actor()
        ph = s.phase
        if ph in (MAIN, SPECIAL):
            v = self.view(s, p)
            win = self._coach_win(v, p)
            if win is not None:
                return win[0], win[1][0], "plan: " + self._win_text(v, win[1])
            sc, plan, why = self.plan_turn(v, p, beam=max(4, self.cfg["beam"]), depth=4)
            if sc >= 500:
                sc = self.ev.score(v, p) + self.WIN_BONUS
            return sc, plan[0], why
        ranked = self.analyze(s, top=1)
        if not ranked:
            return 0.0, None, ""
        return ranked[0]

    # ============================================================== trading
    def evaluate_offer(self, s, responder, proposer, give, get):
        """Proposer gives `give`, receives `get`. Should `responder` accept?
        Compares the world where the proposer then plays its best turn with vs.
        without the trade, scored from the responder's point of view.
        Returns (accept, gain)."""
        if not s.can_afford(responder, get):
            return False, -9.0
        s = self.view(s, responder)
        t = s.clone()
        for i in range(5):
            t.hands[proposer][i] += get[i] - give[i]
            t.hands[responder][i] += give[i] - get[i]
        if s.phase == MAIN and s.current == proposer:
            without = self._proposer_follow_up(s, proposer)
            with_ = self._proposer_follow_up(t, proposer)
        else:
            without, with_ = s, t
        gain = self.ev.score(with_, responder) - self.ev.score(without, responder)
        margin = self.P["trade_margin"]
        if s.public_fame(proposer) >= 8:
            margin += 0.5  # never feed someone about to win
        return gain > margin, gain

    MAX_OFFER = 4  # cards on one side of a counter-offer

    def counter_offer(self, real, responder, proposer, give, get):
        """The responder turned down `give` for `get`. Is there a nearby deal it would
        take instead? It asks for one more card the proposer is believed to hold, or
        gives one card fewer. Returns (give, get, gain) from the proposer's side, or None.
        The smallest change that works is preferred, so the answer is a real offer and
        not an opening bid."""
        if real.public_fame(proposer) >= 8 or not real.can_afford(responder, get):
            return None
        _, base = self.evaluate_offer(real, responder, proposer, give, get)
        if base < -0.8:
            return None  # too far apart to be worth haggling over
        believed = real.belief[responder][proposer] if real.belief is not None else real.hands[proposer]
        mine = real.hands[responder]
        options = []
        if sum(give) < self.MAX_OFFER:
            for r in range(5):
                if get[r] == 0 and believed[r] - give[r] >= 0.99:
                    options.append((tuple(g + (i == r) for i, g in enumerate(give)), tuple(get)))
        if sum(get) > 1:
            for r in range(5):
                if get[r]:
                    options.append((tuple(give), tuple(g - (i == r) for i, g in enumerate(get))))
        best = None
        for g2, t2 in options:
            ok, gain = self.evaluate_offer(real, responder, proposer, g2, t2)
            if not ok:
                continue
            # prefer the card we hold fewest of, then the smaller gain (the fairer deal)
            extra = next((r for r in range(5) if g2[r] > give[r]), None)
            key = (mine[extra] if extra is not None else -1, gain)
            if best is None or key < best[0]:
                best = (key, (g2, t2, gain))
        return best[1] if best else None

    def trade_gain(self, real, p, give, get):
        """What giving `give` for `get` is worth to p on its own turn: its best turn with
        the trade against its best turn without."""
        s = self.view(real, p)
        if not s.can_afford(p, give):
            return -9.0
        base, _, _ = self.plan_turn(s, p, beam=2, depth=2)
        t = s.clone()
        for i in range(5):
            t.hands[p][i] += get[i] - give[i]
        sc, _, _ = self.plan_turn(t, p, beam=2, depth=2)
        return sc - base

    def accepts_counter(self, real, p, give, get):
        """Would proposer p take this changed deal? (A little less than it asks of its own offers.)"""
        gain = self.trade_gain(real, p, give, get)
        return gain > 0.6 * self.P["propose_gain"], gain

    def _proposer_follow_up(self, s, proposer):
        _, plan, _ = self.plan_turn(s, proposer, beam=2, depth=2)
        t = s.clone()
        for a in plan:
            if a[0] == "end" or t.phase != MAIN:
                break
            try:
                self._apply_compound(t, a, proposer)
            except Exception:
                break
        return t

    def maybe_propose(self, s, p):
        """At most one round of player-trade offers per turn."""
        key = (s.turn, p)
        if self._proposed == key or s.trades_this_turn > 0:
            return None
        self._proposed = key
        offers = self.propose_trades(s, p)
        if not offers:
            return None
        gain, give, get = offers[0]
        return ("propose", give, get), f"offers {fmt_cards(give)} for {fmt_cards(get)} (would gain +{gain:.2f})"

    def propose_trades(self, s, p):
        """1:1 / 2:1 offers that unlock a build this turn, as (gain, give, get), best first."""
        h = s.hands[p]
        base_sc, _, _ = self.plan_turn(s, p, beam=2, depth=2)
        offers = {}  # ordered set of (give, get)
        for cost_name in ("town", "village", "venture", "trail"):
            cost = COSTS[cost_name]
            deficit = [max(0, cost[r] - h[r]) for r in range(5)]
            if sum(deficit) != 1:
                continue
            want = deficit.index(1)
            if not any(s.hands[q][want] > 0 for q in range(s.n) if q != p):
                continue
            get = tuple(int(r == want) for r in range(5))
            for give_r in range(5):
                for n_give in (1, 2):
                    if give_r != want and h[give_r] - cost[give_r] >= n_give:
                        offers[tuple(n_give if r == give_r else 0 for r in range(5)), get] = None
        scored = []
        for give, get in offers:
            t = s.clone()
            for i in range(5):
                t.hands[p][i] += get[i] - give[i]
            sc, _, _ = self.plan_turn(t, p, beam=2, depth=2)
            if sc - base_sc > self.P["propose_gain"]:
                scored.append((sc - base_sc, give, get))
        # keep the cheapest offer for each wanted resource
        best = {}
        for offer in sorted(scored, key=lambda x: (sum(x[1]), -x[0])):
            best.setdefault(offer[2], offer)
        return sorted(best.values(), key=lambda x: -x[0])


# ======================================================================
def describe(s, a):
    b = s.board
    k = a[0]
    if k == "settle":
        return f"village at {a[1]} [{b.describe_vertex(a[1])}]"
    if k == "town":
        return f"town at {a[1]} [{b.describe_vertex(a[1])}]"
    if k == "trail":
        return f"trail {a[1]}"
    if k == "buy_venture":
        return "buy venture"
    if k == "play_ranger":
        return "play Ranger"
    if k == "play_pathfinder":
        return "play Pathfinder"
    if k == "play_hoard":
        return f"play Hoard ({RES_NAMES[a[1]]})"
    if k == "play_windfall":
        return f"play Windfall ({RES_NAMES[a[1]]}+{RES_NAMES[a[2]]})"
    if k == "bank":
        return f"trade {s.trade_ratio(s.actor(), a[1])} {RES_NAMES[a[1]]} -> {RES_NAMES[a[2]]}"
    if k == "macro":
        trades = ", ".join(f"{s.trade_ratio(s.actor(), g)} {RES_NAMES[g]}->{RES_NAMES[w]}" for _, g, w in a[1])
        return f"bank-trade ({trades}) and build {a[2]}"
    if k == "trade":
        return f"trade {fmt_cards(a[2])} for {fmt_cards(a[3])} with {s.names[a[1]]}"
    if k == "bandit":
        txt = f"bandit to {b.hex_label(a[1])}"
        if a[2] != -1:
            txt += f", rob {s.names[a[2]]}"
        return txt
    if k == "roll":
        return "roll"
    if k == "end":
        return "pass" if s.phase == SPECIAL else "end turn"
    if k == "discard":
        return "discard " + fmt_cards(a[1])
    return str(a)

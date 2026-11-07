"""Machine opponents.

The engine is an *expert evaluation function* plus *search*:

* `Evaluator.values()` scores every player's position in "victory-point
  equivalents": real VP, expected production (robber-aware, with diminishing
  returns, port conversion and resource diversity), expansion potential
  (reachable open spots, contested-spot discount), hand quality (cards that
  complete builds, 7-roll exposure), development cards and the race for
  Longest Road / Largest Army.
* `score()` turns that into a decision score: my value minus a weighted share
  of opponents' values, with weights that grow for the leader and anyone close
  to winning (so the bots gang up on whoever is ahead - including you).
* `plan_turn()` runs a beam search over sequences of builds, bank/port trades
  and development-card plays inside the current turn.
* Opening placement uses a snake-draft lookahead: for every candidate first
  settlement it simulates the opponents' greedy picks until the bot's second
  pick, then takes the best pairing.
* Robber, discards, knights-before-roll and player trades are all decided by
  the same evaluation, so the bots never accept a trade that helps you more
  than it helps them.

Hidden information: every decision is made on a *view* of the game in which
opponents' resource hands are replaced by what the deciding player can know
from public events (see GameState.belief) - production, builds, trades and
discards are exact, steals between other players are tracked in expectation.
Other players' development cards and the deck order are never visible.
"""

import math
import random
from collections import deque

from .constants import (COSTS, DESERT, KNIGHT, MAX_CITIES, MAX_SETTLEMENTS, MONOPOLY,
                        RES_NAMES, ROAD_BUILDING, UNKNOWN_DEV, VP_CARD, WIN_VP,
                        YEAR_OF_PLENTY, DEV_DECK_COUNTS)
from .game import (DISCARD, MAIN, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD,
                   SETUP_SETTLE, fmt_cards)

PIP_VALUE = 0.105
W_EARLY = (1.15, 1.15, 0.80, 1.00, 0.80)
W_LATE = (0.70, 0.70, 0.70, 1.15, 1.25)
SETUP_W = (1.10, 1.10, 0.80, 1.00, 0.87)
DEV_VALUE = {KNIGHT: 0.33, ROAD_BUILDING: 0.42, MONOPOLY: 0.45, YEAR_OF_PLENTY: 0.55}
# an unseen card: 56% knight, 20% VP, 8% each of the progress cards
UNKNOWN_DEV_VALUE = (0.56 * DEV_VALUE[KNIGHT] + 0.2 * 1.0
                     + 0.08 * (DEV_VALUE[ROAD_BUILDING] + DEV_VALUE[MONOPOLY] + DEV_VALUE[YEAR_OF_PLENTY]))
DIST_DISC = (0.55, 0.38, 0.22)
ROAD_SETTLE = (2, 2, 1, 1, 0)

BUILD_ACTION = {"city": "city", "settlement": "settle", "road": "road", "dev": "buy_dev"}

LEVELS = {
    # beam width, depth, score noise, setup lookahead
    "easy": dict(beam=1, depth=1, noise=0.9, lookahead=False),
    "medium": dict(beam=2, depth=2, noise=0.35, lookahead=False),
    "hard": dict(beam=5, depth=4, noise=0.0, lookahead=True),
}


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


class Evaluator:
    def __init__(self, board):
        self.b = board

    # ------------------------------------------------------------- helpers
    def production(self, s, q):
        b = self.b
        prod = [0.0] * 5
        for lvl, verts in ((1, s.settlements[q]), (2, s.cities[q])):
            for v in verts:
                for h in b.vertex_hexes[v]:
                    if h == s.robber:
                        continue
                    r = b.hex_resource[h]
                    if r != DESERT:
                        prod[r] += b.hex_pips[h] * lvl
        return prod

    def blocked_pips(self, s, q):
        h = s.robber
        b = self.b
        if b.hex_resource[h] == DESERT:
            return 0
        tot = 0
        for v in b.hex_vertices[h]:
            if s.v_owner[v] == q:
                tot += b.hex_pips[h] * s.v_level[v]
        return tot

    def reach(self, s, q):
        """dist[v] = roads needed to reach v from q's network (0..2), else 9."""
        b = self.b
        dist = [9] * b.V
        dq = deque()
        for e in s.roads[q]:
            for v in b.edge_vertices[e]:
                if dist[v]:
                    dist[v] = 0
                    dq.append(v)
        for v in s.settlements[q] + s.cities[q]:
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
            if h == s.robber:
                continue
            r = b.hex_resource[h]
            if r == DESERT:
                continue
            pip = b.hex_pips[h]
            val += weights[r] * b.scarcity[r] * pip
            if prod[r] == 0:
                val += 1.8
        port = b.vertex_port[v]
        if port == "any":
            val += 1.2
        elif port is not None and prod[port] >= 5:
            val += 0.35 * prod[port]
        return val

    # -------------------------------------------------------------- values
    def values(self, s, viewer):
        """Position value of every player as seen by `viewer`."""
        n = s.n
        b = self.b
        if s.winner != -1:
            return [1000.0 if q == s.winner else 0.0 for q in range(n)]
        # hidden-information estimates
        own_vp_cards = s.vp_cards(viewer)
        unknown = s.deck_size() + sum(s.dev_count(q) for q in range(n) if q != viewer)
        p_vp = (DEV_DECK_COUNTS[VP_CARD] - own_vp_cards) / unknown if unknown else 0.0
        p_vp = clamp(p_vp, 0.0, 1.0)

        est_vp = []
        for q in range(n):
            if q == viewer:
                est_vp.append(s.vp(q))
            else:
                est_vp.append(s.public_vp(q) + p_vp * s.dev_count(q))
        top = max(est_vp)
        horizon = clamp((WIN_VP - top) / 8.0, 0.2, 1.0)

        free = [s.vertex_free(v) for v in range(b.V)]
        reaches = [self.reach(s, q) for q in range(n)]
        p7 = clamp(0.155 * (n - 1), 0.0, 0.6)

        out = []
        for q in range(n):
            t = clamp((s.public_vp(q) - 2) / 6.0, 0.0, 1.0)
            w = [W_EARLY[r] * (1 - t) + W_LATE[r] * t for r in range(5)]
            sett_left = MAX_SETTLEMENTS - len(s.settlements[q])
            city_left = MAX_CITIES - len(s.cities[q])
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
            exp_val = PIP_VALUE * horizon * sum(x for x, _ in spots[:min(2, sett_left)])
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
                    pv -= 3.2 * w[r] * (0.5 if 2 in ratios else 1.0)
            prod_val = PIP_VALUE * horizon * pv
            if city_left == 0 and sett_left == 0:
                prod_val *= 0.6

            # --- hand: raw card value + bonus for being close to a build
            h = s.hands[q]
            hs = sum(h)
            hv = 0.0
            for r in range(5):
                hv += h[r] * max(0.03, 0.09 / ratios[r])
            targets = []
            if sett_left > 0 and has_spot0:
                targets.append((COSTS["settlement"], 1.5))
            elif sett_left > 0 and has_spot:
                targets.append((ROAD_SETTLE, 1.25))
            if city_left > 0 and s.settlements[q]:
                targets.append((COSTS["city"], 1.45))
            if s.deck_size() > 0:
                targets.append((COSTS["dev"], 0.5))
            if has_spot and not has_spot0:
                targets.append((COSTS["road"], 0.3))
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
                bonuses.append(util * frac * frac * 0.40)
            if bonuses:
                bonuses.sort(reverse=True)
                hv += bonuses[0] + (0.3 * bonuses[1] if len(bonuses) > 1 else 0.0)
            if hs > 7:
                hv -= p7 * (hs // 2) * 0.13

            # --- development cards
            dv = 0.0
            if q == viewer:
                for card, val in DEV_VALUE.items():
                    c = s.devs[q][card] + s.new_devs[q][card]
                    if c:
                        if card == ROAD_BUILDING and not has_spot:
                            val *= 0.5
                        if card == KNIGHT:
                            val += 0.012 * self.blocked_pips(s, q)
                        dv += c * val
                unk = s.new_devs[q][UNKNOWN_DEV] + s.devs[q][UNKNOWN_DEV]
                dv += unk * UNKNOWN_DEV_VALUE
                knights_in_hand = s.devs[q][KNIGHT] + s.new_devs[q][KNIGHT] + 0.56 * unk
            else:
                hidden = s.dev_count(q)
                dv += hidden * 0.30
                knights_in_hand = hidden * 0.56

            # --- largest army race
            av = 0.0
            best_other = max((s.knights[o] for o in range(n) if o != q), default=0)
            if s.army != q:
                k = s.knights[q]
                needk = max(3, best_other + 1)
                av += 0.1 * k
                if k + knights_in_hand >= needk:
                    av += 0.55
                elif k + knights_in_hand + 1 >= needk:
                    av += 0.2
            elif best_other >= s.knights[q] - 1:
                av -= 0.3

            # --- longest road race
            rv = 0.0
            L = s.road_len[q]
            if s.longest != q:
                holder_len = s.road_len[s.longest] if s.longest != -1 else 4
                target = max(5, holder_len + 1)
                if L >= 3:
                    rv += 0.75 * (min(L, target) / target) ** 3
            elif max((s.road_len[o] for o in range(n) if o != q), default=0) >= L - 1:
                rv -= 0.3

            out.append(est_vp[q] + prod_val + exp_val + hv + dv + av + rv)
        return out

    def score(self, s, p):
        if s.winner != -1:
            return 1000.0 if s.winner == p else -1000.0
        vals = self.values(s, p)
        opp = [q for q in range(s.n) if q != p]
        lead = max(opp, key=lambda q: vals[q])
        sc = vals[p]
        for q in opp:
            wgt = 0.28
            if q == lead:
                wgt += 0.25
            pv = s.public_vp(q)
            if pv >= 8:
                wgt += 0.35
            if pv >= 9:
                wgt += 0.4
            sc -= wgt * vals[q]
        return sc


# ====================================================================== AI
class AIPlayer:
    def __init__(self, board, level="hard", seed=None):
        self.ev = Evaluator(board)
        self.b = board
        self.cfg = LEVELS[level]
        self.rng = random.Random(seed)
        self._proposed = None
        self._no_win = None  # ((turn, player), sigs from which a full win search failed)
        self._robber_plan = None  # robber move chosen together with a pre-roll knight

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
        their development cards are face down (only the count is known)."""
        t = s.clone()
        for q in range(s.n):
            if q == viewer:
                continue
            if s.belief is not None:
                t.hands[q] = s.belief[viewer][q][:]
            for devs in (t.devs, t.new_devs):
                n = sum(devs[q])
                devs[q] = [0] * len(devs[q])
                devs[q][UNKNOWN_DEV] = n
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
        if ph in (PRE_ROLL, MAIN, ROAD_BUILD) and real.vp(p) >= WIN_VP - 3:
            line = self.win_line(s, p)
            if line:
                return line[0], self.win_reason(s, line)
        if ph == MAIN:
            offer = self.maybe_propose(s, p)
            if offer is not None:
                return offer
            _, plan, why = self.plan_turn(s, p)
            first = plan[0]
            if first[0] == "macro":
                first = first[1][0]
            return first, why
        ranked = self._rank(s, p)
        if ph == ROBBER:
            plan, self._robber_plan = self._robber_plan, None
            # beliefs may think an empty-handed player still holds a card
            legal = set(real.legal_actions())
            if plan is not None and plan[0] == (real.turn, p) and plan[1] in legal:
                return plan[1], plan[2]
            ranked = [x for x in ranked if x[1] in legal]
        if ph in (SETUP_SETTLE, SETUP_ROAD):
            ranked = self._noisy(ranked, self._setup_scale(s) / 0.1)
        elif ph == ROBBER:
            ranked = self._noisy(ranked)
        if ph == PRE_ROLL and ranked[0][1][0] == "play_knight":
            # pick the robber now so the stated reason is the move that follows
            t = s.clone()
            t.apply(("play_knight",))
            rr = self._noisy(self.rank_robber(t, p))
            if rr:
                self._robber_plan = ((real.turn, p), rr[0][1], rr[0][2])
                return ranked[0][1], "knight before rolling: " + rr[0][2]
        return ranked[0][1:]

    def win_reason(self, s, line):
        return "winning line: " + " -> ".join(describe(s, a) for a in line)

    def _rank(self, s, p):
        """Ranked (score, action, why) options in the single-choice phases."""
        ph = s.phase
        if ph == SETUP_SETTLE:
            return self.rank_setup_settlements(s, p)
        if ph == SETUP_ROAD:
            return self.rank_setup_roads(s, p)
        if ph == ROBBER:
            return self.rank_robber(s, p)
        if ph == PRE_ROLL:
            return self.rank_preroll(s, p)
        if ph == ROAD_BUILD:
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
        """Opening quality of a set of settlement spots (pip units)."""
        b = self.b
        prod = [0] * 5
        numbers = []
        for v in verts:
            for h in b.vertex_hexes[v]:
                r = b.hex_resource[h]
                if r == DESERT:
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
            port = b.vertex_port[v]
            if port == "any":
                val += 1.3
            elif port is not None:
                val += max(0.0, 0.55 * (prod[port] - 3))
        return val, prod

    def expansion_room(self, s, v, p):
        """Best open spot two roads away from v (where the next settlement goes)."""
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

    # Opening scores are computed in pip units and scaled to VP-equivalents so the review
    # verdicts mean the same as later in the game. The factors are fitted to how much
    # Evaluator.score changes after the placement (tools: regress score deltas on raw deltas).
    SETTLE_SCALE = 0.1     # second settlement
    SETTLE_SCALE_1 = 0.066  # first settlement (the lookahead total also counts the pairing)
    ROAD_SCALE = 0.03

    def _setup_scale(self, s):
        if s.phase == SETUP_ROAD:
            return self.ROAD_SCALE
        return self.SETTLE_SCALE if s.setup_idx >= s.n else self.SETTLE_SCALE_1

    def rank_setup_settlements(self, s, p, extra=None):
        legal = s.legal_settlements(p, setup=True)
        k = self._setup_scale(s)
        mine = list(s.settlements[p])
        b = self.b
        ranked = []
        if s.setup_idx >= s.n:  # second round
            for v in legal:
                val = self._spot_value(s, p, v, mine)
                # starting hand: wood+brick lets you road immediately
                res = [b.hex_resource[h] for h in b.vertex_hexes[v] if b.hex_resource[h] != DESERT]
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
            t.settlements[q].append(x)

        t = s.clone()
        place(p, v)
        order = t.setup_order
        i = t.setup_idx + 1
        while i < len(order) and order[i] != p:
            q = order[i]
            legal = t.legal_settlements(q, setup=True)
            if not legal:
                break
            place(q, max(legal, key=lambda x: self._spot_value(t, q, x, t.settlements[q])))
            i += 1
        legal = t.legal_settlements(p, setup=True)
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

    def rank_setup_roads(self, s, p):
        b = self.b
        v = s.last_settlement
        ranked = []
        for e in s.legal_setup_roads(p):
            a, c = b.edge_vertices[e]
            w = c if a == v else a
            best, target = 0.0, None
            for x in b.vertex_neighbors[w]:
                if x == v or not s.vertex_free(x):
                    continue
                q = self._spot_value(s, p, x, list(s.settlements[p]))
                contested = sum(1 for y in b.vertex_neighbors[x] for z in b.vertex_neighbors[y]
                                if s.v_owner[z] not in (-1, p))
                q *= 1.0 / (1 + 0.25 * contested)
                if q > best:
                    best, target = q, x
            # second-ring options keep the road flexible
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
                val = best + 0.15 * ring + (0.5 if b.vertex_port[target] is not None else 0.0)
                why = f"points toward vertex {target} ({b.describe_vertex(target)})"
            ranked.append((val * self.ROAD_SCALE, ("road", e), why))
        ranked.sort(key=lambda x: -x[0])
        return ranked

    # ======================================================== robber/discard
    def rank_robber(self, s, p):
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
                u.robber = h
                u.phase = s.robber_return
                if r is not None:
                    take = min(1.0, u.hands[victim][r])
                    u.hands[victim][r] -= take
                    u.hands[p][r] += take
                sc += prob * self.ev.score(u, p)
            ranked.append((sc, a, self._robber_reason(s, p, h, victim)))
        ranked.sort(key=lambda x: -x[0])
        return ranked

    def _robber_reason(self, s, p, h, victim):
        b = self.b
        r = b.hex_resource[h]
        label = b.hex_label(h)
        if r == DESERT:
            what = f"{label} (desert)"
        else:
            what = f"{label} ({b.hex_number[h]} {RES_NAMES[r]})"
        hit = sorted({s.names[o] for o in (s.v_owner[v] for v in b.hex_vertices[h]) if o not in (-1, p)})
        txt = f"robber on {what}"
        if hit:
            txt += f" blocks {', '.join(hit)}"
        if victim != -1:
            txt += f"; steals from {s.names[victim]} ({round(s.hand_size(victim))} cards, {s.public_vp(victim)} VP)"
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
    CARD_VALUE = 0.2   # VP-eq of one resource card, for this-roll production estimates
    SEVEN_RISK = 0.02  # small edge lost by waiting with a knight until after the roll

    def rank_preroll(self, s, p):
        base = self.ev.score(s, p)
        ranked = [None]  # roll goes first so it wins ties
        if s.playable(p, KNIGHT):
            sc, why = self._knight_value(s, p)
            blocked = self.ev.blocked_pips(s, p)
            # a knight played now also protects this roll's production
            sc += 0.03 * blocked
            t = s.clone()
            t.apply(("play_knight",))
            if t.army == p and t.vp(p) >= WIN_VP:
                sc = max(sc, base + self.WIN_BONUS)
                why = "takes Largest Army and wins"
            ranked.append((sc, ("play_knight",), "knight before rolling: " + why))
            # rolling first keeps the knight for after the roll: it only loses what the
            # robber blocks on this roll and a small edge
            roll = max(base, sc - blocked / 36.0 * self.CARD_VALUE - self.SEVEN_RISK)
        else:
            roll = base
        ranked[0] = (roll, ("roll",), "roll the dice")
        ranked.sort(key=lambda x: -x[0])
        return ranked

    def _knight_value(self, s, p):
        t = s.clone()
        t.apply(("play_knight",))
        rr = self.rank_robber(t, p)
        if not rr:
            return self.ev.score(t, p), "knight"
        return rr[0][0], rr[0][2]

    def rank_simple(self, s, p, actions):
        out = [(self._score_after(s, a, p), a, describe(s, a)) for a in actions]
        out.sort(key=lambda x: -x[0])
        return out

    MAX_NEED = (1, 1, 1, 2, 3)  # no bank trade for a resource we already hold this many of

    def _candidates(self, s, p):
        h = s.hands[p]
        out = [a for a in s.legal_actions(include_trades=True)
               if a[0] != "end" and not (a[0] == "bank" and h[a[2]] >= self.MAX_NEED[a[2]])]
        if s.phase == MAIN:
            out += self._macros(s, p)
        return out

    def _macros(self, s, p):
        """Bank/port trades that complete a build, bundled with the build so the
        search sees 'trade, trade, city' as one step."""
        h = s.hands[p]
        ratios = s.ratios(p)
        out = []
        for kind in ("city", "settlement", "dev", "road"):
            if kind == "city" and not s.legal_cities(p):
                continue
            if kind == "settlement" and not s.legal_settlements(p):
                continue
            if kind == "road" and not s.legal_roads(p):
                continue
            if kind == "dev" and s.deck_size() == 0:
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
        # knight -> robber, road building -> free roads: resolve greedily
        for _ in range(4):
            if s.winner != -1 or s.phase not in (ROBBER, ROAD_BUILD) or s.actor() != p:
                break
            s.apply(max(s.legal_actions(), key=lambda b: self._score_after(s, b, p)))

    def _sig(self, s, p):
        return (tuple(s.hands[p]), tuple(sorted(s.roads[p])), tuple(sorted(s.settlements[p])),
                tuple(sorted(s.cities[p])), tuple(s.devs[p]), tuple(s.new_devs[p]), s.robber,
                s.dev_played, tuple(s.knights))

    # ======================================================== win-now search
    def _winning_devs(self, s, p):
        """(army_possible, wildcard_cards, free_roads) for each way to use this turn's dev play."""
        free = s.free_roads if s.phase == ROAD_BUILD else 0
        opts = [(False, 0, free)]
        if s.dev_played or s.phase == ROAD_BUILD:
            return opts
        d = s.devs[p]
        if d[KNIGHT] and s.army != p:
            k = s.knights[p] + 1
            if k >= 3 and (s.army == -1 or k > s.knights[s.army]):
                opts.append((True, 0, 0))
        if d[YEAR_OF_PLENTY]:
            opts.append((False, 2, 0))
        if d[MONOPOLY]:
            opts.append((False, max(sum(int(s.hands[q][r]) for q in range(s.n) if q != p)
                                    for r in range(5)), 0))
        if d[ROAD_BUILDING]:
            opts.append((False, 0, 2))
        return opts

    def _win_possible(self, s, p):
        """Optimistic bound: could p still reach WIN_VP this turn from s? (False = certainly not)."""
        need = WIN_VP - s.vp(p)
        if need <= 0:
            return True
        b = self.b
        h = s.hands[p]
        ratios = s.ratios(p)
        sett_left = MAX_SETTLEMENTS - len(s.settlements[p])
        city_left = MAX_CITIES - len(s.cities[p])
        road_left = 15 - len(s.roads[p])
        # open spots by the number of roads needed to reach them
        spots = []
        if sett_left:
            dist = self.ev.reach(s, p)
            open_spots = [v for v in range(b.V) if dist[v] <= 2 and s.vertex_free(v)]
            spots = sorted(dist[v] for v in open_spots)
            for v in open_spots:  # a new port settlement would improve the trade ratios
                port = b.vertex_port[v]
                if port == "any":
                    ratios = [min(x, 3) for x in ratios]
                elif port is not None:
                    ratios[port] = min(ratios[port], 2)
        lr_roads = None
        if s.longest != p:
            target = max(5, max(s.road_len[o] for o in range(s.n) if o != p) + 1)
            # a road can join two branches, so only the total road count bounds the length
            k = max(1, target - len(s.roads[p]))
            if k <= road_left:
                lr_roads = k
        for army, wild, free in self._winning_devs(s, p):
            for lr in ((0, 1) if lr_roads is not None else (0,)):
                for ns in range(min(sett_left, len(spots), need) + 1):
                    for nc in range(min(city_left, len(s.settlements[p]) + ns) + 1):
                        if ns + nc + 2 * (army + lr) < need:
                            continue
                        roads = max(lr * (lr_roads or 0), sum(spots[:ns]))
                        if roads > road_left:
                            continue
                        paid = max(0, roads - free)
                        cost = [ns * COSTS["settlement"][r] + nc * COSTS["city"][r] + paid * COSTS["road"][r]
                                for r in range(5)]
                        short = sum(max(0, cost[r] - h[r]) for r in range(5))
                        spare = sum(int(max(0, h[r] - cost[r]) // ratios[r]) for r in range(5))
                        if short <= wild + spare:
                            return True
        return False

    def _win_moves(self, s, p):
        """Goal-directed move list for the win search (dead ends are cut by _win_possible)."""
        if s.phase == PRE_ROLL:
            return [("play_knight",)] if any(x[0] for x in self._winning_devs(s, p)) else []
        if s.phase == ROAD_BUILD:
            return s.legal_actions()
        trades, roads, devs, builds = [], [], [], []
        lr_open = s.longest != p
        army = any(x[0] for x in self._winning_devs(s, p))
        for a in s.legal_actions(include_trades=True):
            k = a[0]
            if k in ("end", "buy_dev"):
                continue
            if k == "bank":
                trades.append(a)
            elif k == "road":
                roads.append(a)
            elif k == "play_knight":
                if army:
                    devs.append(a)
            elif k.startswith("play_"):
                devs.append(a)
            else:
                builds.append(a)
        if not lr_open and s.legal_settlements(p):
            roads = []  # a road can't win by itself and a spot is already open
        return builds + devs + roads + trades

    def find_win(self, s, p, cap=4000, depth=10):
        """Breadth-first (so the shortest line is found) search for a sequence of builds, bank/port trades and dev-card plays
        that wins *this turn*, on the view `s` (own cards exact, opponents' hands as tracked).
        Only certain gains count: stolen cards are ignored and Monopoly counts whole cards only.
        Returns the action list or None."""
        if s.winner == p:
            return []
        if s.current != p or s.phase not in (PRE_ROLL, MAIN, ROAD_BUILD) or s.vp(p) < WIN_VP - 3:
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
                    if u.phase == ROBBER and u.winner == -1:
                        keep = u.hands[p][:]
                        legal = u.legal_actions()
                        u.apply(next((x for x in legal if x[2] == -1), legal[0]))
                        u.hands[p] = keep  # a steal is never certain
                except Exception:
                    continue
                if u.winner == p:
                    return path + [a]
                if u.phase not in (MAIN, ROAD_BUILD):
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
                if st.winner != -1:
                    continue
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
                        best = (sc, seq + [a] + ([("end",)] if t.phase == MAIN else []))
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
    WIN_BONUS = 3.0  # coach scale: winning now, in VP-eq over the current position

    def _coach_win(self, s, p):
        """(value, line) if p can force a win this turn from view s, else None."""
        if s.phase not in (MAIN, ROAD_BUILD) or s.vp(p) < WIN_VP - 3:
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
        1000), and every non-winning option ranks at least 1 VP-eq below it."""
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
        if s.phase == MAIN:
            return self.rank_main(s, p, top)
        ranked = self._rank(s, p)
        if s.phase == ROAD_BUILD:
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
        out = [(base, ("end",), "end turn, keep cards")]
        for sc0, a, t in firsts[:8]:
            if t.phase == MAIN and t.winner == -1:
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
        if ph in (MAIN, ROAD_BUILD):
            win = self._coach_win(s, p)
            if win is not None:
                return win[0] if a == win[1][0] or self._wins_after(s, a, p) else min(sc, win[0] - 1.0)
            if sc >= 500:
                return self.ev.score(s, p) + self.WIN_BONUS
        return sc

    def _value_of_action(self, s, p, ph, a):
        if ph in (SETUP_SETTLE, SETUP_ROAD):
            ranked = self.rank_setup_settlements(s, p, extra=a[1]) if ph == SETUP_SETTLE else self.rank_setup_roads(s, p)
            for sc, b, _ in ranked:
                if b == a:
                    return sc
            if ph == SETUP_SETTLE:
                return (self._spot_value(s, p, a[1], list(s.settlements[p])) - 10.0) * self._setup_scale(s)
            return ranked[-1][0] if ranked else 0.0
        if ph == ROBBER:
            for sc, b, _ in self.rank_robber(s, p):
                if b == a:
                    return sc
        if ph == PRE_ROLL:
            for sc, b, _ in self.rank_preroll(s, p):
                if b[0] == a[0]:
                    return sc
            t = s.clone()
            self._apply_compound(t, a, p)
            return self.ev.score(t, p)
        if ph == MAIN:
            if a[0] == "end":
                return self.ev.score(s, p)
            t = s.clone()
            self._apply_compound(t, a, p)
            if t.phase == MAIN and t.winner == -1:
                sc, _, _ = self.plan_turn(t, p, beam=max(4, self.cfg["beam"]), depth=3)  # matches best_value
                return max(sc, self.ev.score(t, p))
            return self.ev.score(t, p)
        t = s.clone()
        t.apply(a)
        return self.ev.score(t, p)

    def best_value(self, s):
        p = s.actor()
        ph = s.phase
        if ph == MAIN:
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
        margin = 0.05
        if s.public_vp(proposer) >= 8:
            margin += 0.5  # never feed someone about to win
        return gain > margin, gain

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
        for cost_name in ("city", "settlement", "dev", "road"):
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
            if sc - base_sc > 0.25:
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
        return f"settlement at {a[1]} [{b.describe_vertex(a[1])}]"
    if k == "city":
        return f"city at {a[1]} [{b.describe_vertex(a[1])}]"
    if k == "road":
        return f"road {a[1]}"
    if k == "buy_dev":
        return "buy development card"
    if k == "play_knight":
        return "play Knight"
    if k == "play_road":
        return "play Road Building"
    if k == "play_mono":
        return f"play Monopoly ({RES_NAMES[a[1]]})"
    if k == "play_yop":
        return f"play Year of Plenty ({RES_NAMES[a[1]]}+{RES_NAMES[a[2]]})"
    if k == "bank":
        return f"trade {s.trade_ratio(s.actor(), a[1])} {RES_NAMES[a[1]]} -> {RES_NAMES[a[2]]}"
    if k == "macro":
        trades = ", ".join(f"{s.trade_ratio(s.actor(), g)} {RES_NAMES[g]}->{RES_NAMES[w]}" for _, g, w in a[1])
        return f"bank-trade ({trades}) and build {a[2]}"
    if k == "trade":
        return f"trade {fmt_cards(a[2])} for {fmt_cards(a[3])} with {s.names[a[1]]}"
    if k == "robber":
        txt = f"robber to {b.hex_label(a[1])}"
        if a[2] != -1:
            txt += f", rob {s.names[a[2]]}"
        return txt
    if k == "roll":
        return "roll"
    if k == "end":
        return "end turn"
    if k == "discard":
        return "discard " + fmt_cards(a[1])
    return str(a)

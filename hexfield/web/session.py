"""One web game: engine state, bots, coach, undo history and async move reviews.

Everything the browser sees goes through `WebGame.view()`, which shows only what
the human could know at a real table: their own hand and dev cards, opponents'
card *counts*, and the public log.
"""

import math
import queue
import random
import threading
import time

from ..ai import AIPlayer, describe
from ..constants import DESERT, KNIGHT, MAX_CITIES, MAX_ROADS, MAX_SETTLEMENTS, RES_NAMES
from ..game import (MAIN, OVER, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD, SETUP_SETTLE,
                    GameState, IllegalAction, fmt_cards)
from ..render import second_person

AI_NAMES = ["Vera", "Magnus", "Kasimir", "Ingrid", "Tobias", "Selene"]
VERDICTS = [(0.05, "best"), (0.20, "good"), (0.50, "inaccuracy"), (1.00, "mistake"), (9e9, "blunder")]
HEX_R = 60.0


def verdict(delta):
    for thr, name in VERDICTS:
        if delta < thr:
            return name
    return VERDICTS[-1][1]


def board_geometry(b):
    """Pixel layout of a board (pointy-top hexes, radius HEX_R)."""
    rt3 = math.sqrt(3)
    centers = [(HEX_R * rt3 * (q + r / 2), HEX_R * 1.5 * r) for q, r in b.coords]
    corners = [(math.cos(math.radians(60 * i - 90)) * HEX_R,
                math.sin(math.radians(60 * i - 90)) * HEX_R) for i in range(6)]
    vxy = [None] * b.V
    for h, vs in enumerate(b.hex_vertices):
        cx, cy = centers[h]
        for i, v in enumerate(vs):
            if vxy[v] is None:
                vxy[v] = (round(cx + corners[i][0], 2), round(cy + corners[i][1], 2))
    hexes = []
    for h in range(b.H):
        r = b.hex_resource[h]
        hexes.append({
            "x": round(centers[h][0], 2), "y": round(centers[h][1], 2),
            "res": None if r == DESERT else RES_NAMES[r],
            "num": b.hex_number[h], "pips": b.hex_pips[h], "label": b.hex_label(h),
        })
    ports = []
    for e, kind in b.ports:
        a, c = b.edge_vertices[e]
        ports.append({"edge": e, "kind": "any" if kind == "any" else RES_NAMES[kind],
                      "ratio": 3 if kind == "any" else 2, "v": [a, c]})
    return {
        "hexes": hexes,
        "vertices": [{"x": x, "y": y, "desc": b.describe_vertex(v),
                      "pips": b.vertex_total_pips[v]} for v, (x, y) in enumerate(vxy)],
        "edges": [list(pr) for pr in b.edge_vertices],
        "ports": ports,
        "radius": HEX_R,
    }


def to_action(raw):
    """JSON list -> engine tuple."""
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise IllegalAction("malformed action")
    k = raw[0]
    if k == "discard":
        return ("discard", tuple(int(x) for x in raw[1]))
    if k == "trade":
        return ("trade", int(raw[1]), tuple(int(x) for x in raw[2]), tuple(int(x) for x in raw[3]))
    return (k,) + tuple(int(x) for x in raw[1:])


def to_json_action(a):
    if a is None:
        return None
    if a[0] == "macro":
        return ["macro", [list(t) for t in a[1]], a[2]]
    return [list(x) if isinstance(x, tuple) else x for x in a]


class WebGame:
    def __init__(self, opponents=3, level="hard", seed=None, seat=None, name="You", review=True):
        self.seed = seed if seed is not None else random.randrange(1, 10 ** 6)
        self.level = level
        self.opponents = opponents
        n = opponents + 1
        rng = random.Random(self.seed)
        seat = (seat - 1) if seat else rng.randrange(n)
        names = AI_NAMES[:]
        rng.shuffle(names)
        names = names[:n]
        names[seat] = name or "You"
        self.human = seat
        self.s = GameState(n, seed=self.seed, names=names)
        self.agents = [None if p == seat else AIPlayer(self.s.board, level, seed=self.seed * 7 + p)
                       for p in range(n)]
        self.coach = AIPlayer(self.s.board, "hard", seed=self.seed)
        self.geometry = board_geometry(self.s.board)
        self.lock = threading.RLock()
        self.review_on = review
        self.history = []          # snapshots before each human decision
        self.reviews = []          # dicts, appended by the review worker
        self.reasons = {}          # event index -> bot reasoning
        self.pending_offer = None  # bot offer waiting for the human's answer
        self.my_offer = None       # human offer + which bots accepted
        self.epoch = 0
        self.started = time.time()
        self._jobs = queue.Queue()
        self._reviewer = AIPlayer(self.s.board, "hard", seed=self.seed)
        threading.Thread(target=self._review_worker, daemon=True).start()

    # ================================================================ views
    def view(self, since=0):
        s = self.s
        h = self.human
        players = []
        for p in range(s.n):
            players.append({
                "name": s.names[p],
                "vp": s.vp(p) if p == h or s.phase == OVER else s.public_vp(p),
                "hidden_vp": s.vp_cards(p) if p == h or s.phase == OVER else 0,
                "cards": s.hand_size(p),
                "devs": s.dev_count(p),
                "knights": s.knights[p],
                "road": s.road_len[p],
                "left": {"road": MAX_ROADS - len(s.roads[p]),
                         "settlement": MAX_SETTLEMENTS - len(s.settlements[p]),
                         "city": MAX_CITIES - len(s.cities[p])},
                "bot": self.agents[p] is not None,
            })
        evs = []
        for i in range(since, len(s.events)):
            e = s.events[i]
            turn, p, kind, msg = e
            if kind == "private" and p != h:
                continue
            if kind == "private_steal" and h not in p:
                continue
            item = {"i": i, "turn": turn, "p": p if isinstance(p, int) else None, "kind": kind,
                    "msg": second_person(msg, s.names[h])}
            if i in self.reasons:
                item["why"] = self.reasons[i]
            evs.append(item)
        out = {
            "seed": self.seed, "level": self.level, "human": h, "n": s.n,
            "phase": s.phase, "actor": s.actor(), "current": s.current, "turn": s.turn,
            "last_roll": list(s.last_roll) if s.last_roll else None,
            "robber": s.robber, "v_owner": s.v_owner, "v_level": s.v_level, "e_owner": s.e_owner,
            "players": players, "longest": s.longest, "army": s.army,
            "hand": s.hands[h], "devs": s.devs[h][:5], "new_devs": s.new_devs[h][:5],
            "bank": s.bank, "deck": s.deck_size(), "dev_played": s.dev_played,
            "free_roads": s.free_roads, "discard_owed": s.discards.get(h, 0),
            "discards_pending": {str(q): k for q, k in s.discards.items()},
            "ratios": s.ratios(h), "roll_hist": s.roll_hist, "setup_idx": s.setup_idx,
            "legal": self.legal_for_human(),
            "events": evs, "ev_total": len(s.events),
            "reviews": self.reviews, "review_on": self.review_on,
            "pending_offer": self.pending_offer, "my_offer": self.my_offer,
            "can_undo": bool(self.history), "winner": s.winner,
            "reviewing": self._jobs.unfinished_tasks,
        }
        if s.phase == OVER:
            out["summary"] = self.summary()
        return out

    def legal_for_human(self):
        s = self.s
        h = self.human
        if s.actor() != h or s.phase == OVER or self.pending_offer:
            return {}
        acts = s.legal_actions()
        L = {"settle": [], "city": [], "road": [], "robber": {}, "kinds": []}
        for a in acts:
            k = a[0]
            if k in ("settle", "city", "road"):
                L[k].append(a[1])
            elif k == "robber":
                L["robber"].setdefault(str(a[1]), [])
                if a[2] != -1:
                    L["robber"][str(a[1])].append(a[2])
            if k not in L["kinds"]:
                L["kinds"].append(k)
        return L

    def summary(self):
        s = self.s
        rows = []
        for p in sorted(range(s.n), key=lambda q: -s.vp(q)):
            rows.append({"p": p, "vp": s.vp(p), "settlements": len(s.settlements[p]),
                         "cities": len(s.cities[p]), "longest": s.longest == p,
                         "army": s.army == p, "vp_cards": s.vp_cards(p)})
        revs = self.reviews
        counts = {name: 0 for _, name in VERDICTS}
        for r in revs:
            counts[r["verdict"]] += 1
        acc = 100.0 * sum(1 for r in revs if r["delta"] < 0.2) / len(revs) if revs else None
        worst = [r for r in sorted(revs, key=lambda r: -r["delta"])[:5] if r["delta"] >= 0.2]
        return {"rows": rows, "counts": counts, "accuracy": acc,
                "lost": sum(r["delta"] for r in revs), "worst": worst, "turns": s.turn}

    # ================================================================ bots
    def step(self):
        """Advance one bot decision. Returns True if something happened."""
        s = self.s
        if s.phase == OVER or self.pending_offer:
            return False
        p = s.actor()
        if p == self.human:
            return False
        a, why = self.agents[p].decide(s)
        if a[0] == "propose":
            self._bot_propose(p, a[1], a[2], why)
            return True
        n0 = len(s.events)
        s.apply(a)
        if why and a[0] not in ("roll", "end") and len(s.events) > n0:
            for i in range(n0, len(s.events)):
                if s.events[i][2] not in ("private", "private_steal"):
                    self.reasons[i] = why
                    break
        return True

    def _bot_propose(self, p, give, get, why):
        s = self.s
        s.log(f"{s.names[p]} offers {fmt_cards(give)} for {fmt_cards(get)}", "offer", p)
        self.reasons[len(s.events) - 1] = why
        answers = {}
        for q in range(s.n):
            if q == p or q == self.human:
                continue
            ok = False
            if all(s.hands[q][i] >= get[i] for i in range(5)):
                ok, _ = self.agents[q].evaluate_offer(s, q, p, give, get)
            answers[q] = ok
        if all(s.hands[self.human][i] >= get[i] for i in range(5)):
            # the human sees the offer from their own side: receive `give`, pay `get`
            self.pending_offer = {"from": p, "give": list(give), "get": list(get),
                                  "answers": {str(q): ok for q, ok in answers.items()}}
            return
        self._finish_offer(p, give, get, answers)

    def _finish_offer(self, p, give, get, answers):
        s = self.s
        accepters = [q for q in sorted(answers) if answers[q]]
        for q in sorted(answers):
            s.log(f"{s.names[q]} {'accepts' if answers[q] else 'declines'}", "reply", q)
        if accepters:
            partner = min(accepters, key=lambda q: (s.public_vp(q), s.hand_size(q)))
            s.apply(("trade", partner, tuple(give), tuple(get)))

    def respond(self, accept):
        po = self.pending_offer
        if not po:
            raise IllegalAction("no offer to answer")
        s = self.s
        p, give, get = po["from"], tuple(po["give"]), tuple(po["get"])
        self.snapshot()
        if self.review_on:
            self._queue("offer", (p, give, get, bool(accept)))
        answers = {int(q): ok for q, ok in po["answers"].items()}
        answers[self.human] = bool(accept)
        self.pending_offer = None
        self._finish_offer(p, give, get, answers)

    # ================================================================ human
    def act(self, raw):
        s = self.s
        if s.phase == OVER:
            raise IllegalAction("the game is over")
        if s.actor() != self.human or self.pending_offer:
            raise IllegalAction("not your decision right now")
        a = to_action(raw)
        if a[0] == "trade":
            mo = self.my_offer
            if not mo or a[1] not in mo["accepted"] or list(a[2]) != mo["give"] or list(a[3]) != mo["get"]:
                raise IllegalAction("that player hasn't accepted this trade")
        if a[0] in ("discard", "trade"):
            s.clone(sim=False).apply(a)  # raises if illegal
        elif a not in s.legal_actions():
            s.clone(sim=False).apply(a)  # get a helpful message ...
            raise IllegalAction("not a legal move right now")  # ... or a generic one
        self.snapshot()
        if self.review_on and not self.trivial(a):
            self._queue("discard" if a[0] == "discard" else "action", a)
        self.my_offer = None
        s.apply(a)

    def offer(self, give, get):
        s = self.s
        h = self.human
        if s.phase != MAIN or s.actor() != h:
            raise IllegalAction("you can only offer trades after rolling, on your turn")
        give, get = [int(x) for x in give], [int(x) for x in get]
        if not any(give) or not any(get) or any(x < 0 for x in give + get):
            raise IllegalAction("pick cards on both sides")
        if any(give[r] and get[r] for r in range(5)):
            raise IllegalAction("don't give and ask for the same resource")
        if any(give[r] > s.hands[h][r] for r in range(5)):
            raise IllegalAction("you don't have those cards")
        replies = []
        for q in range(s.n):
            if q == h:
                continue
            ok, _ = self.agents[q].evaluate_offer(s, q, h, tuple(give), tuple(get))
            replies.append({"p": q, "ok": bool(ok)})
        self.my_offer = {"give": give, "get": get, "replies": replies,
                         "accepted": [r["p"] for r in replies if r["ok"]]}
        return self.my_offer

    def trivial(self, a):
        s = self.s
        h = self.human
        if s.phase == PRE_ROLL:
            return not s.playable(h, KNIGHT)
        if s.phase == MAIN and a[0] == "end":
            return all(x[0] == "end" for x in s.legal_actions(include_trades=True))
        if s.phase in (SETUP_ROAD, ROAD_BUILD):
            return len(s.legal_actions()) <= 1
        return False

    # ================================================================ undo
    def snapshot(self):
        agents = [(a.rng.getstate(), a._proposed) if a else None for a in self.agents]
        self.history.append((self.s.clone(sim=False), agents, len(self.reviews),
                             self.pending_offer, dict(self.reasons)))

    def undo(self):
        if not self.history:
            raise IllegalAction("nothing to undo")
        state, agents, nrev, offer, reasons = self.history.pop()
        self.s = state
        for a, saved in zip(self.agents, agents):
            if a:
                a.rng.setstate(saved[0])
                a._proposed = saved[1]
        self.epoch += 1
        del self.reviews[nrev:]
        self.pending_offer = offer
        self.my_offer = None
        self.reasons = reasons

    # ================================================================ coach
    def hint(self):
        s = self.s
        if s.actor() != self.human and not self.pending_offer:
            raise IllegalAction("wait for your turn")
        t0 = time.time()
        if self.pending_offer:
            po = self.pending_offer
            ok, gain = self.coach.evaluate_offer(s, self.human, po["from"], tuple(po["give"]), tuple(po["get"]))
            return {"offer": True, "accept": bool(ok), "gain": gain, "secs": time.time() - t0}
        ranked = self.coach.analyze(s, top=5)
        if not ranked:
            return {"options": [], "secs": time.time() - t0}
        best = ranked[0][0]
        opts = []
        for sc, a, why in ranked:
            text = why if s.phase == MAIN else f"{describe(s, a)} — {why}"
            opts.append({"loss": best - sc, "action": to_json_action(a), "text": text})
        return {"options": opts, "secs": time.time() - t0}

    def evaluate(self):
        s = self.s
        vals = self.coach.ev.values(self.coach.view(s, self.human), self.human)
        rows = []
        for p in range(s.n):
            prod = self.coach.ev.production(s, p)
            rows.append({"p": p, "value": vals[p], "pips": [round(x, 1) for x in prod]})
        return {"rows": rows}

    # ================================================================ reviews
    def _queue(self, kind, payload):
        state = self.s.clone(sim=False)
        self._jobs.put((self.epoch, len(self.history), kind, state, payload))

    def _review_worker(self):
        while True:
            job = self._jobs.get()
            try:
                rev = self._review(*job)
                if rev:
                    with self.lock:
                        if job[0] == self.epoch:
                            self.reviews.append(rev)
            except Exception as ex:  # a review must never break the game
                print("review failed:", ex)
            finally:
                self._jobs.task_done()

    def _review(self, epoch, seq, kind, s, payload):
        if epoch != self.epoch:
            return None
        coach = self._reviewer
        h = self.human
        if kind == "offer":
            p, give, get, yes = payload
            ok, gain = coach.evaluate_offer(s, h, p, give, get)
            mine, best = (gain if yes else 0.0), max(gain, 0.0)
            best_desc = f"{'accept' if gain > 0 else 'decline'} (trade worth {gain:+.2f} to you)"
            label = f"{'accept' if yes else 'decline'} {s.names[p]}'s offer"
        elif kind == "discard":
            counts = list(payload[1])
            (best_v, best_c), (mine, _) = coach.rank_discards(s, h, counts)
            best, best_desc, label = best_v, "discard " + fmt_cards(best_c), "discard " + fmt_cards(counts)
        else:
            a = payload
            best, best_a, why = coach.best_value(s)
            if best_a is None:
                return None
            same = a == best_a or (best_a[0] == "macro" and a == best_a[1][0])
            mine = best if same else coach.value_of_action(s, a)
            best_desc = describe(s, best_a)
            if s.phase == MAIN and why.startswith("plan:"):
                best_desc = why[6:]
            elif why and s.phase in (SETUP_SETTLE, SETUP_ROAD, ROBBER):
                best_desc += f" — {why}"
            label = describe(s, a)
            if a[0] == "trade":
                label = f"trade {fmt_cards(a[2])} for {fmt_cards(a[3])} with {s.names[a[1]]}"
        delta = max(0.0, best - mine)
        mine_a = to_json_action(payload) if kind == "action" else None
        best_a = to_json_action(best_a) if kind == "action" else None
        return {"seq": seq, "action": mine_a, "best_action": best_a, "turn": s.turn, "phase": s.phase, "label": label, "best": best_desc,
                "delta": round(delta, 3), "verdict": verdict(delta)}


"""One web game: engine state, bots, coach, undo history and async move reviews.

Everything the browser sees goes through `WebGame.view()`, which shows only what
the human could know at a real table: their own hand and dev cards, opponents'
card *counts*, and the public log.
"""

import math
import queue
import re
import random
import threading
import time

from ..ai import AIPlayer, describe
from ..cli import VERDICTS
from ..constants import DESERT, MAX_CITIES, MAX_ROADS, MAX_SETTLEMENTS, RES_NAMES
from ..game import (MAIN, OVER, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD, SETUP_SETTLE,
                    GameState, IllegalAction, fmt_cards)
from ..render import second_person

AI_NAMES = ["Vera", "Magnus", "Kasimir", "Ingrid", "Tobias", "Selene"]
HEX_R = 60.0


BUILD_STEP = {"city": "city", "settlement": "settle", "road": "road", "dev": "buy_dev"}


def parse_step(text):
    """One step of an engine plan ('trade 4 wood -> ore', 'city at 12 [..]', 'road 30')
    back to an action, or None when it isn't a simple build or bank trade."""
    text = text.strip()
    m = re.fullmatch(r"trade \d+ (\w+) -> (\w+)", text)
    if m and m[1] in RES_NAMES and m[2] in RES_NAMES:
        return ["bank", RES_NAMES.index(m[1]), RES_NAMES.index(m[2])]
    m = re.match(r"(settlement|city) at (\d+)\b", text)
    if m:
        return ["settle" if m[1] == "settlement" else "city", int(m[2])]
    m = re.fullmatch(r"road (\d+)", text)
    if m:
        return ["road", int(m[1])]
    if text == "buy development card":
        return ["buy_dev"]
    return None


def verdict(delta):
    return next(v[1] for v in VERDICTS if delta < v[0])


def port_name(kind):
    return None if kind is None else "any" if kind == "any" else RES_NAMES[kind]


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
    return {
        "hexes": hexes,
        "vertices": [{"x": x, "y": y, "pips": b.vertex_total_pips[v], "hexes": list(b.vertex_hexes[v]),
                      "port": port_name(b.vertex_port[v])}
                     for v, (x, y) in enumerate(vxy)],
        "edges": [list(pr) for pr in b.edge_vertices],
        "ports": [{"kind": port_name(kind), "v": list(b.edge_vertices[e])} for e, kind in b.ports],
        "radius": HEX_R,
    }


# number of integer arguments each simple action takes
ARITY = {"settle": 1, "road": 1, "city": 1, "buy_dev": 0, "play_knight": 0, "play_road": 0,
         "play_mono": 1, "play_yop": 2, "roll": 0, "robber": 2, "bank": 2, "end": 0,
         "end_road_building": 0}


def _int(x):
    if isinstance(x, bool) or not isinstance(x, int):
        raise IllegalAction("malformed action")
    return x


def cards(raw):
    """Exactly five non-negative ints."""
    if not isinstance(raw, list) or len(raw) != 5:
        raise IllegalAction("card lists need exactly 5 counts")
    out = tuple(_int(x) for x in raw)
    if any(x < 0 for x in out):
        raise IllegalAction("negative card counts")
    return out


def to_action(raw):
    """JSON list -> engine tuple, rejecting anything malformed."""
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise IllegalAction("malformed action")
    k = raw[0]
    if k == "discard" and len(raw) == 2:
        return ("discard", cards(raw[1]))
    if k == "trade" and len(raw) == 4:
        return ("trade", _int(raw[1]), cards(raw[2]), cards(raw[3]))
    if k not in ARITY or len(raw) != ARITY[k] + 1:
        raise IllegalAction("malformed action")
    return (k,) + tuple(_int(x) for x in raw[1:])


def public_reason(why):
    """A bot's plan names later steps (e.g. '-> play Knight') that would reveal its hidden
    cards; keep only the move it actually made."""
    if not why or not why.startswith("plan:"):
        return why
    first = why[5:].split("  (eval")[0].split(" -> ")[0].strip()
    ev = why.rsplit("(eval", 1)
    tail = f"  (eval{ev[1]}" if len(ev) == 2 else ""
    if "+99" in tail or len(tail) > 18:  # a winning move scores huge; the number means nothing
        tail = ""
    return f"plan: {first}" + tail


class WebGame:
    def __init__(self, opponents=3, level="hard", seed=None, seat=None, name="You", review=True):
        self.seed = seed if seed is not None else random.randrange(1, 10 ** 6)
        self.level = level
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
        self.coach = AIPlayer(self.s.board, "hard", seed=self.seed)  # hints; the worker has its own
        self.geometry = board_geometry(self.s.board)
        self.lock = threading.RLock()
        self.review_on = review
        self.history = []          # snapshots before each human decision
        self.reviews = []          # dicts, appended by the review worker
        self.reasons = {}          # event index -> bot reasoning
        self.pending_offer = None  # bot offer waiting for the human's answer
        self.my_offer = None       # human offer + which bots accepted
        self._token = 0            # id of the newest human decision
        self.gid = f"{self.seed}-{time.time_ns()}"
        self._jobs = queue.Queue()
        self._reviewer = AIPlayer(self.s.board, "hard", seed=self.seed)
        self._alive = True
        threading.Thread(target=self._review_worker, daemon=True).start()

    def close(self):
        """Stop the review worker (called when a new game replaces this one)."""
        self._alive = False
        self._jobs.put(None)

    # ================================================================ views
    def view(self, since=0):
        s = self.s
        h = self.human
        players = []
        for p in range(s.n):
            shown = p == h or s.phase == OVER
            players.append({
                "name": s.names[p],
                "vp": s.vp(p) if shown else s.public_vp(p),
                "hidden_vp": s.vp_cards(p) if shown else 0,
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
            turn, p, kind, msg = s.events[i]
            if kind == "private" and p != h:
                continue
            if kind == "private_steal" and h not in p:
                continue
            item = {"i": i, "turn": turn, "p": p if isinstance(p, int) else None, "kind": kind,
                    "msg": self.you(msg)}
            if kind == "private_steal":
                item["thief"], item["victim"] = p
            if i in self.reasons:
                item["why"] = self.you(self.reasons[i])
            evs.append(item)
        out = {
            "gid": self.gid, "seed": self.seed, "level": self.level, "human": h, "n": s.n,
            "phase": s.phase, "actor": s.actor(), "current": s.current, "turn": s.turn,
            "round": self.round(),
            "last_roll": list(s.last_roll) if s.last_roll else None,
            "robber": s.robber, "v_owner": s.v_owner, "v_level": s.v_level, "e_owner": s.e_owner,
            "players": players, "longest": s.longest, "army": s.army,
            "hand": s.hands[h], "devs": s.devs[h][:5], "new_devs": s.new_devs[h][:5],
            "bank": s.bank, "deck": s.deck_size(), "dev_played": s.dev_played,
            "free_roads": s.free_roads, "discard_owed": s.discards.get(h, 0),
            "discards_pending": {str(q): k for q, k in s.discards.items()},
            "ratios": s.ratios(h), "roll_hist": s.roll_hist, "setup_idx": s.setup_idx,
            "tracker": self.tracker(),
            "legal": self.legal_for_human(),
            "events": evs, "ev_total": len(s.events),
            "reviews": sorted(self.reviews, key=lambda r: r["token"]), "review_on": self.review_on,
            "pending_offer": ({k: self.pending_offer[k] for k in ("from", "give", "get")}
                              if self.pending_offer else None),
            "my_offer": self.my_offer,
            "can_undo": bool(self.history), "winner": s.winner,
            "reviewing": self._jobs.unfinished_tasks,
        }
        if s.phase == OVER:
            out["summary"] = self.summary()
        return out

    def you(self, msg):
        """The log in second person: 'Tester steals a card from Vera' -> 'You steal a card
        from Vera', 'Vera steals a card from Tester' -> '... from you'."""
        name = self.s.names[self.human]
        if name != "You" and self.s.names.count(name) == 1:
            msg = re.sub(rf"(?<![\w']){re.escape(name)}(?!\w)", "You", msg)
        msg = second_person(msg, "You")
        return msg.replace("You's ", "Your ").replace("you's ", "your ")

    def round(self):
        """Turn counter -> table round (every player has one turn per round); 0 in setup."""
        s = self.s
        return (s.turn - 1) // s.n + 1 if s.turn else 0

    def tracker(self):
        """The human's card tracker: how many of each resource the opponents hold in
        total, as far as public events tell (the same belief the bots keep)."""
        s, h = self.s, self.human
        if s.belief is None:
            return None
        return [round(sum(s.belief[h][q][r] for q in range(s.n) if q != h), 1) for r in range(5)]

    def legal_for_human(self):
        s = self.s
        h = self.human
        if s.actor() != h or s.phase == OVER or self.pending_offer:
            return {}
        L = {"settle": [], "city": [], "road": [], "robber": {}, "kinds": []}
        for a in s.legal_actions():
            k = a[0]
            if k in ("settle", "city", "road"):
                L[k].append(a[1])
            elif k == "robber":
                victims = L["robber"].setdefault(str(a[1]), [])
                if a[2] != -1:
                    victims.append(a[2])
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
        revs = [r for r in self.reviews if not r.get("forced")]
        counts = {v[1]: 0 for v in VERDICTS}
        for r in revs:
            counts[r["verdict"]] += 1
        acc = 100.0 * sum(1 for r in revs if r["delta"] < 0.2) / len(revs) if revs else None
        worst = [r for r in sorted(revs, key=lambda r: -r["delta"])[:5] if r["delta"] >= 0.2]
        return {"rows": rows, "counts": counts, "accuracy": acc,
                "lost": sum(r["delta"] for r in revs), "worst": worst, "turns": s.turn,
                "rounds": self.round()}

    # ================================================================ bots
    def step(self):
        """Advance one bot decision, if it is a bot's turn."""
        s = self.s
        p = s.actor()
        if s.phase == OVER or self.pending_offer or p == self.human:
            return
        a, why = self.agents[p].decide(s)
        if a[0] == "propose":
            return self._bot_propose(p, a[1], a[2], why)
        n0 = len(s.events)
        s.apply(a)
        why = public_reason(why)
        if why and a[0] not in ("roll", "end") and len(s.events) > n0:
            for i in range(n0, len(s.events)):
                if s.events[i][2] not in ("private", "private_steal"):
                    self.reasons[i] = why
                    break

    def _bot_propose(self, p, give, get, why):
        s = self.s
        s.log(f"{s.names[p]} offers {fmt_cards(give)} for {fmt_cards(get)}", "offer", p)
        self.reasons[len(s.events) - 1] = public_reason(why)

        def can_pay(q):
            return all(s.hands[q][i] >= get[i] for i in range(5))

        answers = {q: can_pay(q) and self.agents[q].evaluate_offer(s, q, p, give, get)[0]
                   for q in range(s.n) if q not in (p, self.human)}
        if can_pay(self.human):
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
        p, give, get = po["from"], tuple(po["give"]), tuple(po["get"])
        self.snapshot(f"{'accept' if accept else 'decline'} {self.s.names[p]}'s offer")
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
        forced = self.trivial(a)
        label = describe(s, a)
        if a[0] == "trade":
            label = f"trade {fmt_cards(a[2])} for {fmt_cards(a[3])} with {s.names[a[1]]}"
        elif a[0] == "discard":
            label = "discard " + fmt_cards(a[1])
        self.snapshot(label)
        if self.review_on:
            if forced:
                self.reviews.append({"token": self._token, "turn": s.turn, "phase": s.phase,
                                     "label": label, "best": "only sensible move", "delta": 0.0,
                                     "verdict": "forced", "forced": True,
                                     "action": a, "best_action": a})
            else:
                self._queue("discard" if a[0] == "discard" else "action", a)
        self.my_offer = None
        s.apply(a)

    def offer(self, give, get):
        s = self.s
        h = self.human
        if s.phase != MAIN or s.actor() != h:
            raise IllegalAction("you can only offer trades after rolling, on your turn")
        give, get = list(cards(give)), list(cards(get))
        if not any(give) or not any(get):
            raise IllegalAction("pick cards on both sides")
        if any(give[r] and get[r] for r in range(5)):
            raise IllegalAction("don't give and ask for the same resource")
        if any(give[r] > s.hands[h][r] for r in range(5)):
            raise IllegalAction("you don't have those cards")
        replies = []
        for q in range(s.n):
            if q == h:
                continue
            ok, gain = self.agents[q].evaluate_offer(s, q, h, tuple(give), tuple(get))
            replies.append({"p": q, "ok": bool(ok), "why": self.reply_reason(q, get, ok, gain)})
        self.my_offer = {"give": give, "get": get, "replies": replies,
                         "accepted": [r["p"] for r in replies if r["ok"]]}
        return self.my_offer

    def reply_reason(self, q, get, ok, gain):
        """One line on why bot q answered the human's offer that way (gain is the
        trade's value to q, from evaluate_offer)."""
        s = self.s
        if not s.can_afford(q, get):
            return "not interested"  # saying why would reveal their hidden hand
        if ok:
            return None
        if gain > 0.05 and s.public_vp(self.human) >= 8:
            return "won't trade with the leader"
        if gain < -0.05:
            return "helps you more than them"
        return "not worth it to them"

    def trivial(self, a):
        s = self.s
        if s.phase in (PRE_ROLL, SETUP_ROAD, ROAD_BUILD):
            return len(s.legal_actions()) <= 1
        if s.phase == MAIN and a[0] == "end":
            return all(x[0] == "end" for x in s.legal_actions(include_trades=True))
        return False

    # ================================================================ undo
    def snapshot(self, label=""):
        agents = [(a.rng.getstate(), a._proposed) if a else None for a in self.agents]
        self._token += 1
        self.history.append((self.s.clone(sim=False), agents, self._token,
                             self.pending_offer, dict(self.reasons), label))

    def undo(self):
        """Step back one human decision; returns what that decision was."""
        if not self.history:
            raise IllegalAction("nothing to undo")
        state, agents, token, offer, reasons, label = self.history.pop()
        self.s = state
        for a, saved in zip(self.agents, agents):
            if a:
                a.rng.setstate(saved[0])
                a._proposed = saved[1]
        # the undone decision's review goes; reviews of earlier moves stay (even pending ones)
        self.reviews = [r for r in self.reviews if r["token"] != token]
        self.pending_offer = offer
        self.my_offer = None
        self.reasons = reasons
        return self.you(label)

    # ================================================================ coach
    def hint(self):
        s = self.s
        if s.actor() != self.human and not self.pending_offer:
            raise IllegalAction("wait for your turn")
        t0 = time.time()
        if self.pending_offer:
            po = self.pending_offer
            ok, gain = self.coach.evaluate_offer(s, self.human, po["from"], tuple(po["give"]), tuple(po["get"]))
            return {"accept": bool(ok), "gain": gain, "secs": time.time() - t0}
        ranked = self.coach.analyze(s, top=6)
        if not ranked:
            return {"options": [], "secs": time.time() - t0}
        best = ranked[0][0]
        opts, seen = [], set()
        for sc, a, why in ranked:
            if s.phase == MAIN:
                steps, text, more = self.playable_steps(a, why)
            else:
                steps, text, more = [list(a)], f"{describe(s, a)} — {why}", False
            if text in seen:  # e.g. a bank trade then a city, and the same as one macro
                continue
            seen.add(text)
            opts.append({"loss": best - sc, "action": a, "steps": steps, "more": more, "text": text})
        return {"options": opts[:5], "secs": time.time() - t0}

    def playable_steps(self, a, plan):
        """The first part of a main-phase plan that one Play click can carry out: a single
        move, or bank trades together with the build they pay for (a macro is expanded to
        its trades and the spot the engine would build on). Returns (steps, plan text with
        the macro spelled out, whether the plan goes on after those steps)."""
        s, h = self.s, self.human
        parts = plan.split(" -> ")
        if a[0] == "macro":
            t = self.coach.view(s, h)
            steps = []
            for tr in a[1]:
                t.apply(tr)
                steps.append(list(tr))
            kind = BUILD_STEP[a[2]]
            builds = [b for b in t.legal_actions(include_trades=False) if b[0] == kind]
            if builds:
                steps.append(list(max(builds, key=lambda b: self.coach._score_after(t, b, h))))
            parts = [describe(t if st[0] != "bank" else s, tuple(st)) for st in steps] + parts[1:]
            used = len(steps)
        else:
            steps, used = [list(a)], 1
            # trades followed by the build they pay for go together
            if a[0] == "bank":
                for part in parts[1:]:
                    st = parse_step(part)
                    if not st:
                        break
                    steps.append(st)
                    used += 1
                    if st[0] != "bank":
                        break
                if steps[-1][0] == "bank":
                    steps, used = steps[:1], 1
        # keep only the prefix that is legal on the real game
        t = s.clone(sim=False)
        ok = 0
        for st in steps:
            try:
                t.apply(tuple(st))
            except Exception:
                break
            ok += 1
        if ok < len(steps):
            steps, used = steps[:max(ok, 1)], max(ok, 1)
        return steps, " -> ".join(parts), used < len(parts)

    def evaluate(self):
        s = self.s
        vals = self.coach.ev.values(self.coach.view(s, self.human), self.human)
        rows = []
        for p in range(s.n):
            prod = self.coach.ev.production(s, p)
            rows.append({"p": p, "value": vals[p], "pips": [round(x, 1) for x in prod]})
        return {"rows": rows}

    # ================================================================ reviews
    def _live(self, token):
        return any(h[2] == token for h in self.history)

    def _queue(self, kind, payload):
        self._jobs.put((self._token, kind, self.s.clone(sim=False), payload))

    def _review_worker(self):
        while True:
            job = self._jobs.get()
            if job is None or not self._alive:
                self._jobs.task_done()
                return
            try:
                rev = self._review(*job) if self._live(job[0]) else None
                if rev:
                    with self.lock:
                        if self._live(job[0]):
                            self.reviews.append(rev)
            except Exception as ex:  # a review must never break the game
                print("review failed:", ex)
            finally:
                self._jobs.task_done()

    def _review(self, token, kind, s, payload):
        coach = self._reviewer
        h = self.human
        mine_a = best_a = None  # only move reviews carry actions to show on the board
        if kind == "offer":
            p, give, get, yes = payload
            ok, gain = coach.evaluate_offer(s, h, p, give, get)
            # same rule as the hint (it refuses to feed a player on 8+ VP)
            mine, best = (gain if yes else 0.0), (gain if ok else 0.0)
            best_desc = f"{'accept' if ok else 'decline'} (trade worth {gain:+.2f} to you)"
            label = f"{'accept' if yes else 'decline'} {s.names[p]}'s offer"
        elif kind == "discard":
            counts = list(payload[1])
            (best_v, best_c), (mine, _) = coach.rank_discards(s, h, counts)
            best, best_desc, label = best_v, "discard " + fmt_cards(best_c), "discard " + fmt_cards(counts)
        else:
            a = mine_a = payload
            if a[0] == "trade":
                # the partner accepted, so it certainly holds the cards it gives
                b = s.belief[h][a[1]]
                for r in range(5):
                    b[r] = max(b[r], float(a[3][r]))
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
        return {"token": token, "action": mine_a, "best_action": best_a, "turn": s.turn, "phase": s.phase,
                "label": label, "best": best_desc, "delta": round(delta, 3), "verdict": verdict(delta),
                "forced": False}


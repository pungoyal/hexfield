"""Web games: engine state, bots, coach, undo, saved games, replays and move reviews.

Everything a browser sees goes through `Table.view(seat)`, which shows only what that
player could know at a real table: their own hand and dev cards, opponents' card
*counts*, and the public log. A game can seat several humans (hot-seat on one device,
or online with one device each); every human seat has a secret token, and a request
acts for the seat its token belongs to.

`WebGame` is a live game. Every decision is written to its `Record` (record.py) and
saved, so a game survives a restart. `Replay` steps through a finished or unfinished
record without playing it.
"""

import math
import queue
import re
import random
import secrets
import threading
import time

from .. import store
from ..ai import LEVELS, STYLES, AIPlayer, describe
from ..cli import VERDICTS
from ..constants import BADLANDS, MAX_CITIES, MAX_ROADS, MAX_SETTLEMENTS, RES_NAMES
from ..game import (DICE_MODES, LAYOUTS, MAIN, OVER, PRE_ROLL, ROAD_BUILD, BANDIT, SETUP_ROAD,
                    SETUP_SETTLE, SPECIAL, IllegalAction, fmt_cards)
from ..match import AI_NAMES, bot_counters, clean_name, pick_partner, public_reason, seat_plan
from ..record import Record
from ..render import second_person

MODES = ("solo", "hotseat", "online")
COUNTER_TRIES = 2  # counter-offers a human may make to one bot offer

HEX_R = 60.0


BUILD_STEP = {"city": "city", "settlement": "settle", "road": "road", "dev": "buy_dev"}


def parse_step(text):
    """One step of an engine plan ('trade 4 timber -> stone', 'city at 12 [..]', 'road 30')
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
            "res": None if r == BADLANDS else RES_NAMES[r],
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
ARITY = {"settle": 1, "road": 1, "city": 1, "buy_dev": 0, "play_ranger": 0, "play_road": 0,
         "play_hoard": 1, "play_windfall": 2, "roll": 0, "bandit": 2, "bank": 2, "end": 0,
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


class Table:
    """A game position with everything needed to show it to one seat. Subclasses:
    WebGame (live play) and Replay (a record being stepped through)."""

    live = False

    def _table(self, rec, gid):
        self.rec = rec
        self.gid = gid
        self.seed = rec.seed
        self.s = rec.new_state()
        self.humans = rec.humans
        self.human = self.humans[0] if self.humans else 0   # the seat requests default to
        self.mode = rec.meta.get("mode", "solo")
        self.geometry = board_geometry(self.s.board)
        self.coach = AIPlayer(self.s.board, "hard", seed=self.seed)
        self.reasons = {}        # event index -> bot reasoning
        self.offer = None        # the trade offer on the table, if any
        self.history = []        # undo points: dicts, one per human decision
        self.review_on = {h: True for h in self.humans}
        self.open = set()        # online: human seats nobody has taken yet
        self.version = 0

    @property
    def reviews(self):
        return self.rec.reviews

    @property
    def level(self):
        return next((x.get("level") for x in self.rec.seats if x["kind"] == "bot"), "hard")

    def is_bot(self, p):
        return self.rec.seats[p]["kind"] == "bot"

    # ================================================================ views
    def view(self, seat=None, since=0, reveal=False):
        s = self.s
        h = self.human if seat is None else seat
        players = []
        for p in range(s.n):
            shown = p == h or s.phase == OVER or reveal
            info = self.rec.seats[p]
            pl = {
                "name": s.names[p],
                "vp": s.vp(p) if shown else s.public_vp(p),
                "hidden_vp": s.vp_cards(p) if shown else 0,
                "cards": s.hand_size(p),
                "devs": s.dev_count(p),
                "rangers": s.rangers[p],
                "road": s.road_len[p],
                "left": {"road": MAX_ROADS - len(s.roads[p]),
                         "settlement": MAX_SETTLEMENTS - len(s.settlements[p]),
                         "city": MAX_CITIES - len(s.cities[p])},
                "bot": info["kind"] == "bot",
                "level": info.get("level"), "style": info.get("style"),
                "open": p in self.open,
            }
            if reveal:
                pl["hand"] = list(s.hands[p])
                pl["dev_cards"] = [s.devs[p][k] + s.new_devs[p][k] for k in range(5)]
            players.append(pl)
        evs = []
        for i in range(since, len(s.events)):
            turn, p, kind, msg = s.events[i]
            if kind == "private" and p != h and not reveal:
                continue
            if kind == "private_steal" and h not in p and not reveal:
                continue
            item = {"i": i, "turn": turn, "p": p if isinstance(p, int) else None, "kind": kind,
                    "msg": self.you(msg, h)}
            if kind == "private_steal":
                item["thief"], item["victim"] = p
            if i in self.reasons:
                item["why"] = self.you(self.reasons[i], h)
            evs.append(item)
        po = self.pending_for(h)
        revs = [r for r in self.reviews if r.get("seat") == h]
        out = {
            "gid": self.gid, "seed": self.seed, "level": self.level, "human": h, "n": s.n,
            "mode": self.mode, "options": self.rec.options, "large": s.board.large,
            "special_build": s.special_build, "version": self.version,
            "phase": s.phase, "actor": s.actor() if s.phase != OVER else s.current,
            "current": s.current, "turn": s.turn, "round": self.round(),
            "last_roll": list(s.last_roll) if s.last_roll else None,
            "bandit": s.bandit, "v_owner": s.v_owner, "v_level": s.v_level, "e_owner": s.e_owner,
            "players": players, "longest": s.longest, "patrol": s.patrol,
            "hand": s.hands[h], "devs": s.devs[h][:5], "new_devs": s.new_devs[h][:5],
            "bank": s.bank, "deck": s.deck_size(), "dev_played": s.dev_played,
            "free_roads": s.free_roads, "discard_owed": s.discards.get(h, 0),
            "discards_pending": {str(q): k for q, k in s.discards.items()},
            "ratios": s.ratios(h), "roll_hist": s.roll_hist, "setup_idx": s.setup_idx,
            "tracker": self.tracker(h),
            "legal": self.legal_for(h),
            "events": evs, "ev_total": len(s.events),
            "reviews": sorted(revs, key=lambda r: r["token"]), "review_on": self.review_on.get(h, False),
            "pending_offer": po, "my_offer": self.my_offer_for(h),
            "waiting_on": self.waiting_on(), "lobby": bool(self.open),
            "can_undo": self.live and len(self.humans) == 1 and bool(self.history), "winner": s.winner,
            "reviewing": self.reviewing(),
        }
        if s.phase == OVER:
            out["summary"] = self.summary(h)
        return out

    def reviewing(self):
        return 0

    def you(self, msg, seat=None):
        """The log in second person: 'Tester steals a card from Vera' -> 'You steal a card
        from Vera', 'Vera steals a card from Tester' -> '... from you'."""
        name = self.s.names[self.human if seat is None else seat]
        if name != "You" and self.s.names.count(name) == 1:
            msg = re.sub(rf"(?<![\w']){re.escape(name)}(?!\w)", "You", msg)
        msg = second_person(msg, "You")
        return msg.replace("You's ", "Your ").replace("you's ", "your ")

    def round(self):
        """Turn counter -> table round (every player has one turn per round); 0 in setup."""
        s = self.s
        return (s.turn - 1) // s.n + 1 if s.turn else 0

    def tracker(self, h=None):
        """A player's card tracker: how many of each resource the opponents hold in
        total, as far as public events tell (the same belief the bots keep)."""
        s = self.s
        h = self.human if h is None else h
        if s.belief is None:
            return None
        return [round(sum(s.belief[h][q][r] for q in range(s.n) if q != h), 1) for r in range(5)]

    def waiting_on(self):
        """The human seats the game is waiting for right now."""
        s = self.s
        if s.phase == OVER or self.open:
            return []
        out = [q for q, a in (self.offer["answers"].items() if self.offer else ()) if a is None]
        if not (self.offer and self.offer["bot"]) and not self.is_bot(s.actor()):
            out.append(s.actor())
        return sorted(set(out))

    def pending_for(self, h):
        """The offer seat h still has to answer (seen from the proposer's side: h
        receives `give` and pays `get`)."""
        o = self.offer
        if not o or o["answers"].get(h, 0) is not None:
            return None
        return {"from": o["from"], "give": list(o["give"]), "get": list(o["get"]),
                "counters_left": COUNTER_TRIES - o["tries"].get(h, 0) if o["bot"] else 1,
                "note": o["notes"].get(h)}

    def my_offer_for(self, h):
        o = self.offer
        if not o or o["bot"] or o["from"] != h:
            return None
        replies = [{"p": q, "ok": o["answers"][q], "why": o["notes"].get(q), "counter": o["counters"].get(q)}
                   for q in sorted(o["answers"])]
        return {"give": list(o["give"]), "get": list(o["get"]), "replies": replies,
                "accepted": [r["p"] for r in replies if r["ok"]]}

    def legal_for(self, h):
        s = self.s
        if (not self.live or s.phase == OVER or self.open or s.actor() != h
                or (self.offer and self.offer["bot"])):
            return {}
        L = {"settle": [], "city": [], "road": [], "bandit": {}, "kinds": []}
        for a in s.legal_actions():
            k = a[0]
            if k in ("settle", "city", "road"):
                L[k].append(a[1])
            elif k == "bandit":
                victims = L["bandit"].setdefault(str(a[1]), [])
                if a[2] != -1:
                    victims.append(a[2])
            if k not in L["kinds"]:
                L["kinds"].append(k)
        return L

    def summary(self, h=None):
        s = self.s
        h = self.human if h is None else h
        rows = []
        for p in sorted(range(s.n), key=lambda q: -s.vp(q)):
            rows.append({"p": p, "vp": s.vp(p), "settlements": len(s.settlements[p]),
                         "cities": len(s.cities[p]), "longest": s.longest == p,
                         "patrol": s.patrol == p, "vp_cards": s.vp_cards(p)})
        revs = [r for r in self.reviews if r.get("seat") == h and not r.get("forced")]
        counts = {v[1]: 0 for v in VERDICTS}
        for r in revs:
            counts[r["verdict"]] += 1
        acc = 100.0 * sum(1 for r in revs if r["delta"] < 0.2) / len(revs) if revs else None
        worst = [r for r in sorted(revs, key=lambda r: -r["delta"])[:5] if r["delta"] >= 0.2]
        return {"rows": rows, "counts": counts, "accuracy": acc,
                "lost": sum(r["delta"] for r in revs), "worst": worst, "turns": s.turn,
                "rounds": self.round()}

    # ================================================================ coach
    def hint(self, seat=None):
        s = self.s
        h = self.human if seat is None else seat
        po = self.pending_for(h)
        if s.phase == OVER or (s.actor() != h and not po):
            raise IllegalAction("wait for your turn")
        t0 = time.time()
        if po:
            ok, gain = self.coach.evaluate_offer(s, h, po["from"], tuple(po["give"]), tuple(po["get"]))
            return {"accept": bool(ok), "gain": gain, "secs": time.time() - t0}
        ranked = self.coach.analyze(s, top=6)
        if not ranked:
            return {"options": [], "secs": time.time() - t0}
        best = ranked[0][0]
        opts, seen = [], set()
        for sc, a, why in ranked:
            if s.phase in (MAIN, SPECIAL):
                steps, text, more = self.playable_steps(a, why, h)
            else:
                steps, text, more = [list(a)], f"{describe(s, a)} — {why}", False
            if text in seen:  # e.g. a bank trade then a city, and the same as one macro
                continue
            seen.add(text)
            opts.append({"loss": best - sc, "action": a, "steps": steps, "more": more, "text": text})
        return {"options": opts[:5], "secs": time.time() - t0}

    def playable_steps(self, a, plan, h):
        """The first part of a main-phase plan that one Play click can carry out: a single
        move, or bank trades together with the build they pay for (a macro is expanded to
        its trades and the spot the engine would build on). Returns (steps, plan text with
        the macro spelled out, whether the plan goes on after those steps)."""
        s = self.s
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
        # keep only the prefix that is legal outside the simulation
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

    def evaluate(self, seat=None):
        s = self.s
        h = self.human if seat is None else seat
        vals = self.coach.ev.values(self.coach.view(s, h), h)
        rows = []
        for p in range(s.n):
            prod = self.coach.ev.production(s, p)
            rows.append({"p": p, "value": vals[p], "pips": [round(x, 1) for x in prod]})
        return {"rows": rows}


class WebGame(Table):
    live = True

    def __init__(self, opponents=3, level="hard", seed=None, seat=None, name="You", review=True,
                 styles="mixed", dice="random", layout="random", humans=None, mode="solo",
                 persist=True, record=None, saved=None, gid=None):
        if record is None:
            seed = seed if seed is not None else random.randrange(1, 10 ** 6)
            if level not in LEVELS or (styles != "mixed" and styles not in STYLES):
                raise IllegalAction("unknown level or style")
            if mode not in MODES or dice not in DICE_MODES or layout not in LAYOUTS:
                raise IllegalAction("unknown game option")
            if opponents + len(humans or [1]) >= 5:
                layout = "random"  # the beginner map is a four-player board
            seats = seat_plan(opponents, level, seed, seat, name, humans, styles)
            record = Record(seed, seats, {"dice": dice, "layout": layout}, {"mode": mode})
        self._table(record, gid or store.new_id())
        s = self.s
        self.persist = persist
        self.agents = [self._agent(p) for p in range(s.n)]
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.review_on = {h: bool(review) for h in self.humans}
        self.tokens = {}           # secret -> seat
        self.touched = time.time()
        self._saved_at = 0.0
        self._jobs = queue.Queue()
        self._reviewer = AIPlayer(s.board, "hard", seed=self.seed)
        self._alive = True
        if saved is not None or record.entries:
            self._restore(saved or {})
        else:
            for h in self.humans:
                self.tokens[secrets.token_urlsafe(12)] = h
            if self.mode == "online":
                self.open = set(self.humans[1:])  # the host holds the first human seat
        threading.Thread(target=self._review_worker, daemon=True).start()

    def _agent(self, p):
        x = self.rec.seats[p]
        if x["kind"] != "bot":
            return None
        return AIPlayer(self.s.board, x.get("level", "hard"), seed=self.seed * 7 + p,
                        style=x.get("style", "balanced"))

    # ============================================================ persistence
    @classmethod
    def resume(cls, gid, upto=None, persist=True):
        """A saved game, live again. With `upto`, a new game that plays on from that
        point of the record (the original file is left as it is)."""
        rec, saved = store.load(gid)
        if upto is not None:
            rec = rec.copy(upto)
            rec.meta.pop("result", None)
            rec.meta.pop("coach", None)
            rec.meta["branched_from"] = gid
            rec.meta["created"] = time.time()
            return cls(record=rec, saved={}, persist=persist)
        return cls(record=rec, saved=saved, gid=gid, persist=persist)

    def _restore(self, saved):
        """Play the record back onto the fresh state, rebuilding the undo points."""
        s = self.s
        for i, e in enumerate(self.rec.entries):
            if e[0] == "m":
                offer = e[3] if len(e) > 3 else None
                self.history.append({"state": s.clone(sim=False), "at": i, "seat": e[1], "label": e[2],
                                     "offer": _offer_in(offer), "reasons": dict(self.reasons), "agents": None})
                continue
            got = Record.play(s, e)
            if got:
                self.reasons[got[0]] = got[1]
        self.offer = _offer_in(saved.get("offer"))
        self.tokens = {t: int(q) for t, q in (saved.get("tokens") or {}).items() if int(q) in self.humans}
        for h in self.humans:  # a seat always has at least one token
            if h not in self.tokens.values():
                self.tokens[secrets.token_urlsafe(12)] = h
        self.open = {int(q) for q in saved.get("open") or [] if int(q) in self.humans}

    def save(self, force=True):
        if not self.persist or (not force and time.time() - self._saved_at < 3):
            return
        s = self.s
        meta = self.rec.meta
        meta["round"] = self.round()
        if s.phase == OVER:
            meta["result"] = {"winner": s.winner, "vp": [s.vp(p) for p in range(s.n)],
                              "rounds": self.round(), "human": self.human}
            meta["coach"] = store.review_summary(self.reviews, self.human)
        else:
            meta.pop("result", None)
            meta.pop("coach", None)
        try:
            store.save(self.gid, self.rec, offer=_offer_out(self.offer), tokens=self.tokens,
                       open=sorted(self.open))
            self._saved_at = time.time()
        except OSError as ex:  # a full disk must not stop the game
            print("could not save the game:", ex)

    def close(self):
        """Stop the review worker (the game stays on disk)."""
        if self._alive:
            self.save()
        self._alive = False
        self._jobs.put(None)

    def bump(self):
        """Something changed: wake everyone waiting for news of this game."""
        self.version += 1
        self.touched = time.time()
        with self.changed:
            self.changed.notify_all()

    def wait(self, version, timeout=25.0):
        """Block until the game has moved past `version` (long polling)."""
        with self.changed:
            if self.version == version and self._alive:
                self.changed.wait(timeout)

    def reviewing(self):
        return self._jobs.unfinished_tasks

    # ================================================================ seats
    def seat_of(self, token):
        return self.tokens.get(token) if token else None

    def tokens_of(self, seats):
        return {str(q): t for t, q in self.tokens.items() if q in seats}

    def join(self, name):
        """Take an open seat in an online game; returns (seat, token)."""
        if not self.open:
            raise IllegalAction("this game is full")
        seat = min(self.open)
        name = clean_name(name, f"Player {seat + 1}")
        if name in self.s.names:
            name = clean_name(f"{name[:12]} {seat + 1}", name)
        self.open.discard(seat)
        self.rec.seats[seat]["name"] = name
        self.s.names[seat] = name
        token = next(t for t, q in self.tokens.items() if q == seat)
        self.bump()
        self.save()
        return seat, token

    def fill_with_bots(self, level=None):
        """Start an online game now: every seat still open is played by a bot."""
        free = [b for b in AI_NAMES if b not in self.s.names]
        for seat in sorted(self.open):
            self.rec.seats[seat] = {"name": free.pop(0), "kind": "bot", "level": level or self.level,
                                    "style": random.Random(self.seed * 13 + seat).choice(sorted(STYLES))}
            self.s.names[seat] = self.rec.seats[seat]["name"]
            self.agents[seat] = self._agent(seat)
            self.tokens = {t: q for t, q in self.tokens.items() if q != seat}
        self.open = set()
        self.humans = self.rec.humans
        self.review_on = {h: self.review_on.get(h, True) for h in self.humans}
        self.bump()
        self.save()

    # ================================================================ bots
    def _apply(self, a, why=None):
        self.rec.act(self.s, a, why)

    def step(self, version=None):
        """Advance one bot decision, if it is a bot's turn. A caller that passes the
        version it has seen only steps from that version: when several browsers watch
        the same game, their step requests don't pile up."""
        s = self.s
        if version is not None and version != self.version:
            return
        if s.phase == OVER or self.open or (self.offer and self.offer["bot"]):
            return
        p = s.actor()
        if self.agents[p] is None:
            return
        self.offer = None
        a, why = self.agents[p].decide(s)
        if a[0] == "propose":
            self._bot_propose(p, a[1], a[2], why)
        else:
            n0 = len(s.events)
            why = public_reason(why) if a[0] not in ("roll", "end") else None
            self._apply(a, why)
            if why:
                for i in range(n0, len(s.events)):
                    if s.events[i][2] not in ("private", "private_steal"):
                        self.reasons[i] = why
                        break
        self.bump()
        self.save(force=s.phase == OVER)

    def _note(self, kind, p, msg):
        self.rec.note(self.s, kind, p, msg)

    def _bot_propose(self, p, give, get, why):
        s = self.s
        self._note("offer", p, f"{s.names[p]} offers {fmt_cards(give)} for {fmt_cards(get)}")
        self.reasons[len(s.events) - 1] = public_reason(why)
        answers = {}
        for q in range(s.n):
            if q == p:
                continue
            if self.agents[q] is not None:
                answers[q] = bool(s.can_afford(q, get) and self.agents[q].evaluate_offer(s, q, p, give, get)[0])
            elif s.can_afford(q, get):
                answers[q] = None  # a human answers for themselves; one who can't pay isn't asked
        self.offer = {"from": p, "give": tuple(give), "get": tuple(get), "answers": answers,
                      "counters": {}, "tries": {}, "notes": {}, "bot": True}
        if None not in answers.values():
            self._finish_offer()

    def _finish_offer(self):
        """Everyone has answered a bot's offer: trade with a taker, or with the best counter."""
        s, o = self.s, self.offer
        p, give, get = o["from"], o["give"], o["get"]
        self.offer = None
        for q in sorted(o["answers"]):
            self._note("reply", q, f"{s.names[q]} {'accepts' if o['answers'][q] else 'declines'}")
        accepters = [q for q in sorted(o["answers"]) if o["answers"][q]]
        if accepters:
            self._apply(("trade", pick_partner(s, accepters), give, get))
            return
        best = None
        declined = [q for q in sorted(o["answers"]) if self.agents[q] is not None and s.can_afford(q, get)]
        for q, g2, t2, _ in bot_counters(s, self.agents, p, give, get, declined):
            ok, gain = self.agents[p].accepts_counter(s, p, g2, t2)
            self._note("offer", q, f"{s.names[q]} counters: {fmt_cards(t2)} for {fmt_cards(g2)}")
            self._note("reply", p, f"{s.names[p]} {'accepts' if ok else 'declines'}")
            if ok and (best is None or gain > best[0]):
                best = (gain, q, g2, t2)
            if ok:
                break
        if best:
            self._apply(("trade", best[1], best[2], best[3]))

    def respond(self, accept, seat=None, counter=None):
        """A human's answer to the offer on the table: yes, no, or a counter-offer
        ({"give": what the proposer gives, "get": what it gets}, like the offer itself)."""
        h = self.human if seat is None else seat
        o = self.offer
        if not o or o["answers"].get(h, 0) is not None:
            raise IllegalAction("no offer to answer")
        s = self.s
        p = o["from"]
        if counter is not None:
            give2, get2 = self._check_counter(o, h, counter)
            label = f"counter {s.names[p]}'s offer: {fmt_cards(get2)} for {fmt_cards(give2)}"
        else:
            label = f"{'accept' if accept else 'decline'} {s.names[p]}'s offer"
        self.snapshot(label, h)
        if counter is not None and not o["bot"]:
            o["answers"][h] = False
            o["counters"][h] = {"give": list(give2), "get": list(get2)}
            o["notes"][h] = "counter-offer"
        elif counter is not None:
            ok = s.can_afford(p, give2) and self.agents[p].accepts_counter(s, p, give2, get2)[0]
            self._note("offer", h, f"{s.names[h]} counters: {fmt_cards(get2)} for {fmt_cards(give2)}")
            self._note("reply", p, f"{s.names[p]} {'accepts' if ok else 'declines'}")
            if ok:
                if self.review_on.get(h):
                    self._queue("offer", (p, give2, get2, True), h)
                self.offer = None
                self._apply(("trade", h, give2, get2))
            else:
                o["tries"][h] = o["tries"].get(h, 0) + 1
                o["notes"][h] = f"{s.names[p]} turned your counter-offer down"
        else:
            if self.review_on.get(h):
                self._queue("offer", (p, o["give"], o["get"], bool(accept)), h)
            o["answers"][h] = bool(accept)
            if o["bot"] and None not in o["answers"].values():
                self._finish_offer()
        self.bump()
        self.save()

    def _check_counter(self, o, h, counter):
        s = self.s
        if not isinstance(counter, dict):
            raise IllegalAction("malformed counter-offer")
        give2, get2 = cards(counter.get("give")), cards(counter.get("get"))
        if not any(give2) or not any(get2) or any(a and b for a, b in zip(give2, get2)):
            raise IllegalAction("a counter-offer needs different cards on each side")
        if (give2, get2) == (tuple(o["give"]), tuple(o["get"])):
            raise IllegalAction("that is the offer you were made")
        if not s.can_afford(h, get2):
            raise IllegalAction("you don't have those cards")
        if o["bot"] and o["tries"].get(h, 0) >= COUNTER_TRIES:
            raise IllegalAction("no more counter-offers: accept or decline")
        return give2, get2

    # ================================================================ human
    def act(self, raw, seat=None):
        s = self.s
        h = self.human if seat is None else seat
        if s.phase == OVER:
            raise IllegalAction("the game is over")
        if self.open:
            raise IllegalAction("waiting for players to join")
        if s.actor() != h or (self.offer and self.offer["bot"]):
            raise IllegalAction("not your decision right now")
        a = to_action(raw)
        if a[0] == "trade":
            o = self.offer
            terms = {"give": list(a[2]), "get": list(a[3])}
            agreed = o and not o["bot"] and o["from"] == h and (
                (o["answers"].get(a[1]) is True and terms == {"give": list(o["give"]), "get": list(o["get"])})
                or o["counters"].get(a[1]) == terms)
            if not agreed:
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
        at = self.snapshot(label, h)
        if self.review_on.get(h):
            if forced:
                self.reviews.append({"token": at, "at": at, "seat": h, "turn": s.turn, "phase": s.phase,
                                     "label": label, "best": "only sensible move", "delta": 0.0,
                                     "verdict": "forced", "forced": True,
                                     "action": a, "best_action": a})
            else:
                self._queue("discard" if a[0] == "discard" else "action", a, h)
        self.offer = None
        self._apply(a)
        self.bump()
        self.save()

    def make_offer(self, give, get, seat=None):
        s = self.s
        h = self.human if seat is None else seat
        if s.phase != MAIN or s.actor() != h or self.open:
            raise IllegalAction("you can only offer trades after rolling, on your turn")
        give, get = cards(give), cards(get)
        if not any(give) or not any(get):
            raise IllegalAction("pick cards on both sides")
        if any(give[r] and get[r] for r in range(5)):
            raise IllegalAction("don't give and ask for the same resource")
        if any(give[r] > s.hands[h][r] for r in range(5)):
            raise IllegalAction("you don't have those cards")
        o = {"from": h, "give": give, "get": get, "answers": {}, "counters": {}, "tries": {},
             "notes": {}, "bot": False}
        for q in range(s.n):
            if q == h:
                continue
            if self.agents[q] is None:
                # another human answers for themselves (one without the cards simply can't)
                o["answers"][q] = None if s.can_afford(q, get) else False
                if o["answers"][q] is False:
                    o["notes"][q] = "not interested"
                continue
            ok, gain = self.agents[q].evaluate_offer(s, q, h, give, get)
            o["answers"][q] = bool(ok)
            why = self.reply_reason(q, h, get, ok, gain)
            if not ok:
                c = self.agents[q].counter_offer(s, q, h, give, get)
                if c:
                    o["counters"][q] = {"give": list(c[0]), "get": list(c[1])}
                    why = "would do it for more"
            if why:
                o["notes"][q] = why
        self.offer = o
        self.bump()
        return self.my_offer_for(h)

    offer_trade = make_offer

    def reply_reason(self, q, h, get, ok, gain):
        """One line on why bot q answered the human's offer that way (gain is the
        trade's value to q, from evaluate_offer)."""
        s = self.s
        if not s.can_afford(q, get):
            return "not interested"  # saying why would reveal their hidden hand
        if ok:
            return None
        if gain > 0.05 and s.public_vp(h) >= 8:
            return "won't trade with the leader"
        if gain < -0.05:
            return "helps you more than them"
        return "not worth it to them"

    def trivial(self, a):
        s = self.s
        if s.phase in (PRE_ROLL, SETUP_ROAD, ROAD_BUILD):
            return len(s.legal_actions()) <= 1
        if s.phase in (MAIN, SPECIAL) and a[0] == "end":
            return all(x[0] == "end" for x in s.legal_actions(include_trades=True))
        return False

    # ================================================================ undo
    def snapshot(self, label="", seat=None):
        """Mark a human decision: undo comes back to exactly here. Returns its id."""
        at = len(self.rec.entries)
        agents = [(a.rng.getstate(), a._proposed) if a else None for a in self.agents]
        self.history.append({"state": self.s.clone(sim=False), "at": at, "seat": seat, "label": label,
                             "offer": _offer_copy(self.offer), "reasons": dict(self.reasons), "agents": agents})
        self.rec.mark(seat, label, _offer_out(self.offer))
        return at

    def undo(self, seat=None):
        """Step back one human decision; returns what that decision was."""
        if len(self.humans) != 1:
            raise IllegalAction("undo is for games with one human player")
        if not self.history:
            raise IllegalAction("nothing to undo")
        h = self.history.pop()
        self.s = h["state"]
        if h["agents"]:
            for a, saved in zip(self.agents, h["agents"]):
                if a:
                    a.rng.setstate(saved[0])
                    a._proposed = saved[1]
        # the undone decision and its review go; reviews of earlier moves stay (even pending ones)
        self.rec.truncate(h["at"])
        self.offer = h["offer"]
        self.reasons = h["reasons"]
        self.bump()
        self.save()
        return self.you(h["label"])

    # ================================================================ reviews
    def _live(self, at):
        return any(h["at"] == at for h in self.history)

    def _queue(self, kind, payload, seat):
        at = self.history[-1]["at"]
        self._jobs.put((at, kind, self.s.clone(sim=False), payload, seat))

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
                            self.bump()
                            if self.s.phase == OVER:
                                self.save()
            except Exception as ex:  # a review must never break the game
                print("review failed:", ex)
            finally:
                self._jobs.task_done()

    def _review(self, at, kind, s, payload, h):
        coach = self._reviewer
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
            if s.phase in (MAIN, SPECIAL) and why.startswith("plan:"):
                best_desc = why[6:]
            elif why and s.phase in (SETUP_SETTLE, SETUP_ROAD, BANDIT):
                best_desc += f" — {why}"
            label = describe(s, a)
            if a[0] == "trade":
                label = f"trade {fmt_cards(a[2])} for {fmt_cards(a[3])} with {s.names[a[1]]}"
        delta = max(0.0, best - mine)
        return {"token": at, "at": at, "seat": h, "action": mine_a, "best_action": best_a, "turn": s.turn,
                "phase": s.phase, "kind": "offer" if kind == "offer" else s.phase,
                "label": label, "best": best_desc, "delta": round(delta, 3), "verdict": verdict(delta),
                "forced": False}


def _offer_copy(o):
    if not o:
        return None
    return {**o, "answers": dict(o["answers"]), "counters": dict(o["counters"]),
            "tries": dict(o["tries"]), "notes": dict(o["notes"])}


def _offer_out(o):
    """An offer as JSON (seat keys become strings)."""
    if not o:
        return None
    js = lambda d: {str(k): v for k, v in d.items()}  # noqa: E731
    return {"from": o["from"], "give": list(o["give"]), "get": list(o["get"]), "bot": o["bot"],
            "answers": js(o["answers"]), "counters": js(o["counters"]), "tries": js(o["tries"]),
            "notes": js(o["notes"])}


def _offer_in(d):
    if not d:
        return None
    back = lambda x: {int(k): v for k, v in (x or {}).items()}  # noqa: E731
    return {"from": d["from"], "give": tuple(d["give"]), "get": tuple(d["get"]), "bot": d["bot"],
            "answers": back(d.get("answers")), "counters": back(d.get("counters")),
            "tries": back(d.get("tries")), "notes": back(d.get("notes"))}


class Replay(Table):
    """A record stepped through move by move. Position k is the game after its first k
    actions; log lines written between two actions belong to the earlier position."""

    EVERY = 40  # a checkpoint every this many entries, so stepping back is quick

    def __init__(self, gid, rec=None):
        if rec is None:
            rec, _ = store.load(gid)
        self._table(rec, gid)
        self.lock = threading.RLock()
        self.touched = time.time()
        self.cuts = []      # entry index just past the k-th action (and the notes after it)
        entries = rec.entries
        i = 0
        while i <= len(entries):
            j = i
            while j < len(entries) and entries[j][0] != "a":
                j += 1
            self.cuts.append(j)   # everything before the next action
            i = j + 1
        if self.cuts and len(self.cuts) > 1:
            self.cuts[-1] = len(entries)
        self.total = len(self.cuts) - 1
        self.marks = []     # human decisions: {"k", "seat", "label", "review"}
        by_at = {r.get("at"): r for r in rec.reviews}
        k = 0
        for i, e in enumerate(entries):
            if e[0] == "a":
                k += 1
            elif e[0] == "m":
                self.marks.append({"k": k, "at": i, "seat": e[1], "label": e[2], "review": by_at.get(i)})
        self.turns = []
        self._check = {}    # entry index -> (state, reasons)
        self._at = 0
        self._reasons_at = {}
        self.s = rec.new_state()
        self.k = 0
        self._save_check()
        self.seek(0)

    def _save_check(self):
        self._check[self._at] = (self.s.clone(sim=False), dict(self.reasons))

    def seek(self, k):
        k = max(0, min(self.total, int(k)))
        target = self.cuts[k]
        if target < self._at:
            base = max(i for i in self._check if i <= target)
            state, reasons = self._check[base]
            self.s, self.reasons, self._at = state.clone(sim=False), dict(reasons), base
        while self._at < target:
            e = self.rec.entries[self._at]
            got = Record.play(self.s, e)
            if got:
                self.reasons[got[0]] = got[1]
            self._at += 1
            if self._at % self.EVERY == 0 and self._at not in self._check:
                self._save_check()
        self.k = k
        self.touched = time.time()
        return k

    def view(self, seat=None, since=0, reveal=False):
        out = Table.view(self, seat, since, reveal)
        h = out["human"]
        here = [m for m in self.marks if m["k"] == self.k and m["seat"] == h]
        out["replay"] = {
            "k": self.k, "total": self.total, "reveal": bool(reveal),
            "decision": here[-1] if here else None,
            "marks": [{"k": m["k"], "verdict": (m["review"] or {}).get("verdict"),
                       "delta": (m["review"] or {}).get("delta", 0)} for m in self.marks if m["seat"] == h],
            "finished": bool(self.rec.meta.get("result")), "seats": self.humans,
            "entry": self.cuts[self.k],
        }
        return out

    def hint_here(self, seat=None):
        s = self.s
        h = self.human if seat is None else seat
        if s.phase == OVER or s.actor() != h:
            raise IllegalAction("it isn't this player's decision here")
        return Table.hint(self, h)

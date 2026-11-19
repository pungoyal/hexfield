"""A game as data.

A `Record` is the seed, the table (who sits where, which options) and every decision
in order. Replaying it on a fresh `GameState` gives back the exact game: the board,
the dice, the deck and the steals all come from the seed, so nothing else needs to be
stored. Saved games, resuming after a restart, the replay viewer and "play on from
here" are all this one list.

Entries are small JSON lists:

    ["a", action, why]         an action applied to the state (why: a bot's reasoning)
    ["l", kind, player, msg]   a log line written outside the engine (offers, replies)
    ["m", seat, label, extra]  a decision point of a human player: where undo returns to

Nothing here depends on the bots, so a record stays valid when they change.
"""

import json
import time

from .game import DICE_MODES, LAYOUTS, GameState

VERSION = 2  # 2: the renamed actions (bandit, ranger, hoard, windfall)


def to_action(raw):
    """JSON list -> engine tuple (card lists become tuples again)."""
    return tuple(tuple(x) if isinstance(x, list) else x for x in raw)


def from_action(a):
    return [list(x) if isinstance(x, tuple) else x for x in a]


class Record:
    def __init__(self, seed, seats, options=None, meta=None):
        """seats: one dict per player, in turn order:
        {"name": str, "kind": "human" | "bot", "level": str, "style": str}"""
        self.seed = int(seed)
        self.seats = [dict(x) for x in seats]
        self.options = {"dice": "random", "layout": "random", **(options or {})}
        if self.options["dice"] not in DICE_MODES or self.options["layout"] not in LAYOUTS:
            raise ValueError("unknown game option")
        self.entries = []
        self.reviews = []      # coach verdicts: dicts with "at" (entry index of the decision) and "seat"
        self.meta = {"created": time.time(), **(meta or {})}

    # ------------------------------------------------------------ building
    @property
    def names(self):
        return [x["name"] for x in self.seats]

    @property
    def humans(self):
        return [i for i, x in enumerate(self.seats) if x["kind"] == "human"]

    def new_state(self):
        return GameState(len(self.seats), seed=self.seed, names=self.names,
                         dice=self.options["dice"], layout=self.options["layout"])

    def act(self, s, action, why=None):
        """Apply `action` to `s` and write it down. Raises (and records nothing) if illegal."""
        s.apply(action)
        self.entries.append(["a", from_action(action), why] if why else ["a", from_action(action)])

    def note(self, s, kind, player, msg):
        s.log(msg, kind, player)
        self.entries.append(["l", kind, player, msg])

    def mark(self, seat, label, extra=None):
        self.entries.append(["m", seat, label, extra] if extra is not None else ["m", seat, label])

    def truncate(self, n):
        del self.entries[n:]
        self.reviews = [r for r in self.reviews if r.get("at", 0) < n]

    # ------------------------------------------------------------- replay
    @staticmethod
    def play(s, entry):
        """Apply one entry to `s`. Returns (event index, why) when a bot's reasoning
        belongs to one of the log lines it produced, else None."""
        kind = entry[0]
        if kind == "l":
            s.log(entry[3], entry[1], entry[2])
            return None
        if kind != "a":
            return None
        n0 = len(s.events)
        s.apply(to_action(entry[1]))
        why = entry[2] if len(entry) > 2 else None
        if why:
            for i in range(n0, len(s.events)):
                if s.events[i][2] not in ("private", "private_steal"):
                    return i, why
        return None

    def replay(self, upto=None):
        """The state after the first `upto` entries (all of them by default)."""
        s = self.new_state()
        for e in self.entries[:upto]:
            self.play(s, e)
        return s

    def steps(self):
        """Indices of the entries that change the board (what a replay steps through)."""
        return [i for i, e in enumerate(self.entries) if e[0] == "a"]

    # --------------------------------------------------------------- json
    def to_dict(self, **extra):
        return {"version": VERSION, "seed": self.seed, "seats": self.seats, "options": self.options,
                "meta": self.meta, "entries": self.entries, "reviews": self.reviews, **extra}

    def dumps(self, **extra):
        return json.dumps(self.to_dict(**extra), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d):
        if not isinstance(d, dict) or "seed" not in d or "seats" not in d:
            raise ValueError("not a Hexfield game file")
        v = d.get("version")
        if v != VERSION:  # say which way it is off, so the player knows what would open it
            older = isinstance(v, int) and v < VERSION
            raise ValueError(f"this game was saved by {'an older' if older else 'a newer'} version of Hexfield "
                             f"(save format {v}; this version reads format {VERSION}), so it can't be opened here")
        seats = d["seats"]
        if not isinstance(seats, list) or not 2 <= len(seats) <= 6:
            raise ValueError("bad seat list")
        rec = cls(d["seed"], seats, d.get("options"), d.get("meta"))
        rec.entries = list(d.get("entries", []))
        rec.reviews = list(d.get("reviews", []))
        return rec

    @classmethod
    def loads(cls, text):
        return cls.from_dict(json.loads(text))

    def copy(self, upto=None):
        """An independent copy, cut after `upto` entries (for "play on from here")."""
        rec = Record(self.seed, self.seats, self.options, dict(self.meta))
        n = len(self.entries) if upto is None else upto
        rec.entries = [list(e) for e in self.entries[:n]]
        rec.reviews = [dict(r) for r in self.reviews if r.get("at", 0) < n]
        return rec

"""Saved games and coach statistics on disk.

Everything lives in one folder: $HEXFIELD_HOME, or ~/.hexfield. Each game is one JSON
file (see record.py), written atomically after every decision, so a crash or a
restart loses nothing. Statistics are worked out from the finished games; there is no
second database to keep in step.

On a server with accounts (see web/accounts.py) each account's games are in a folder of
its own, games/users/<account id>; `owner` names it. The default, "", is games/ itself:
the games of whoever runs Hexfield.
"""

import json
import os
import re
import secrets
import shutil
import tempfile
import time

from .record import VERSION, Record

KEEP = 200  # saved games kept; the oldest finished ones go first
ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
OWNER_RE = re.compile(r"^[0-9a-f]{32}$")
PHASE_GROUPS = {
    "setup_village": "opening", "setup_trail": "opening",
    "main": "building and trading", "special_build": "building and trading",
    "free_trails": "building and trading", "pre_roll": "rangers",
    "move_bandit": "bandit", "discard": "discards", "offer": "trade offers",
}


def home():
    return os.environ.get("HEXFIELD_HOME") or os.path.join(os.path.expanduser("~"), ".hexfield")


def games_dir(owner=""):
    d = os.path.join(home(), "games")
    if owner:
        if not OWNER_RE.match(owner):
            raise ValueError("bad owner")
        d = os.path.join(d, "users", owner)
    os.makedirs(d, mode=0o700, exist_ok=True)  # the files hold seat keys: yours alone
    return d


def new_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def _path(gid, owner=""):
    if not ID_RE.match(gid or ""):
        raise ValueError("bad game id")
    return os.path.join(games_dir(owner), gid + ".json")


def save(gid, record, owner="", **live):
    """Write the game file (atomically: a reader never sees half a file)."""
    record.meta["updated"] = time.time()
    path = _path(gid, owner)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(record.dumps(live=live))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(gid, owner=""):
    """-> (Record, live dict)."""
    with open(_path(gid, owner)) as f:
        d = json.load(f)
    return Record.from_dict(d), d.get("live") or {}


def delete(gid, owner=""):
    try:
        os.unlink(_path(gid, owner))
        return True
    except OSError:
        return False


def delete_owner(owner):
    """Remove every saved game of an account."""
    if OWNER_RE.match(owner or ""):
        shutil.rmtree(os.path.join(home(), "games", "users", owner), ignore_errors=True)


def _summary(gid, d):
    meta = d.get("meta", {})
    seats = d.get("seats", [])
    return {
        "id": gid, "seed": d.get("seed"), "created": meta.get("created"), "updated": meta.get("updated"),
        "mode": meta.get("mode", "solo"), "finished": bool(meta.get("result")),
        "result": meta.get("result"), "coach": meta.get("coach"),
        "players": [{"name": x.get("name"), "kind": x.get("kind"), "level": x.get("level"),
                     "style": x.get("style")} for x in seats],
        "options": d.get("options", {}), "moves": sum(1 for e in d.get("entries", []) if e and e[0] == "a"),
        "round": meta.get("round", 0),
    }


def list_games(limit=60, owner=""):
    """Newest first. Unreadable files are skipped, never fatal."""
    d = games_dir(owner)
    files = []
    for name in os.listdir(d):
        if name.endswith(".json") and ID_RE.match(name[:-5]):
            try:
                files.append((os.path.getmtime(os.path.join(d, name)), name[:-5]))
            except OSError:
                pass
    files.sort(reverse=True)
    out = []
    for _, gid in files[:limit]:
        try:
            with open(os.path.join(d, gid + ".json")) as f:
                data = json.load(f)
            if data.get("version") == VERSION:
                out.append(_summary(gid, data))
        except (OSError, ValueError):
            continue
    return out


def prune(keep=KEEP, owner=""):
    """Drop the oldest games beyond `keep` (finished ones before unfinished)."""
    games = list_games(limit=10 ** 6, owner=owner)
    extra = len(games) - keep
    if extra <= 0:
        return 0
    order = sorted(games, key=lambda g: (not g["finished"], g["updated"] or 0))
    for g in order[:extra]:
        delete(g["id"], owner)
    return extra


# ------------------------------------------------------------------ statistics
def review_summary(reviews, seat):
    """Accuracy of one player's reviewed decisions, overall and by kind of decision."""
    revs = [r for r in reviews if r.get("seat") == seat and not r.get("forced")]
    if not revs:
        return None
    groups = {}
    for r in revs:
        g = groups.setdefault(PHASE_GROUPS.get(r.get("kind") or r.get("phase"), "other"), [0, 0.0, 0])
        g[0] += 1
        g[1] += r["delta"]
        g[2] += r["delta"] < 0.2
    return {
        "decisions": len(revs),
        "accuracy": 100.0 * sum(1 for r in revs if r["delta"] < 0.2) / len(revs),
        "lost": sum(r["delta"] for r in revs),
        "groups": {k: {"n": n, "lost": round(lost, 3), "good": good} for k, (n, lost, good) in groups.items()},
    }


def stats(limit=200, owner=""):
    """Coach statistics across finished solo games, oldest first: one row per game,
    plus totals by kind of decision (where the points go)."""
    rows, groups = [], {}
    won = 0
    for g in reversed(list_games(limit, owner)):
        coach, res = g.get("coach"), g.get("result")
        if not g["finished"] or g["mode"] != "solo" or not res:
            continue
        me = res.get("human")
        row = {"id": g["id"], "when": g["updated"], "won": res.get("winner") == me,
               "fame": (res.get("fame") or [None])[me] if me is not None else None,
               "rounds": res.get("rounds"), "level": next((p["level"] for p in g["players"] if p["kind"] == "bot"), None),
               "players": len(g["players"]),
               "accuracy": coach and coach["accuracy"], "lost": coach and coach["lost"],
               "decisions": coach and coach["decisions"]}
        rows.append(row)
        won += row["won"]
        for k, v in ((coach or {}).get("groups") or {}).items():
            t = groups.setdefault(k, {"n": 0, "lost": 0.0, "good": 0})
            t["n"] += v["n"]
            t["lost"] += v["lost"]
            t["good"] += v["good"]
    for t in groups.values():
        t["per"] = t["lost"] / t["n"] if t["n"] else 0.0
        t["accuracy"] = 100.0 * t["good"] / t["n"] if t["n"] else None
    return {"games": rows, "won": won, "groups": groups}

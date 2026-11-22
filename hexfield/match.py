"""Glue for running turns: resolving trade proposals and headless games."""

import random
import time

from .ai import STYLES, AIPlayer
from .game import OVER, GameState, IllegalAction

AI_NAMES = ["Vera", "Magnus", "Kasimir", "Ingrid", "Tobias", "Selene"]


def clean_name(name, fallback):
    return (str(name or "").strip() or fallback)[:14]


def seat_plan(opponents, level, seed, seat, name, humans, styles):
    """Who sits where: a list of seat dicts in turn order. One human keeps the classic
    seating (so an old seed deals the same table); more humans are spread at random."""
    names = [clean_name(x, f"Player {i + 1}") for i, x in enumerate(humans or [name])]
    n = opponents + len(names)
    if not 3 <= n <= 6:
        raise IllegalAction("a game needs 3 to 6 players")
    rng = random.Random(seed)
    if len(names) == 1:
        mine = [(seat - 1) if seat else rng.randrange(n)]
        bots = AI_NAMES[:]
        rng.shuffle(bots)
    else:
        mine = sorted(rng.sample(range(n), len(names)))
        bots = AI_NAMES[:]
        rng.shuffle(bots)
    bots = [b for b in bots if b not in names]
    pick = random.Random(seed * 13 + 5)
    seats = []
    for p in range(n):
        if p in mine:
            seats.append({"name": names[mine.index(p)], "kind": "human"})
        else:
            style = pick.choice(sorted(STYLES)) if styles == "mixed" else styles
            seats.append({"name": bots.pop(0), "kind": "bot", "level": level, "style": style})
    return seats


def public_reason(why):
    """A bot's plan names later steps (e.g. '-> play Ranger') that would reveal its hidden
    cards; keep only the move it actually made."""
    if not why or not why.startswith("plan:"):
        return why
    first = why[5:].split("  (eval")[0].split(" -> ")[0].strip()
    ev = why.rsplit("(eval", 1)
    tail = f"  (eval{ev[1]}" if len(ev) == 2 else ""
    if "+99" in tail or len(tail) > 18:  # a winning move scores huge; the number means nothing
        tail = ""
    return f"plan: {first}" + tail


def pick_partner(s, accepters):
    """A proposer with several takers trades with the one furthest behind."""
    return min(accepters, key=lambda q: (s.public_fame(q), s.hand_size(q)))


def bot_counters(s, agents, proposer, give, get, declined):
    """Counter-offers from the bots that turned the deal down: [(q, give, get, gain)],
    each still from the proposer's side."""
    out = []
    for q in declined:
        if agents[q] is None:
            continue
        c = agents[q].counter_offer(s, q, proposer, give, get)
        if c:
            out.append((q, c[0], c[1], c[2]))
    return out


def resolve_proposal(s, agents, proposer, give, get, ask_human=None, on_reply=None, apply=None, on_counter=None):
    """Offer `give` for `get` to every other player. Returns the partner or None.

    agents[q] is an AIPlayer or None for a human; ask_human(q, proposer, give, get)
    -> bool answers for humans. on_reply(q, accepted, gain) lets the UI narrate the
    answers. If nobody accepts a bot's offer, the bots that declined may counter and
    the proposer takes the counter it likes best: on_counter(q, give, get, accepted).
    `apply` plays the agreed trade (default: straight onto `s`).
    """
    apply = apply or s.apply
    accepters, declined = [], []
    for q in range(s.n):
        if q == proposer:
            continue
        if not s.can_afford(q, get):
            continue
        if agents[q] is None:
            ok = ask_human(q, proposer, give, get) if ask_human else False
            if ok == "traded":  # the human made a counter-offer and the proposer took it
                return q
            ok, gain = bool(ok), None
        else:
            ok, gain = agents[q].evaluate_offer(s, q, proposer, give, get)
        if on_reply:
            on_reply(q, ok, gain)
        (accepters if ok else declined).append(q)
    if accepters:
        partner = pick_partner(s, accepters)
        apply(("trade", partner, give, get))
        return partner
    if agents[proposer] is None:
        return None
    best = None
    for q, g2, t2, _ in bot_counters(s, agents, proposer, give, get, declined):
        ok, gain = agents[proposer].accepts_counter(s, proposer, g2, t2)
        if on_counter:
            on_counter(q, g2, t2, ok)
        if ok and (best is None or gain > best[0]):
            best = (gain, q, g2, t2)
    if best is None:
        return None
    apply(("trade", best[1], best[2], best[3]))
    return best[1]


def play_headless(
    seed, levels, max_turns=500, verbose=False, styles=None, params=None, dice="random", layout="random", record=None
):
    """One bot-only game. levels/styles/params are per seat (params: evaluation
    overrides for tools/tune.py). With a `record` every action is written to it."""
    n = len(levels)
    s = (
        record.new_state()
        if record
        else GameState(n, seed=seed, names=[f"Bot{i}" for i in range(n)], dice=dice, layout=layout)
    )
    agents = [
        AIPlayer(
            s.board,
            lv,
            seed=(seed or 0) * 31 + i,
            style=(styles[i] if styles else "balanced"),
            params=(params[i] if params else None),
        )
        for i, lv in enumerate(levels)
    ]
    apply = (lambda a, why=None: record.act(s, a, why)) if record else (lambda a, why=None: s.apply(a))
    t0 = time.time()
    while s.phase != OVER and s.turn < max_turns:
        p = s.actor()
        a, why = agents[p].decide(s)
        if verbose:
            print(s.turn, s.names[p], a, why)
        if a[0] == "propose":
            resolve_proposal(s, agents, p, a[1], a[2], apply=apply)
            continue
        apply(a, why)
    return s, time.time() - t0

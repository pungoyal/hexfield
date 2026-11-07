"""Glue for running turns: resolving trade proposals and headless games."""

import time

from .game import OVER, GameState
from .ai import AIPlayer


def resolve_proposal(s, agents, proposer, give, get, ask_human=None, on_reply=None):
    """Offer `give` for `get` to every other player. Returns partner or None.

    agents[q] is an AIPlayer or None for the human; ask_human(q, proposer,
    give, get) -> bool answers for humans.  on_reply(q, accepted, gain) lets
    the UI narrate responses.
    """
    accepters = []
    for q in range(s.n):
        if q == proposer:
            continue
        if not all(s.hands[q][i] >= get[i] for i in range(5)):
            continue
        if agents[q] is None:
            ok = bool(ask_human and ask_human(q, proposer, give, get))
            gain = None
        else:
            ok, gain = agents[q].evaluate_offer(s, q, proposer, give, get)
        if on_reply:
            on_reply(q, ok, gain)
        if ok:
            accepters.append(q)
    if not accepters:
        return None
    # proposer prefers the partner who is furthest behind
    partner = min(accepters, key=lambda q: (s.public_vp(q), s.hand_size(q)))
    s.apply(("trade", partner, give, get))
    return partner


def play_headless(seed, levels, max_turns=500, verbose=False):
    s = GameState(len(levels), seed=seed, names=[f"Bot{i}" for i in range(len(levels))])
    agents = [AIPlayer(s.board, lv, seed=(seed or 0) * 31 + i) for i, lv in enumerate(levels)]
    t0 = time.time()
    while s.phase != OVER and s.turn < max_turns:
        p = s.actor()
        a, why = agents[p].decide(s)
        if verbose:
            print(s.turn, s.names[p], a, why)
        if a[0] == "propose":
            resolve_proposal(s, agents, p, a[1], a[2])
            continue
        s.apply(a)
    return s, time.time() - t0

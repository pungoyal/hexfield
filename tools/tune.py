"""Tune the bots' evaluation weights by self-play (SPSA).

    python3 tools/tune.py --iterations 60 --games 48 --out tuned.json
    python3 tools/tune.py --validate tuned.json --games 600

Each iteration nudges every weight up or down at random by a few percent, plays the
"plus" set against the "minus" set (two seats each, seats rotated), and moves the
weights a small step toward the side that won more. One iteration says almost
nothing; the signal builds up over many. This is the method chess engines use for
the same job (simultaneous perturbation stochastic approximation: Spall 1992), and
it suits a noisy objective like a win rate because every weight learns from every
game.

A tuned set is only worth adopting if it beats the current weights in a separate,
larger match: --validate plays it and prints the win share with a 95% interval.
Adopt when the whole interval is above 50%; then copy the values into PARAMS in
hexfield/ai.py.
"""

import argparse
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hexfield.ai import PARAMS  # noqa: E402
from selfplay import run_match, wilson  # noqa: E402

# trading thresholds are left alone: against copies of itself a bot "learns" to refuse every deal
FROZEN = ("trade_margin", "propose_gain")


def duel(a, b, games, seed, level, jobs):
    """Two seats of parameter set `a` against two of `b`: a's share of the decided games."""
    wins, _, _, _ = run_match([level] * 4, games, seed, params=[a, a, b, b], jobs=jobs)
    wa, wb = wins[0] + wins[1], wins[2] + wins[3]
    return wa, wb


def tune(args):
    names = [k for k in PARAMS if k not in FROZEN and (not args.only or k in args.only)]
    theta = {k: math.log(PARAMS[k]) for k in names}
    rng = random.Random(args.seed)
    seed = args.seed * 100000
    t0 = time.time()
    for it in range(1, args.iterations + 1):
        a = args.step / (it + 10) ** 0.602 * 11 ** 0.602
        c = args.nudge / it ** 0.101
        delta = {k: rng.choice((-1, 1)) for k in names}
        plus = {k: math.exp(theta[k] + c * delta[k]) for k in names}
        minus = {k: math.exp(theta[k] - c * delta[k]) for k in names}
        wp, wm = duel(plus, minus, args.games, seed, args.level, args.jobs)
        seed += args.games
        decided = max(1, wp + wm)
        grad = (wp - wm) / decided / (2 * c)
        for k in names:
            theta[k] += a * grad * delta[k]
        moved = sorted(names, key=lambda k: -abs(theta[k] - math.log(PARAMS[k])))[:3]
        print(f"iter {it:3d}  plus {wp:2d} - {wm:2d} minus   step {a:.4f}   biggest changes: "
              + ", ".join(f"{k} x{math.exp(theta[k]) / PARAMS[k]:.2f}" for k in moved)
              + f"   ({time.time() - t0:.0f}s)", flush=True)
        if args.out:
            with open(args.out, "w") as f:
                json.dump({k: round(math.exp(v), 4) for k, v in theta.items()}, f, indent=1)
    return {k: math.exp(v) for k, v in theta.items()}


def validate(params, games, seed, level, jobs):
    wa, wb = duel(params, {}, games, seed, level, jobs)
    lo, hi = wilson(wa, wa + wb)
    print(f"tuned {wa} - {wb} current   tuned share {wa / max(1, wa + wb):.1%}  (95% {lo:.1%}-{hi:.1%})")
    print("adopt it" if lo > 0.5 else "not shown to be stronger: keep the current weights"
          if hi > 0.5 else "weaker: keep the current weights")
    return wa, wb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iterations", type=int, default=60)
    ap.add_argument("--games", type=int, default=48, help="games per iteration (or for --validate)")
    ap.add_argument("--level", default="hard")
    ap.add_argument("--nudge", type=float, default=0.10, help="size of each trial change (log scale; 0.10 = 10%%)")
    ap.add_argument("--step", type=float, default=0.03, help="learning rate")
    ap.add_argument("--only", nargs="*", help="tune just these parameters")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int)
    ap.add_argument("--out", help="write the weights here after every iteration")
    ap.add_argument("--validate", metavar="FILE", help="play a saved set against the current weights instead")
    args = ap.parse_args()
    if args.validate:
        with open(args.validate) as f:
            validate(json.load(f), args.games, args.seed * 7919, args.level, args.jobs)
        return
    params = tune(args)
    print("\nvalidating against the current weights...")
    validate(params, max(200, args.games * 4), args.seed * 7919, args.level, args.jobs)


if __name__ == "__main__":
    main()

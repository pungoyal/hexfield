"""Bot-vs-bot benchmark, run on every core.

    python3 tools/selfplay.py --levels hard,medium,easy --games 60
    python3 tools/selfplay.py --levels hard,hard,hard,hard --styles builder,citizen,general,trader --games 120
    python3 tools/selfplay.py --levels hard,hard,hard,hard --games 20 --verbose --jobs 1

Seats are rotated through every permutation so no entry benefits from going first.
The report gives each entry's win rate with a 95% interval: with 100 games an entry
at 25% reads as roughly 17-34%, so small differences need several hundred games.
"""

import argparse
import itertools
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hexfield.ai import LEVELS, STYLES
from hexfield.match import play_headless


def wilson(wins, n, z=1.96):
    """95% score interval for a win rate (sound for small samples, unlike +-2 sigma)."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, mid - half), min(1.0, mid + half)


def one_game(job):
    """(seed, perm, levels, styles, params, options) -> (winner entry or -1, turns, fame by entry, secs)."""
    seed, perm, levels, styles, params, options = job
    seat = lambda xs: [xs[i] for i in perm] if xs else None
    s, dt = play_headless(seed, seat(levels), styles=seat(styles), params=seat(params), **options)
    fame = [0] * len(perm)
    for q, entry in enumerate(perm):
        fame[entry] = s.fame(q)
    return (perm[s.winner] if s.winner >= 0 else -1), s.turn, fame, dt


def run_match(levels, games, seed=1000, styles=None, params=None, options=None, jobs=None, on_game=None):
    """Play `games` games; returns (wins by entry, mean turns, mean fame by entry, unfinished)."""
    n = len(levels)
    perms = list(itertools.permutations(range(n)))
    random.Random(n).shuffle(perms)  # a short run still spreads the seats (6 players: 720 orders)
    work = [(seed + g, perms[g % len(perms)], levels, styles, params, options or {}) for g in range(games)]
    jobs = jobs or os.cpu_count() or 1
    wins, turns, vps, unfinished = [0] * n, [], [0] * n, 0
    if jobs == 1:
        results = map(one_game, work)
    else:
        pool = ProcessPoolExecutor(max_workers=min(jobs, games))
        results = pool.map(one_game, work, chunksize=max(1, games // (jobs * 4)))
    for g, (w, t, fame, dt) in enumerate(results):
        if w >= 0:
            wins[w] += 1
        else:
            unfinished += 1
        turns.append(t)
        vps = [a + b for a, b in zip(vps, fame)]
        if on_game:
            on_game(g, work[g], w, t, fame, dt)
    if jobs != 1:
        pool.shutdown()
    return wins, sum(turns) / len(turns), [v / games for v in vps], unfinished


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--levels",
        default="hard,medium,easy",
        help=f"comma-separated bot levels, one per seat (3 to 6): {', '.join(LEVELS)}",
    )
    ap.add_argument("--styles", help=f"play styles, one per seat: {', '.join(STYLES)} (default: balanced)")
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1000, help="seed of the first game")
    ap.add_argument("--dice", choices=("random", "balanced"), default="random")
    ap.add_argument("--layout", choices=("random", "beginner"), default="random")
    ap.add_argument("--jobs", type=int, help="worker processes (default: every core)")
    ap.add_argument("--verbose", action="store_true", help="print every game")
    args = ap.parse_args()

    levels = args.levels.split(",")
    if not 3 <= len(levels) <= 6 or any(lv not in LEVELS for lv in levels):
        ap.error(f"need 3 to 6 levels out of {', '.join(LEVELS)}")
    styles = args.styles.split(",") if args.styles else None
    if styles and (len(styles) != len(levels) or any(st not in STYLES for st in styles)):
        ap.error(f"--styles needs one of {', '.join(STYLES)} per seat")

    def show(g, job, w, t, fame, dt):
        if args.verbose:
            who = "nobody" if w < 0 else f"{levels[w]}#{w}"
            print(f"game {g:3d}  seed {job[0]}  turns {t:3d}  won by {who:<10} fame {fame}  ({dt:.1f}s)")

    t0 = time.time()
    wins, turns, vps, unfinished = run_match(
        levels,
        args.games,
        args.seed,
        styles,
        options={"dice": args.dice, "layout": args.layout},
        jobs=args.jobs,
        on_game=show,
    )
    fair = 1 / len(levels)
    print(
        f"\n{args.games} games, {len(levels)} players, avg {turns:.1f} turns, {time.time() - t0:.0f}s"
        + (f", {unfinished} unfinished" if unfinished else "")
    )
    for i, lv in enumerate(levels):
        lo, hi = wilson(wins[i], args.games)
        name = f"{lv}#{i}" + (f" {styles[i]}" if styles else "")
        print(
            f"  {name:<18} won {wins[i]:3d}/{args.games:<3d}  {wins[i] / args.games:6.1%}  "
            f"(95% {lo:.0%}-{hi:.0%})   avg {vps[i]:.1f} fame   fair share {fair:.1%}"
        )


if __name__ == "__main__":
    main()

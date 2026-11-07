"""Bot-vs-bot benchmark.

    python3 tools/selfplay.py --levels hard,medium,easy --games 60
    python3 tools/selfplay.py --levels hard,hard,hard,hard --games 20 --verbose

Seats are rotated through every permutation so no level benefits from going first.
"""

import argparse
import collections
import itertools
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from hexfield.match import play_headless  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels", default="hard,medium,easy",
                    help="comma-separated bot levels, one per seat (3 or 4)")
    ap.add_argument("--games", type=int, default=30)
    ap.add_argument("--seed", type=int, default=1000, help="seed of the first game")
    ap.add_argument("--verbose", action="store_true", help="print every game")
    args = ap.parse_args()

    levels = args.levels.split(",")
    if len(levels) not in (3, 4):
        ap.error("need 3 or 4 levels")
    perms = list(itertools.permutations(range(len(levels))))
    wins = collections.Counter()
    games = collections.Counter()
    turns = []
    t0 = time.time()
    for g in range(args.games):
        perm = perms[g % len(perms)]
        seats = [levels[i] for i in perm]
        s, dt = play_headless(args.seed + g, seats)
        for i in perm:
            games[f"{levels[i]}#{i}"] += 1
        if s.winner >= 0:
            wins[f"{levels[perm[s.winner]]}#{perm[s.winner]}"] += 1
        turns.append(s.turn)
        if args.verbose:
            vps = ", ".join(f"{seats[q]}={s.vp(q)}" for q in range(s.n))
            print(f"game {g:3d}  seed {args.seed + g}  turns {s.turn:3d}  {vps}  ({dt:.1f}s)")
    fair = 1 / len(levels)
    print(f"\n{args.games} games, {len(levels)} players, avg {sum(turns) / len(turns):.1f} turns, "
          f"{time.time() - t0:.0f}s")
    for key in sorted(games):
        rate = wins[key] / games[key]
        print(f"  {key:<10} won {wins[key]:3d}/{games[key]:<3d}  {rate:6.1%}   (fair share {fair:.1%})")


if __name__ == "__main__":
    main()

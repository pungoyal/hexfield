"""Interactive terminal game: you vs. 2 or 3 machine opponents."""

import argparse
import random
import re
import textwrap
import time
from collections import Counter

from .ai import AIPlayer, describe
from .constants import (DESERT, KNIGHT, MONOPOLY, PIPS, RES_NAMES, ROAD_BUILDING, YEAR_OF_PLENTY,
                        parse_res)
from .game import (DISCARD, MAIN, OVER, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD,
                   SETUP_SETTLE, GameState, IllegalAction, fmt_cards)
from .match import resolve_proposal
from .render import (PLAYER_COLORS, PLAYER_COLOR_NAMES, RES_ABBR, Renderer, clip, pad, plural, second_person,
                     term_width, use_color_default)

AI_NAMES = ["Vera", "Magnus", "Kasimir", "Ingrid", "Tobias", "Selene"]

HELP_ROWS = (
    ("COMMANDS", (
        ("r, roll", "roll the dice"),
        ("s [v], settle [v]", "settlement at vertex v (no v: list the spots)"),
        ("c [v], city [v]", "upgrade your settlement at v to a city"),
        ("rd [e], road [e]", "road on edge e (no e: show the edge ids)"),
        ("d, dev, buy", "buy a development card"),
        ("k, knight", "play a knight"),
        ("rb, roadbuilding", "play road building"),
        ("mono <res>", "play monopoly, e.g. mono ore"),
        ("yop <res> <res>", "play year of plenty, e.g. yop wheat ore"),
        ("t <give> <get> [n]", "bank/port trade at your best ratio;"),
        ("", "n = number of trades, e.g. t wood ore 2"),
        ("offer <cards> for <cards>", "offer the bots a trade: offer 2 wood for ore"),
        ("e, end", "end your turn"),
    )),
    ("COACHING", (
        ("h, hint", "engine's top options for this decision"),
        ("eval", "engine evaluation of every player + production"),
        ("review [on|off]", "post-move review of your decisions (default on)"),
        ("reason [on|off]", "show the bots' reasoning (default on)"),
        ("undo", "take back your last decision (dice stay the same)"),
    )),
    ("VIEW", (
        ("b, board", "redraw the board"),
        ("ids", "show all vertex ids"),
        ("log [n]", "recent events"),
        ("cost", "building costs"),
        ("stats", "dice histogram"),
        ("q, quit", "quit (asks first)"),
    )),
)


def help_text():
    left = max(len(k) for _, rows in HELP_ROWS for k, _ in rows) + 2
    inner = left + max(len(v) for _, rows in HELP_ROWS for _, v in rows) + 2
    out = [""]
    for i, (title, rows) in enumerate(HELP_ROWS):
        corner = "┌┐" if i == 0 else "├┤"
        out.append(f" {corner[0]}─ {title} " + "─" * (inner - len(title) - 3) + corner[1])
        out += [f" │ {k:<{left}}{v:<{inner - left - 1}}│" for k, v in rows]
    out.append(" └" + "─" * inner + "┘")
    out.append(" Resources: wood (w) brick (b) sheep (s) wheat (wh) ore (o).")
    out.append(' Cards: "2 wood ore", "w w o", "2w 1o".  Vertices and edges are numbered, hexes lettered.')
    out.append(" Robber: a hex letter, optionally with a victim: F  or  F vera.")
    return "\n".join(out) + "\n"


VERDICTS = [
    (0.05, "best", 46, "✓"),
    (0.20, "good", 114, "✓"),
    (0.50, "inaccuracy", 220, "?!"),
    (1.00, "mistake", 208, "?"),
    (9e9, "blunder", 196, "??"),
]


class Quit(Exception):
    pass


class Undo(Exception):
    pass


def parse_cards(tokens):
    """'2 wood ore', 'w w o', '2w 1o' -> [5 counts] or None."""
    counts = [0] * 5
    pending = None
    for tok in tokens:
        tok = tok.lower().strip(",")
        if not tok or tok in ("and", "+"):
            continue
        if tok.isdigit():
            pending = int(tok)
            continue
        before, name, after = re.fullmatch(r"(\d*)(.*?)(\d*)", tok).groups()
        r = parse_res(name)
        if r is None:
            return None
        counts[r] += int(before or after or pending or 1)
        pending = None
    return counts


class Game:
    def __init__(self, args):
        self.args = args
        seed = args.seed if args.seed is not None else random.randrange(1, 10 ** 6)
        self.seed = seed
        n = args.opponents + 1
        rng = random.Random(seed)
        seat = (args.seat - 1) if args.seat else rng.randrange(n)
        names = AI_NAMES[:]
        rng.shuffle(names)
        names = names[:n]
        names[seat] = args.name
        self.human = seat
        self.s = GameState(n, seed=seed, names=names)
        self.agents = [None if p == seat else AIPlayer(self.s.board, args.level, seed=seed * 7 + p)
                       for p in range(n)]
        self.coach = AIPlayer(self.s.board, "hard", seed=seed)
        self.r = Renderer(self.s, seat, use_color_default() and not args.no_color)
        self.st = self.r.st
        self.printed = 0
        self.review_on = not args.no_review
        self.reason_on = not args.no_reasoning
        self.delay = 0.0 if args.fast else args.delay
        self.history = []        # undo snapshots
        self.reviews = []        # (turn, phase, my, best, delta, verdict)
        self.pending_review = None
        self.shown = None
        self.last_reason = ""

    # ------------------------------------------------------------- output
    def out(self, msg=""):
        print(msg)

    def flush_events(self):
        evs = self.s.events
        while self.printed < len(evs):
            e = evs[self.printed]
            self.printed += 1
            if self.r.visible_event(e):
                _, p, kind, msg = e
                if kind == "turn":
                    self.print_review()  # your review belongs to your turn
                    self.out("")
                self.out(self.r.fmt_event(p, kind, msg))
        self.print_review()

    def print_review(self):
        if self.pending_review:
            self.out(self.pending_review)
            self.pending_review = None

    def apply(self, a):
        """Play `a` on the real state. Monopoly also says whom the cards came from
        (each player hands them over in the open)."""
        s = self.s
        p, n = s.actor(), len(s.events)
        before = [h[:] for h in s.hands]
        s.apply(a)
        if a[0] != "play_mono":
            return
        r = a[1]
        took = ", ".join(f"{before[q][r] - s.hands[q][r]} from {s.names[q]}" for q in range(s.n)
                         if q != p and before[q][r] > s.hands[q][r])
        for i in range(n, len(s.events)):
            t, who, kind, msg = s.events[i]
            if took and kind == "dev" and "Monopoly" in msg:
                s.events[i] = (t, who, kind, f"{msg} ({took})")

    def show(self, vertex_ids=None, edge_ids=None, highlight=None):
        self.r.s = self.s
        self.out("")
        self.out(self.r.screen(vertex_ids, edge_ids, highlight))

    def ask(self, prompt):
        st = self.st
        p = self.human
        tag = st(f" {self.s.names[p]} ", fg=16, bg=PLAYER_COLORS[p], bold=True)
        try:
            line = input(f"{tag} {st(prompt, bold=True)} {st('›', fg=PLAYER_COLORS[p], bold=True)} ")
        except EOFError:
            raise Quit()
        except KeyboardInterrupt:
            print()
            raise Quit()
        return line.strip()

    def command(self, prompt, redraw=None, parse=None):
        """Ask until the answer is more than a common command; return its words.
        `redraw` re-shows the board for 'b' when the prompt needs ids marked on it.
        `parse(words)` is tried first: if it returns anything but None, that is the answer
        (so 'b w w' at the discard prompt means brick, wood, wood - not 'board')."""
        while True:
            words = self.ask(prompt).split()
            if not words:
                continue
            if parse and (got := parse(words)) is not None:
                return got
            cmd = words[0].lower()
            if redraw and cmd in ("b", "board", "map"):
                redraw()
            elif not self.common(cmd, words[1:]):
                return words

    def match_player(self, answer, players):
        """The first of `players` whose name starts with `answer`, else None."""
        if not answer:
            return None
        return next((p for p in players if self.s.names[p].lower().startswith(answer.lower())), None)

    def info(self, msg):
        self.out(self.st("  " + msg, fg=110))

    def warn(self, msg):
        self.out(self.st("  ✗ " + msg, fg=203))

    # --------------------------------------------------------------- run
    def replay(self):
        return (f"python3 -m hexfield --seed {self.seed} --seat {self.human + 1} "
                f"--opponents {self.args.opponents}")

    def run(self):
        st = self.st
        you = PLAYER_COLOR_NAMES[self.human]
        self.out(self.banner())
        self.out(f"  Seed {st(str(self.seed), bold=True)}  ·  replay this exact board and dice with "
                 f"{st(self.replay(), fg=117)}")
        order = " → ".join(self.r.pname(p) for p in range(self.s.n))
        self.out(f"  You are {st(you, fg=PLAYER_COLORS[self.human], bold=True)} (seat {self.human + 1}).  "
                 f"Turn order: {order}.  Bots: {self.args.level}.")
        self.out(f"  Type {st('help', bold=True)} for commands, {st('hint', bold=True)} for engine advice.")
        try:
            while self.s.phase != OVER:
                p = self.s.actor()
                if p == self.human:
                    self.human_turn_step()
                else:
                    self.ai_step(p)
                self.flush_events()
            self.game_over()
        except Quit:
            self.out("\n  Bye! Seed was " + str(self.seed))

    def banner(self):
        st = self.st
        art = [
            "   ╔═╗╔═╗╔╦╗╔═╗╔╗╔   ",
            "   ║  ╠═╣ ║ ╠═╣║║║   ",
            "   ╚═╝╩ ╩ ╩ ╩ ╩╝╚╝   ",
        ]
        cols = [208, 178, 28]
        lines = [st(a, fg=c, bold=True) for a, c in zip(art, cols)]
        lines.append(st("   settle · trade · build · outthink the machines", dim=True))
        return "\n" + "\n".join(lines) + "\n"

    # ---------------------------------------------------------------- AI
    def ai_step(self, p):
        s = self.s
        a, why = self.agents[p].decide(s)
        if a[0] == "propose":
            _, give, get = a
            self.out(" " + self.st("⇄", fg=PLAYER_COLORS[p], bold=True) +
                     f" {s.names[p]} offers {fmt_cards(give)} for {fmt_cards(get)}")
            if self.reason_on:
                self.reason(why)

            def on_reply(q, ok, gain):
                if q != self.human:
                    self.out(self.st(f"     {s.names[q]} {'accepts' if ok else 'declines'}", dim=True))

            resolve_proposal(s, self.agents, p, give, get, ask_human=self.ask_trade, on_reply=on_reply)
            self.flush_events()
            return
        self.apply(a)
        self.flush_events()
        if self.reason_on and why and a[0] not in ("roll", "end"):
            self.reason(why)
        else:
            self.last_reason = ""
        if self.delay:
            time.sleep(self.delay)

    def reason(self, why):
        # a knight's reason already explains the robber move that follows it
        if why not in self.last_reason:
            why_you = second_person(why, self.s.names[self.human])
            self.out(self.st(f"     ↳ {why_you}", fg=245, italic=True))
        self.last_reason = why

    def ask_trade(self, q, proposer, give, get):
        """Bot offers the human a trade. give/get are from the proposer's side."""
        s = self.s
        hand = s.hands[q]
        after = [hand[r] + give[r] - get[r] for r in range(5)]
        self.out(f"  You have {self.r.hand_chips(hand)}  →  after {self.r.hand_chips(after)}")
        self.info("this trade " + self.r.hand_change(hand, after, q))
        while True:
            ans = self.ask(f"{s.names[proposer]} gives you {fmt_cards(give)} for your {fmt_cards(get)}. "
                           "accept? [y/N, hint]").lower()
            if ans in ("h", "hint"):
                ok, gain = self.coach.evaluate_offer(s, q, proposer, give, get)
                self.info(f"engine: {'ACCEPT' if ok else 'DECLINE'} (net value for you {gain:+.2f}, "
                          f"includes what {s.names[proposer]} builds with it)")
                continue
            if ans == "undo":
                self.warn("can't undo in the middle of an offer - answer first")
                continue
            if ans in ("y", "yes", "n", "no", ""):
                yes = ans in ("y", "yes")
                if self.review_on:
                    ok, gain = self.coach.evaluate_offer(s, q, proposer, give, get)
                    my = gain if yes else 0.0
                    best = max(gain, 0.0)
                    eng = "accept" if gain > 0 else "decline"
                    self.report(my, best, f"{eng} (trade worth {gain:+.2f} to you)",
                                f"{'accept' if yes else 'decline'} {s.names[proposer]}'s offer")
                return yes
            self.warn("answer y or n")

    # ------------------------------------------------------------- undo
    def snapshot(self):
        agents = [(a.rng.getstate(), a._proposed) if a else None for a in self.agents]
        self.history.append((self.s.clone(sim=False), agents, len(self.reviews)))

    def undo(self):
        # drop the snapshot of the current decision, restore the previous one
        if len(self.history) < 2:
            self.warn("nothing to undo")
            self.history.pop()  # the current decision is re-snapshotted
            return False
        self.shown = None  # the next prompt redraws the board once
        self.history.pop()
        state, agents, nrev = self.history.pop()
        self.s = state
        for a, saved in zip(self.agents, agents):
            if a:
                a.rng.setstate(saved[0])
                a._proposed = saved[1]
        del self.reviews[nrev:]
        self.pending_review = None
        self.printed = len(self.s.events)
        self.info(f"undone - back to turn {self.s.turn} ({self.s.phase.replace('_', ' ')})")
        return True

    # ------------------------------------------------------------ review
    def report(self, mine, best, best_desc, label=None, cmd=None):
        delta = max(0.0, best - mine)
        name, col, sym = next(v[1:] for v in VERDICTS if delta < v[0])
        st = self.st
        head = f"  ◆ {sym} {name}"
        msg = st(head, fg=col, bold=True)
        if delta >= 0.05:
            cost = f"  (−{delta:.2f} VP-eq)"
            lead = f"  engine: → {cmd}  " if cmd else "  engine: "
            room = term_width() - 1 - len(head) - len(cost) - len(lead)
            msg += st(cost, fg=col) + st("  engine: ", fg=250)
            if cmd:
                msg += st(f"→ {cmd}", fg=117, bold=True) + "  "
            msg += st(clip(best_desc, room), fg=250)
        self.pending_review = msg
        best_desc = (f"→ {cmd}  " if cmd else "") + best_desc
        self.reviews.append((self.s.turn, self.s.phase, label or "", best_desc, delta, name))

    def trivial(self, a):
        s = self.s
        p = self.human
        if s.phase == PRE_ROLL:
            return not s.playable(p, KNIGHT)
        if s.phase == MAIN and a[0] == "end":
            return all(x[0] == "end" for x in s.legal_actions(include_trades=True))
        if s.phase in (SETUP_ROAD, ROAD_BUILD):
            return len(s.legal_actions()) <= 1
        return False

    def review_action(self, a, times=1):
        """Review `a` (played `times` times in a row, e.g. 't wood ore 3') as one decision."""
        if not self.review_on or self.trivial(a):
            return
        s = self.s
        try:
            best_sc, best_a, best_why = self.coach.best_value(s)
            if best_a is None:
                return
            if times == 1 and (a == best_a or (best_a[0] == "macro" and a == best_a[1][0])):
                mine = best_sc
            else:
                t = s.clone(sim=False)
                for _ in range(times - 1):
                    t.apply(a)
                mine = self.coach.value_of_action(t, a)
            if s.phase == MAIN and best_why.startswith("plan:"):
                desc = best_why[6:]
            else:
                desc = self.option_text(best_a, best_why)
            label = describe(s, a) + (f" ×{times}" if times > 1 else "")
            self.report(mine, best_sc, desc, label, self.command_for(best_a))
        except Exception as ex:  # reviews must never crash the game
            self.out(self.st(f"  (review unavailable: {ex})", dim=True))

    def option_text(self, a, why):
        """An engine option in words, without repeating itself."""
        s = self.s
        if s.phase == MAIN or (why and s.phase in (SETUP_SETTLE, SETUP_ROAD, ROBBER, PRE_ROLL)):
            return why  # these reasons already name the move (and the command shows the id)
        d = describe(s, a)
        return d if not why or why == d else f"{d} - {why}"

    def command_for(self, a):
        """What to type for engine action `a` (its first step), or None."""
        s = self.s
        k = a[0]
        if k == "macro":
            a, k = a[1][0], "bank"
        bare = s.phase in (SETUP_SETTLE, SETUP_ROAD, ROAD_BUILD)
        if k in ("settle", "road", "city"):
            return str(a[1]) if bare else f"{dict(settle='s', road='rd', city='c')[k]} {a[1]}"
        simple = {"buy_dev": "d", "play_knight": "k", "play_road": "rb", "roll": "r", "end": "e"}
        if k in simple:
            return simple[k]
        if k == "play_mono":
            return f"mono {RES_NAMES[a[1]]}"
        if k == "play_yop":
            return f"yop {RES_NAMES[a[1]]} {RES_NAMES[a[2]]}"
        if k == "bank":
            return f"t {RES_NAMES[a[1]]} {RES_NAMES[a[2]]}"
        if k == "discard":
            return " ".join(f"{c} {RES_NAMES[r]}" for r, c in enumerate(a[1]) if c)
        if k == "robber":
            return s.board.hex_label(a[1]) + (f" {s.names[a[2]].lower()}" if a[2] != -1 else "")
        return None

    # ------------------------------------------------------------- human
    def human_turn_step(self):
        s = self.s
        ph = s.phase
        self.snapshot()
        try:
            if ph == SETUP_SETTLE:
                self.human_setup_settle()
            elif ph == SETUP_ROAD:
                self.human_setup_road()
            elif ph == DISCARD:
                self.human_discard()
            elif ph == ROBBER:
                self.human_robber()
            elif ph == ROAD_BUILD:
                self.human_free_road()
            elif ph in (PRE_ROLL, MAIN):
                self.human_main()
        except Undo:
            self.undo()

    def common(self, cmd, args):
        """Commands valid at every prompt. Returns True if handled."""
        if cmd in ("q", "quit", "exit"):
            if self.ask("really quit? [y/n]").lower().startswith("y"):
                raise Quit()
            return True
        if cmd in ("help", "?"):
            self.out(help_text())
            return True
        if cmd in ("b", "board", "map"):
            self.show()
            return True
        if cmd == "ids":
            self.show(vertex_ids=range(self.s.board.V))
            return True
        if cmd in ("h", "hint"):
            self.hint()
            return True
        if cmd == "eval":
            self.eval_cmd()
            return True
        if cmd == "stats":
            self.stats()
            return True
        if cmd == "log":
            k = int(args[0]) if args and args[0].isdigit() else 25
            for line in self.r.recent_log(k):
                self.out(line)
            return True
        if cmd == "cost" or cmd == "costs":
            self.out("  road: wood+brick · settlement: wood+brick+sheep+wheat · "
                     "city: 2 wheat+3 ore · dev card: sheep+wheat+ore")
            return True
        if cmd == "review":
            self.review_on = self.toggle("post-move review", "review", self.review_on, args)
            return True
        if cmd in ("reason", "reasoning"):
            self.reason_on = self.toggle("bot reasoning", "reason", self.reason_on, args)
            return True
        if cmd == "undo":
            raise Undo()
        return False

    def toggle(self, what, cmd, on, args):
        """'review on|off': set it; bare 'review': just say how it is."""
        arg = args[0].lower() if args else ""
        if arg in ("on", "off"):
            on = arg == "on"
        elif arg:
            self.warn(f"usage: {cmd} on|off")
        self.info(f"{what} is {'on' if on else 'off'}" + ("" if arg else f"  ({cmd} on|off to change)"))
        return on

    def hint(self):
        s = self.s
        t0 = time.time()
        ranked = self.coach.analyze(s, top=5)
        if not ranked:
            self.info("no options to analyse")
            return
        best = ranked[0][0]
        self.out(self.st(f"  Engine options ({time.time() - t0:.1f}s):", bold=True))
        cmds = [self.command_for(a) or "" for _, a, _ in ranked]
        cw = max(len(c) for c in cmds) + 2
        indent = 13 + cw + 2
        for i, ((sc, a, why), cmd) in enumerate(zip(ranked, cmds)):
            d = best - sc
            tag = self.st("best", fg=46, bold=True) if i == 0 else self.st(f"−{d:.2f}", fg=220 if d < 0.5 else 208)
            arrow = self.st(f"→ {cmd}", fg=117, bold=True) if cmd else ""
            # long plans wrap under their own column instead of the terminal's left edge
            lines = textwrap.wrap(self.option_text(a, why), max(30, term_width() - 1 - indent)) or [""]
            self.out(f"   {i + 1}. {pad(tag, 6)} {pad(arrow, cw + 2)}{lines[0]}")
            for more in lines[1:]:
                self.out(" " * indent + more)

    def eval_cmd(self):
        s = self.s
        vals = self.coach.ev.values(self.coach.view(s, self.human), self.human)
        self.out(self.st("  Engine evaluation (VP-equivalents, your point of view):", bold=True))
        top = max(vals)
        for p in range(s.n):
            prod = self.coach.ev.production(s, p)
            bar_len = int(max(0, vals[p]) / max(top, 1) * 24)
            bar = self.st("█" * bar_len, fg=PLAYER_COLORS[p])
            pips = " ".join(f"{RES_ABBR[r]}{int(prod[r]):>2}" for r in range(5))
            self.out(f"   {self.r.pname(p, 8)} {vals[p]:5.2f} {pad(bar, 25)} pips/36: {pips}  (={int(sum(prod))})")
        self.out(self.st("   pips = expected cards per 36 rolls (robber-blocked hexes excluded)", dim=True))

    def stats(self):
        s = self.s
        hist = s.roll_hist
        total = sum(hist)
        self.out(self.st(f"  Dice ({total} rolls)  actual vs expected", bold=True))
        for t in range(2, 13):
            exp = PIPS.get(t, 6 if t == 7 else 0) / 36 * total
            bar = "█" * hist[t]
            self.out(f"   {t:>2} {hist[t]:>3} {exp:5.1f}  {self.st(bar, fg=160 if t == 7 else 75)}")

    def act(self, a, times=1):
        """Check `a` (`times` in a row) on a copy, so it raises IllegalAction before
        anything happens; review it as one decision, then play it."""
        t = self.s.clone(sim=False)
        for _ in range(times):
            t.apply(a)
        self.review_action(a, times)
        for _ in range(times):
            self.apply(a)

    def spot_list(self, legal, top=8):
        """The legal settlement spots, best (most pips) first, a few per line."""
        b = self.s.board
        best = sorted(legal, key=lambda v: (-b.vertex_total_pips[v], v))[:top]
        items = [f"{v:>2}: {b.describe_vertex(v)} = {b.vertex_total_pips[v]} pips" for v in best]
        colw = max(len(x) for x in items) + 3
        per = max(1, min(2, (term_width() - 4) // colw))
        more = f" (top {len(best)} of {len(legal)})" if len(legal) > len(best) else ""
        self.out(self.st(f"  Open spots by pips{more}:", bold=True))
        for i in range(0, len(items), per):
            self.out("   " + "".join(x.ljust(colw) for x in items[i:i + per]).rstrip())

    def pick_spot(self, prompt, spot, legal):
        """Show the board with the legal vertex/edge ids marked; ask until one is picked."""
        def redraw():
            self.show(**{f"{spot}_ids": legal})
            if spot == "vertex":
                self.spot_list(legal)
        redraw()
        while True:
            cmd = self.command(prompt, redraw)[0]
            if cmd.isdigit() and int(cmd) in legal:
                return int(cmd)
            self.warn(f"pick one of the highlighted {spot} ids")

    # setup -------------------------------------------------------------
    def human_setup_settle(self):
        s = self.s
        which = "first" if s.setup_idx < s.n else "second"
        v = self.pick_spot(f"place your {which} settlement (vertex id)", "vertex",
                           s.legal_settlements(self.human, setup=True))
        self.info(f"vertex {v}: {s.board.describe_vertex(v)}")
        self.act(("settle", v))

    def human_setup_road(self):
        e = self.pick_spot("place a road next to it (edge id)", "edge",
                           self.s.legal_setup_roads(self.human))
        self.act(("road", e))

    def human_free_road(self):
        s = self.s
        e = self.pick_spot(f"road building: place a free road ({s.free_roads} left)", "edge",
                           s.legal_roads(self.human))
        self.act(("road", e))

    # discard / robber ----------------------------------------------------
    def human_discard(self):
        s = self.s
        k = s.discards[self.human]
        hand = s.hands[self.human]
        self.out("")
        self.out("  " + self.r.hand_chips(hand))
        while True:
            # cards first: 'b w w' is brick, wood, wood here, not the board command
            counts = self.command(f"a 7! discard {k} cards (e.g. '2 wood ore')", parse=parse_cards)
            if isinstance(counts[0], str):
                self.warn("couldn't parse that - try e.g. 'wood 2 ore'")
            elif sum(counts) != k:
                self.warn(f"that's {plural(sum(counts), 'card')}, need exactly {k}")
            elif any(counts[r] > hand[r] for r in range(5)):
                self.warn("you don't have those cards")
            else:
                break
        if self.review_on:
            (best_v, best_c), (mine_v, _) = self.coach.rank_discards(s, self.human, counts)
            self.report(mine_v, best_v, "discard " + fmt_cards(best_c), "discard " + fmt_cards(counts))
        self.apply(("discard", tuple(counts)))

    def robber_table(self):
        """Every hex with a building: whom the robber blocks there (pips) and whom you can rob."""
        s, st = self.s, self.st
        b, me = s.board, self.human
        rows = []
        for h in range(b.H):
            if h == s.robber:
                continue
            pips = Counter()
            for v in b.hex_vertices[h]:
                if s.v_owner[v] != -1:
                    pips[s.v_owner[v]] += PIPS[b.hex_number[h]] * s.v_level[v]
            if pips:
                others = sum(n for q, n in pips.items() if q != me)
                rows.append((me in pips, -others, h, pips))
        if not rows:
            return
        rows.sort()
        r = b.hex_resource
        what = {h: "desert" if r[h] == DESERT else f"{b.hex_number[h]:>2} {RES_NAMES[r[h]]}" for *_, h, _ in rows}
        blocks = {h: ", ".join(f"{s.names[q]} {n}" for q, n in sorted(pips.items()) if q != me) or "-"
                  for *_, h, pips in rows}
        robs = {h: ", ".join(f"{s.names[q]} ({plural(s.hand_size(q), 'card')})" for q in s.robber_targets(me, h)) or "-"
                for *_, h, _ in rows}
        ww = max(len(x) for x in what.values()) + 2
        bw = max(len(x) for x in blocks.values())
        rw = max(len(x) for x in robs.values())
        self.out(st(f"  Robber: {'hex':<{ww + 1}} {'blocks (pips)':<{bw}}  can rob", bold=True))
        for mine, _, h, pips in rows:
            line = f"  {'':8}{b.hex_label(h)} {what[h]:<{ww}} {blocks[h]:<{bw}}  "
            if mine:
                self.out(st(line + robs[h].ljust(rw), dim=True) + st(f"  ✗ blocks you ({pips[me]})", fg=203))
            else:
                self.out(line + robs[h])

    def human_robber(self):
        s = self.s
        self.show()
        self.robber_table()
        while True:
            label, *args = self.command("move the robber: hex letter [victim name]")
            h = s.board.hex_from_label(label)
            if h is not None and h != s.robber:
                break
            self.warn("give a hex letter from the board (not the robber's current hex)")
        vics = s.robber_targets(self.human, h)
        victim = vics[0] if vics else -1
        if len(vics) > 1:
            opts = ", ".join(f"{s.names[v]} ({plural(s.hand_size(v), 'card')})" for v in vics)
            victim = self.match_player(args[0], vics) if args else None
            if args and victim is None:
                self.warn(f"{args[0]} isn't next to {s.board.hex_label(h)}")
            while victim is None:
                # names first, then the usual commands (hint, undo, q...)
                got = self.command(f"steal from whom? {opts}", parse=lambda w: self.match_player(w[0], vics))
                if isinstance(got, int):
                    victim = got
                else:
                    self.warn(f"type a name: {', '.join(s.names[v] for v in vics)}")
        self.act(("robber", h, victim))

    # main turn -----------------------------------------------------------
    def human_main(self):
        s = self.s
        if s.phase == PRE_ROLL and self.args.autoroll and not any(
                s.playable(self.human, c) for c in (KNIGHT, ROAD_BUILDING, MONOPOLY, YEAR_OF_PLENTY)):
            self.apply(("roll",))
            self.flush_events()
            return
        if s.phase == PRE_ROLL or self.shown != (s.turn, s.current):
            self.show()
            self.shown = (s.turn, s.current)
        prompt = "roll (r) or play a knight (k)" if s.phase == PRE_ROLL else "your move"
        if s.phase == MAIN:
            self.out(self.r.build_status(self.human))
        while True:
            cmd, *args = self.command(prompt)
            try:
                a = self.parse_main(cmd.lower(), args)
                if a is None:
                    continue
                if a[0] == "offer":
                    self.human_offer(a[1], a[2])
                    return
                if a[0] == "repeat":
                    _, times, a = a
                    self.act(a, times)
                else:
                    self.act(a)
            except IllegalAction as ex:
                self.warn(str(ex))
                continue
            if a[0] in ("settle", "city", "road", "play_knight", "play_road") and s.phase == MAIN:
                self.flush_events()
                self.show()
            return

    def build(self, kind, noun, legal, args, none_left):
        """(kind, id) for a build command; with no id, mark the legal ones and ask."""
        if self.s.phase != MAIN:
            raise IllegalAction("roll first")
        spot = "edge" if kind == "road" else "vertex"
        if not args:
            if not legal:
                raise IllegalAction(none_left)
            if kind == "city" and len(legal) == 1:
                return ("city", legal[0])
            self.show(**{f"{spot}_ids": legal})
            if kind == "settle":
                self.spot_list(legal)
            args = [self.ask(f"{noun} at ({spot} id, blank to cancel)")]
            if not args[0]:
                return None
        last = (self.s.board.E if kind == "road" else self.s.board.V) - 1
        if not args[0].isdigit():
            short = {"settle": "s", "city": "c", "road": "rd"}[kind]
            raise IllegalAction(f"'{args[0]}' isn't a {spot} id - type '{short}' alone to see them marked")
        if int(args[0]) > last:
            raise IllegalAction(f"there is no {spot} {args[0]} (ids run 0-{last})")
        return (kind, int(args[0]))

    def parse_main(self, cmd, args):
        s = self.s
        p = self.human
        if cmd in ("r", "roll"):
            return ("roll",)
        if cmd in ("e", "end", "done", "pass"):
            return ("end",)
        if cmd in ("s", "settle", "settlement"):
            return self.build("settle", "settlement", s.legal_settlements(p), args,
                              "no legal settlement spots (need a road leading to an open spot)")
        if cmd in ("c", "city"):
            return self.build("city", "city", s.legal_cities(p), args, "no settlements to upgrade")
        if cmd in ("rd", "road"):
            return self.build("road", "road", s.legal_roads(p), args, "nowhere to build a road")
        if cmd in ("d", "dev", "buy"):
            return ("buy_dev",)
        if cmd in ("k", "knight"):
            return ("play_knight",)
        if cmd in ("rb", "roadbuilding", "road_building"):
            return ("play_road",)
        if cmd == "play" and args:
            return self.parse_main(args[0], args[1:])
        if cmd in ("mono", "monopoly"):
            r = parse_res(args[0]) if args else None
            if r is None:
                raise IllegalAction("usage: mono <resource>")
            return ("play_mono", r)
        if cmd in ("yop", "plenty", "year"):
            rs = [parse_res(x) for x in args[:2]]
            if len(rs) == 1 and rs[0] is not None:
                rs = rs * 2
            if len(rs) != 2 or None in rs:
                raise IllegalAction("usage: yop <res> <res>")
            return ("play_yop", min(rs), max(rs))
        if cmd in ("t", "trade", "bank"):
            words = [x for x in args if not x.isdigit()]
            if len(words) < 2:
                raise IllegalAction("usage: t <give> <get> [n]   e.g. t wood ore  (n = number of trades)")
            give, get = parse_res(words[0]), parse_res(words[-1])
            if give is None or get is None:
                raise IllegalAction("unknown resource")
            a = ("bank", give, get)
            # a trailing number repeats the trade: 't wood ore 2' - all of them or none
            n = int(args[-1]) if args[-1].isdigit() else 1
            if n == 1:
                return a
            if n < 1:
                raise IllegalAction("n is the number of trades, e.g. t wood ore 2")
            ratio = s.trade_ratio(p, give)
            if s.phase == MAIN and give != get and s.hands[p][give] < ratio * n:
                raise IllegalAction(f"{n} trades need {ratio * n} {RES_NAMES[give]} at {ratio}:1 - "
                                    f"you have {s.hands[p][give]}")
            if s.phase == MAIN and s.bank[get] < n:
                raise IllegalAction(f"the bank has only {s.bank[get]} {RES_NAMES[get]}")
            return ("repeat", n, a)
        if cmd == "offer":
            left, sep, right = f" {' '.join(args).lower()} ".partition(" for ")
            if not sep:
                raise IllegalAction("usage: offer <cards> for <cards>   e.g. offer 2 wood for ore")
            give, get = parse_cards(left.split()), parse_cards(right.split())
            if give is None or get is None or not any(give) or not any(get):
                raise IllegalAction("couldn't parse the offer")
            if s.phase != MAIN:
                raise IllegalAction("roll first")
            if any(give[r] > s.hands[p][r] for r in range(5)):
                raise IllegalAction("you don't have those cards")
            return ("offer", tuple(give), tuple(get))
        raise IllegalAction(f"unknown command '{cmd}' (type help)")

    def human_offer(self, give, get):
        s = self.s
        st = self.st
        accepters = []
        for q in range(s.n):
            if q == self.human:
                continue
            # a bot without the cards simply declines - its hand stays hidden
            ok, gain = self.agents[q].evaluate_offer(s, q, self.human, give, get)
            self.out(f"     {self.r.pname(q)}: " + (st("accepts", fg=46, bold=True) if ok else st("declines", fg=203)))
            if ok:
                accepters.append(q)
        q = accepters[0] if len(accepters) == 1 else None
        if len(accepters) > 1:
            names = "/".join(s.names[q] for q in accepters)
            while q is None:
                ans = self.ask(f"trade with whom? {names} (blank = nobody)")
                if not ans:
                    break
                q = self.match_player(ans, accepters)
        if q is None:
            self.history.pop()  # nothing happened: drop this decision's undo snapshot
            return
        self.act(("trade", q, give, get))

    # ---------------------------------------------------------- game over
    def game_over(self):
        s = self.s
        st = self.st
        self.show()
        w = s.winner
        self.out("")
        if w == self.human:
            self.out(st("  ♛ ♛ ♛  YOU WIN!  ♛ ♛ ♛", fg=226, bold=True))
        else:
            self.out(st(f"  {s.names[w]} wins.", fg=PLAYER_COLORS[w], bold=True))
        self.out("")
        self.out(st("  Final scores", bold=True))
        for p in sorted(range(s.n), key=lambda q: -s.vp(q)):
            parts = [plural(len(s.settlements[p]), "settlement"), plural(len(s.cities[p]), "city", "cities")]
            if s.longest == p:
                parts.append("longest road")
            if s.army == p:
                parts.append("largest army")
            if s.vp_cards(p):
                parts.append(plural(s.vp_cards(p), "VP card"))
            self.out(f"   {self.r.pname(p, 9)} {s.vp(p):>2} VP  ({', '.join(parts)})")
        if self.reviews:
            self.out("")
            self.out(st("  Your decision review", bold=True))
            counts = Counter(r[5] for r in self.reviews)
            self.out("   " + "".join(st(f"{name} {counts[name]}   ", fg=col, bold=True)
                                     for _, name, col, _ in VERDICTS))
            loss = sum(r[4] for r in self.reviews)
            acc = 100.0 * sum(1 for r in self.reviews if r[4] < 0.2) / len(self.reviews)
            self.out(f"   accuracy {acc:.0f}% of {len(self.reviews)} decisions  ·  total value lost {loss:.2f} VP-eq")
            worst = [r for r in sorted(self.reviews, key=lambda r: -r[4])[:5] if r[4] >= 0.2]
            if worst:
                self.out(st("   Biggest misses:", bold=True))
                for turn, ph, mine, best, delta, name in worst:
                    self.out(f"    turn {turn:>3} {ph.replace('_', ' '):<16} you: {mine}")
                    self.out(st(clip(f"             −{delta:.2f}  engine: {best}", term_width() - 1), dim=True))
        self.out("")
        self.out(f"  Replay: {self.replay()}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hexfield", description="Hex-board settling and trading game vs. tough bots")
    ap.add_argument("-o", "--opponents", type=int, choices=(2, 3), default=3, help="number of bots (2 or 3)")
    ap.add_argument("-l", "--level", choices=("easy", "medium", "hard"), default="hard")
    ap.add_argument("--seed", type=int, help="board/dice seed (replays are exact)")
    ap.add_argument("--seat", type=int, choices=(1, 2, 3, 4), help="your seat (1 = goes first)")
    ap.add_argument("--name", default="You")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--no-review", action="store_true", help="disable post-move review")
    ap.add_argument("--no-reasoning", action="store_true", help="hide bot reasoning")
    ap.add_argument("--fast", action="store_true", help="no pause between bot actions")
    ap.add_argument("--delay", type=float, default=0.15, help="seconds between bot actions")
    ap.add_argument("--no-autoroll", dest="autoroll", action="store_false",
                    help="always ask before rolling (default: auto-roll when no dev card is playable)")
    args = ap.parse_args(argv)
    if args.seat and args.seat > args.opponents + 1:
        ap.error("seat must be <= number of players")
    try:
        import readline  # noqa: F401  (history + line editing)
    except ImportError:
        pass
    Game(args).run()


if __name__ == "__main__":
    main()

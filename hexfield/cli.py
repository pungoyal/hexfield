"""Interactive terminal game: you vs. 2 or 3 machine opponents."""

import argparse
import random
import time

from .ai import AIPlayer, describe
from .constants import (KNIGHT, MONOPOLY, RES_NAMES, ROAD_BUILDING,
                        YEAR_OF_PLENTY, parse_res, PIPS)
from .game import (DISCARD, MAIN, OVER, PRE_ROLL, ROAD_BUILD, ROBBER, SETUP_ROAD,
                   SETUP_SETTLE, GameState, IllegalAction, fmt_cards)
from .match import resolve_proposal
from .render import PLAYER_COLORS, PLAYER_COLOR_NAMES, Renderer, use_color_default, visible_len

AI_NAMES = ["Vera", "Magnus", "Kasimir", "Ingrid", "Tobias", "Selene"]

HELP = """
 ┌─ COMMANDS ───────────────────────────────────────────────────────────────┐
 │ r, roll                  roll the dice                                     │
 │ s [v], settle [v]        build a settlement at vertex v (no v: show ids)   │
 │ c [v], city [v]          upgrade settlement at v to a city                 │
 │ rd [e], road [e]         build a road on edge e (no e: show ids)           │
 │ d, dev, buy              buy a development card                            │
 │ k, knight                play a knight                                     │
 │ rb, roadbuilding         play road building                                │
 │ mono <res>               play monopoly, e.g.  mono ore                     │
 │ yop <res> <res>          play year of plenty, e.g.  yop wheat ore          │
 │ t <give> <get>           bank/port trade at your best ratio, e.g. t wood ore│
 │ offer <cards> for <cards>  offer a trade to the bots: offer 2 wood for ore │
 │ e, end                   end your turn                                     │
 ├─ COACHING ────────────────────────────────────────────────────────────────┤
 │ h, hint                  engine's top options for this decision            │
 │ eval                     engine evaluation of every player + production    │
 │ review on|off            post-move review of your decisions (default on)   │
 │ reason on|off            show the bots' reasoning (default on)             │
 │ undo                     take back your last decision (dice stay the same) │
 ├─ VIEW ────────────────────────────────────────────────────────────────────┤
 │ b, board                 redraw the board      ids   show all vertex ids   │
 │ log [n]                  recent events         cost  building costs        │
 │ stats                    dice histogram        q, quit                     │
 └───────────────────────────────────────────────────────────────────────────┘
 Resources: wood (w) brick (b) sheep (s) wheat (wh) ore (o).
 Cards: "2 wood ore", "w w o", "2w 1o".  Vertices are numbered, hexes lettered.
"""

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
        num = None
        i = 0
        while i < len(tok) and tok[i].isdigit():
            i += 1
        if i:
            num, tok = int(tok[:i]), tok[i:]
        else:
            j = len(tok)
            while j > 0 and tok[j - 1].isdigit():
                j -= 1
            if j < len(tok):
                num, tok = int(tok[j:]), tok[:j]
        r = parse_res(tok)
        if r is None:
            return None
        k = num if num is not None else (pending if pending is not None else 1)
        counts[r] += k
        pending = None
    return counts


class Game:
    def __init__(self, args):
        self.args = args
        self.color = use_color_default() and not args.no_color
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
        self.r = Renderer(self.s, seat, self.color)
        self.st = self.r.st
        self.printed = 0
        self.review_on = not args.no_review
        self.reason_on = not args.no_reasoning
        self.delay = 0.0 if args.fast else args.delay
        self.history = []        # undo snapshots
        self.reviews = []        # (turn, phase, my, best, delta, verdict)
        self.pending_review = None
        self.shown = None

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
                    self.out("")
                self.out(self.r.fmt_event(p, kind, msg))
        if self.pending_review:
            self.out(self.pending_review)
            self.pending_review = None

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

    def info(self, msg):
        self.out(self.st("  " + msg, fg=110))

    def warn(self, msg):
        self.out(self.st("  ✗ " + msg, fg=203))

    # --------------------------------------------------------------- run
    def run(self):
        st = self.st
        you = PLAYER_COLOR_NAMES[self.human]
        self.out(self.banner())
        self.out(f"  Seed {st(str(self.seed), bold=True)}  ·  replay this exact board and dice with "
                 f"{st(f'python3 -m hexfield --seed {self.seed} --seat {self.human + 1} --opponents {self.args.opponents}', fg=117)}")
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
        s.apply(a)
        self.flush_events()
        if self.reason_on and why and a[0] not in ("roll", "end"):
            self.reason(why)
        if self.delay:
            time.sleep(self.delay)

    def reason(self, why):
        self.out(self.st(f"     ↳ {why}", fg=245, italic=True))

    def ask_trade(self, q, proposer, give, get):
        """Bot offers the human a trade. give/get are from the proposer's side."""
        s = self.s
        while True:
            ans = self.ask(f"{s.names[proposer]} gives you {fmt_cards(give)} for your {fmt_cards(get)}. accept? [y/n/hint]").lower()
            if ans in ("h", "hint"):
                ok, gain = self.coach.evaluate_offer(s, q, proposer, give, get)
                self.info(f"engine: {'ACCEPT' if ok else 'DECLINE'} (net value for you {gain:+.2f}, "
                          f"includes what {s.names[proposer]} builds with it)")
                continue
            if ans in ("undo",):
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
    def verdict(self, delta):
        for thr, name, col, sym in VERDICTS:
            if delta < thr:
                return name, col, sym
        return VERDICTS[-1][1:]

    def report(self, mine, best, best_desc, label=None):
        delta = max(0.0, best - mine)
        name, col, sym = self.verdict(delta)
        st = self.st
        msg = st(f"  ◆ {sym} {name}", fg=col, bold=True)
        if delta >= 0.05:
            msg += st(f"  (−{delta:.2f} VP-eq)", fg=col) + st(f"  engine: {best_desc}", fg=250)
        self.pending_review = msg
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

    def review_action(self, a, best_cache=None):
        if not self.review_on or self.trivial(a):
            return
        s = self.s
        try:
            if best_cache is None:
                best_sc, best_a, best_why = self.coach.best_value(s)
            else:
                best_sc, best_a, best_why = best_cache
            if best_a is None:
                return
            same = a == best_a or (best_a[0] == "macro" and a == best_a[1][0])
            if same:
                mine = best_sc
            else:
                mine = self.coach.value_of_action(s, a)
            desc = describe(s, best_a) if best_a else ""
            if s.phase == MAIN and best_why.startswith("plan:"):
                desc = best_why[6:]
            elif best_why and s.phase in (SETUP_SETTLE, SETUP_ROAD, ROBBER):
                desc += f" - {best_why}"
            self.report(mine, best_sc, desc, describe(s, a))
        except Exception as ex:  # reviews must never crash the game
            self.out(self.st(f"  (review unavailable: {ex})", dim=True))

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
            self.show()

    def common(self, cmd, args):
        """Commands valid at every prompt. Returns True if handled."""
        if cmd in ("q", "quit", "exit"):
            if self.ask("really quit? [y/n]").lower().startswith("y"):
                raise Quit()
            return True
        if cmd in ("help", "?"):
            self.out(HELP)
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
        if cmd == "review" and args:
            self.review_on = args[0] == "on"
            self.info(f"post-move review {'on' if self.review_on else 'off'}")
            return True
        if cmd in ("reason", "reasoning") and args:
            self.reason_on = args[0] == "on"
            self.info(f"bot reasoning {'on' if self.reason_on else 'off'}")
            return True
        if cmd == "undo":
            raise Undo()
        return False

    def hint(self):
        s = self.s
        t0 = time.time()
        ranked = self.coach.analyze(s, top=5)
        if not ranked:
            self.info("no options to analyse")
            return
        best = ranked[0][0]
        self.out(self.st(f"  Engine options ({time.time() - t0:.1f}s):", bold=True))
        for i, (sc, a, why) in enumerate(ranked):
            d = best - sc
            tag = self.st("best", fg=46, bold=True) if i == 0 else self.st(f"−{d:.2f}", fg=220 if d < 0.5 else 208)
            text = why if s.phase == MAIN else f"{describe(s, a)} - {why}"
            self.out(f"   {i + 1}. {pad_vis(tag, 6)} {text}")

    def eval_cmd(self):
        s = self.s
        vals = self.coach.ev.values(self.coach.view(s, self.human), self.human)
        self.out(self.st("  Engine evaluation (VP-equivalents, your point of view):", bold=True))
        top = max(vals)
        for p in range(s.n):
            prod = self.coach.ev.production(s, p)
            bar_len = int(max(0, vals[p]) / max(top, 1) * 24)
            bar = self.st("█" * bar_len, fg=PLAYER_COLORS[p])
            pips = " ".join(f"{RES_NAMES[r][:2].title()}{int(prod[r]):>2}" for r in range(5))
            self.out(f"   {self.r.pname(p, 8)} {vals[p]:5.2f} {pad_vis(bar, 25)} pips/36: {pips}  (={int(sum(prod))})")
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

    # setup -------------------------------------------------------------
    def human_setup_settle(self):
        s = self.s
        legal = s.legal_settlements(self.human, setup=True)
        self.show(vertex_ids=legal)
        which = "first" if s.setup_idx < s.n else "second"
        while True:
            line = self.ask(f"place your {which} settlement (vertex id)")
            if not line:
                continue
            cmd, *args = line.split()
            if self.common(cmd.lower(), args):
                if cmd.lower() in ("b", "board", "map"):
                    self.show(vertex_ids=legal)
                continue
            if cmd.isdigit() and int(cmd) in legal:
                v = int(cmd)
                self.info(f"vertex {v}: {s.board.describe_vertex(v)}")
                a = ("settle", v)
                self.review_action(a)
                s.apply(a)
                return
            self.warn("pick one of the highlighted vertex ids")

    def human_setup_road(self):
        s = self.s
        legal = s.legal_setup_roads(self.human)
        self.show(edge_ids=legal)
        while True:
            line = self.ask("place a road next to it (edge id)")
            if not line:
                continue
            cmd, *args = line.split()
            if self.common(cmd.lower(), args):
                continue
            if cmd.isdigit() and int(cmd) in legal:
                a = ("road", int(cmd))
                self.review_action(a)
                s.apply(a)
                return
            self.warn("pick one of the highlighted edge ids")

    # discard / robber ----------------------------------------------------
    def human_discard(self):
        s = self.s
        k = s.discards[self.human]
        self.out("")
        self.out("  " + " ".join(self.r.chip(r, s.hands[self.human][r]) for r in range(5)))
        while True:
            line = self.ask(f"a 7! discard {k} cards (e.g. '2 wood ore')")
            if not line:
                continue
            cmd, *args = line.split()
            if self.common(cmd.lower(), args):
                continue
            counts = parse_cards(line.split())
            if counts is None:
                self.warn("couldn't parse that - try e.g. 'wood 2 ore'")
                continue
            if sum(counts) != k:
                self.warn(f"that's {sum(counts)} cards, need exactly {k}")
                continue
            if any(counts[r] > s.hands[self.human][r] for r in range(5)):
                self.warn("you don't have those cards")
                continue
            if self.review_on:
                (best_v, best_c), (mine_v, _) = self.coach.rank_discards(s, self.human, counts)
                self.report(mine_v, best_v, "discard " + fmt_cards(best_c), "discard " + fmt_cards(counts))
            s.apply(("discard", tuple(counts)))
            return

    def human_robber(self):
        s = self.s
        self.show()
        while True:
            line = self.ask("move the robber: hex letter [victim name]")
            if not line:
                continue
            cmd, *args = line.split()
            if self.common(cmd.lower(), args):
                continue
            h = s.board.hex_from_label(cmd)
            if h is None or h == s.robber:
                self.warn("give a hex letter A-S (not the robber's current hex)")
                continue
            vics = s.robber_targets(self.human, h)
            victim = -1
            if vics:
                if len(vics) == 1:
                    victim = vics[0]
                else:
                    name = args[0] if args else None
                    while victim == -1:
                        if name is None:
                            opts = ", ".join(f"{s.names[v]} ({s.hand_size(v)} cards)" for v in vics)
                            name = self.ask(f"steal from whom? {opts}")
                        for v in vics:
                            if s.names[v].lower().startswith(name.lower()):
                                victim = v
                        if victim == -1:
                            self.warn("not a valid victim")
                            name = None
            a = ("robber", h, victim)
            self.review_action(a)
            s.apply(a)
            return

    def human_free_road(self):
        s = self.s
        legal = s.legal_roads(self.human)
        self.show(edge_ids=legal)
        while True:
            line = self.ask(f"road building: place a free road ({s.free_roads} left)")
            if not line:
                continue
            cmd, *args = line.split()
            if self.common(cmd.lower(), args):
                continue
            if cmd.isdigit() and int(cmd) in legal:
                a = ("road", int(cmd))
                self.review_action(a)
                s.apply(a)
                return
            self.warn("pick one of the highlighted edge ids")

    # main turn -----------------------------------------------------------
    def human_main(self):
        s = self.s
        if s.phase == PRE_ROLL:
            can_play = any(s.playable(self.human, c) for c in (KNIGHT, ROAD_BUILDING, MONOPOLY, YEAR_OF_PLENTY))
            if not can_play and self.args.autoroll:
                s.apply(("roll",))
                self.flush_events()
                return
            self.show()
            self.shown = (s.turn, s.current)
        elif self.shown != (s.turn, s.current):
            self.show()
            self.shown = (s.turn, s.current)
        while True:
            prompt = "roll (r) or play a knight (k)" if s.phase == PRE_ROLL else "your move"
            line = self.ask(prompt)
            if not line:
                continue
            parts = line.split()
            cmd, args = parts[0].lower(), parts[1:]
            if self.common(cmd, args):
                continue
            try:
                a = self.parse_main(cmd, args)
            except IllegalAction as ex:
                self.warn(str(ex))
                continue
            if a is None:
                continue
            if a == "redo":
                return
            if a[0] == "offer":
                self.human_offer(a[1], a[2])
                return
            # validate before reviewing
            try:
                t = s.clone(sim=False)
                t.apply(a)
            except IllegalAction as ex:
                self.warn(str(ex))
                continue
            self.review_action(a)
            s.apply(a)
            if a[0] in ("settle", "city", "road", "play_knight", "play_road") and s.phase == MAIN:
                self.flush_events()
                self.show()
            return

    def parse_main(self, cmd, args):
        s = self.s
        p = self.human
        if cmd in ("r", "roll"):
            return ("roll",)
        if cmd in ("e", "end", "done", "pass"):
            return ("end",)
        if cmd in ("s", "settle", "settlement"):
            if s.phase != MAIN:
                raise IllegalAction("roll first")
            legal = s.legal_settlements(p)
            if not args:
                if not legal:
                    raise IllegalAction("no legal settlement spots (need a road leading to an open spot)")
                self.show(vertex_ids=legal)
                v = self.ask("settlement at (vertex id, blank to cancel)")
                if not v.isdigit():
                    return None
                args = [v]
            return ("settle", int(args[0])) if args[0].isdigit() else None
        if cmd in ("c", "city"):
            if s.phase != MAIN:
                raise IllegalAction("roll first")
            legal = s.legal_cities(p)
            if not args:
                if not legal:
                    raise IllegalAction("no settlements to upgrade")
                if len(legal) == 1:
                    return ("city", legal[0])
                self.show(vertex_ids=legal)
                v = self.ask("city at (vertex id, blank to cancel)")
                if not v.isdigit():
                    return None
                args = [v]
            return ("city", int(args[0])) if args[0].isdigit() else None
        if cmd in ("rd", "road"):
            if s.phase != MAIN:
                raise IllegalAction("roll first")
            legal = s.legal_roads(p)
            if not args:
                if not legal:
                    raise IllegalAction("nowhere to build a road")
                self.show(edge_ids=legal)
                e = self.ask("road at (edge id, blank to cancel)")
                if not e.isdigit():
                    return None
                args = [e]
            return ("road", int(args[0])) if args[0].isdigit() else None
        if cmd in ("d", "dev", "buy"):
            return ("buy_dev",)
        if cmd in ("k", "knight"):
            return ("play_knight",)
        if cmd in ("rb", "roadbuilding", "road_building"):
            return ("play_road",)
        if cmd in ("play",) and args:
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
            if len(args) < 2:
                raise IllegalAction("usage: t <give> <get>   e.g. t wood ore")
            nums = [x for x in args if x.isdigit()]
            words = [x for x in args if not x.isdigit()]
            if len(words) < 2:
                raise IllegalAction("usage: t <give> <get>")
            give, get = parse_res(words[0]), parse_res(words[-1])
            if give is None or get is None:
                raise IllegalAction("unknown resource")
            times = 1
            if len(nums) >= 2:
                times = int(nums[-1])
            acts = []
            for _ in range(times):
                acts.append(("bank", give, get))
            if times > 1:
                for a in acts[:-1]:
                    t = s.clone(sim=False)
                    t.apply(a)
                    self.review_action(a)
                    s.apply(a)
                    self.flush_events()
            return acts[-1]
        if cmd == "offer":
            text = " ".join(args).lower()
            if " for " not in f" {text} ":
                raise IllegalAction("usage: offer <cards> for <cards>   e.g. offer 2 wood for ore")
            left, right = text.split(" for ", 1) if " for " in text else ("", text[4:])
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
        if not accepters:
            self.history.pop()
            return
        if len(accepters) == 1:
            q = accepters[0]
        else:
            names = "/".join(s.names[q] for q in accepters)
            q = None
            while q is None:
                ans = self.ask(f"trade with whom? {names} (blank = nobody)")
                if not ans:
                    self.history.pop()
                    return
                for c in accepters:
                    if s.names[c].lower().startswith(ans.lower()):
                        q = c
        a = ("trade", q, give, get)
        self.review_action(a)
        s.apply(a)

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
            parts = [f"{len(s.settlements[p])} settl", f"{len(s.cities[p])} cities"]
            if s.longest == p:
                parts.append("longest road")
            if s.army == p:
                parts.append("largest army")
            if s.vp_cards(p):
                parts.append(f"{s.vp_cards(p)} VP cards")
            self.out(f"   {self.r.pname(p, 9)} {s.vp(p):>2} VP  ({', '.join(parts)})")
        if self.reviews:
            self.out("")
            self.out(st("  Your decision review", bold=True))
            counts = {}
            for *_, name in self.reviews:
                counts[name] = counts.get(name, 0) + 1
            line = "   "
            for thr, name, col, sym in VERDICTS:
                line += st(f"{name} {counts.get(name, 0)}   ", fg=col, bold=True)
            self.out(line)
            loss = sum(r[4] for r in self.reviews)
            acc = 100.0 * sum(1 for r in self.reviews if r[4] < 0.2) / len(self.reviews)
            self.out(f"   accuracy {acc:.0f}% of {len(self.reviews)} decisions  ·  total value lost {loss:.2f} VP-eq")
            worst = sorted(self.reviews, key=lambda r: -r[4])[:5]
            worst = [r for r in worst if r[4] >= 0.2]
            if worst:
                self.out(st("   Biggest misses:", bold=True))
                for turn, ph, mine, best, delta, name in worst:
                    self.out(f"    turn {turn:>3} {ph.replace('_', ' '):<16} you: {mine}")
                    self.out(st(f"             −{delta:.2f}  engine: {best}", dim=True))
        self.out("")
        self.out(f"  Replay: python3 -m hexfield --seed {self.seed} --seat {self.human + 1} --opponents {self.args.opponents}")


def pad_vis(text, width):
    return text + " " * max(0, width - visible_len(text))


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

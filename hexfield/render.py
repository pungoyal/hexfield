"""ANSI terminal rendering: coloured hex board, player panel, event log."""

import math
import os
import re
import shutil
from itertools import zip_longest

from .constants import (DEV_NAMES, KNIGHT, MONOPOLY, PIPS, RES_NAMES, ROAD_BUILDING,
                        VP_CARD, YEAR_OF_PLENTY, MAX_ROADS, MAX_SETTLEMENTS, MAX_CITIES)

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# 256-colour palette
TERRAIN_BG = (28, 130, 107, 178, 245, 180)          # wood brick sheep wheat ore desert
TERRAIN_FG = (157, 223, 230, 230, 255, 94)
TERRAIN_TXT = ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE", "DESERT")
RES_CHIP_BG = TERRAIN_BG[:5]
RES_CHIP_FG = (231, 231, 231, 16, 16)
OCEAN_BG = 24
OCEAN_FG = 31
GROUT_BG = 237
GROUT_FG = 243
PLAYER_COLORS = (196, 39, 208, 255)
PLAYER_COLOR_NAMES = ("Red", "Blue", "Orange", "White")
TOKEN_BG = 230
DICE = "⚀⚁⚂⚃⚄⚅"
RES_ABBR = [name[:2].title() for name in RES_NAMES]     # Wo Br Sh Wh Or
EVENT_ICONS = {"build": "⌂", "roll": "⚄", "produce": "+", "robber": "☗", "steal": "✋",
               "dev": "★", "trade": "⇄", "award": "♛", "discard": "−", "win": "♛"}


class Style:
    def __init__(self, color=True):
        self.color = color

    def __call__(self, text, fg=None, bg=None, bold=False, dim=False, italic=False):
        if not self.color:
            return text
        codes = [code for code, on in (("1", bold), ("2", dim), ("3", italic)) if on]
        if fg is not None:
            codes.append(f"38;5;{fg}")
        if bg is not None:
            codes.append(f"48;5;{bg}")
        if not codes:
            return text
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def visible_len(s):
    return len(ANSI_RE.sub("", s))


def pad(s, width):
    return s + " " * max(0, width - visible_len(s))


def use_color_default():
    return not os.environ.get("NO_COLOR")


class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.fg = [[None] * w for _ in range(h)]
        self.bg = [[None] * w for _ in range(h)]
        self.bold = [[False] * w for _ in range(h)]

    def put(self, x, y, c, fg=None, bg=None, bold=False):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.ch[y][x] = c
            self.fg[y][x] = fg
            self.bg[y][x] = bg
            self.bold[y][x] = bold

    def text(self, x, y, s, fg=None, bg=None, bold=False):
        for i, c in enumerate(s):
            self.put(x + i, y, c, fg, bg, bold)

    def ctext(self, cx, y, s, **kw):
        self.text(cx - len(s) // 2, y, s, **kw)

    def lines(self, color=True):
        out = []
        for y in range(self.h):
            if not color:
                out.append("".join(self.ch[y]))
                continue
            parts = []
            cur = None
            for x in range(self.w):
                st = (self.fg[y][x], self.bg[y][x], self.bold[y][x])
                if st != cur:
                    codes = ["0"]
                    if st[2]:
                        codes.append("1")
                    if st[0] is not None:
                        codes.append(f"38;5;{st[0]}")
                    if st[1] is not None:
                        codes.append(f"48;5;{st[1]}")
                    parts.append(f"\x1b[{';'.join(codes)}m")
                    cur = st
                parts.append(self.ch[y][x])
            parts.append("\x1b[0m")
            out.append("".join(parts))
        return out


class Renderer:
    def __init__(self, state, human=0, color=True):
        self.s = state
        self.b = state.board
        self.human = human
        self.color = color
        self.st = Style(color)

    # ----------------------------------------------------------------- board
    def board_lines(self, vertex_ids=None, edge_ids=None, highlight=None):
        s, b = self.s, self.b
        cv = Canvas(b.width, b.height)
        # ocean with sparse waves
        for y in range(b.height):
            for x in range(b.width):
                wave = "~" if self.color and (x * 7 + y * 13) % 23 == 0 else " "
                cv.put(x, y, wave, OCEAN_FG, OCEAN_BG)
        # tiles
        for h, (cx, cy) in enumerate(b.hex_xy):
            r = b.hex_resource[h]
            bg = TERRAIN_BG[r]
            for dy in range(-3, 4):
                span = 2 if abs(dy) == 3 else 5
                for dx in range(-span, span + 1):
                    cv.put(cx + dx, cy + dy, " ", None, bg)
            fg = TERRAIN_FG[r]
            cv.ctext(cx, cy - 2, b.hex_label(h), fg=fg, bg=bg, bold=True)
            cv.ctext(cx, cy - 1, TERRAIN_TXT[r], fg=fg, bg=bg)
            n = b.hex_number[h]
            if n:
                red = n in (6, 8)
                cv.text(cx - 1, cy, f"{n:^3}", fg=160 if red else 16, bg=TOKEN_BG, bold=True)
                pips = "•" * PIPS[n]
                cv.ctext(cx, cy + 1, pips, fg=160 if red else fg, bg=bg, bold=red)
            if h == s.robber:
                cv.ctext(cx, cy + 2, " ROBBER ", fg=231, bg=16, bold=True)
            if highlight and h in highlight:
                cv.ctext(cx, cy - 3, "▼▼▼", fg=226, bg=bg, bold=True)
        # edges
        for e, (a, c) in enumerate(b.edge_vertices):
            (x1, y1), (x2, y2) = b.vertex_xy[a], b.vertex_xy[c]
            owner = s.e_owner[e]
            for x, y, ch in self._edge_cells(x1, y1, x2, y2):
                if owner == -1:
                    cv.put(x, y, ch, GROUT_FG, GROUT_BG)
                elif self.color:
                    cv.put(x, y, "┃" if ch == "│" else ch, 16, PLAYER_COLORS[owner], bold=True)
                else:
                    cv.put(x, y, str(owner + 1), 16, PLAYER_COLORS[owner], bold=True)
        # ports
        cxm, cym = b.center
        for e, kind in b.ports:
            a, c = b.edge_vertices[e]
            (x1, y1), (x2, y2) = b.vertex_xy[a], b.vertex_xy[c]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            dx, dy = mx - cxm, (my - cym) * 2
            d = math.hypot(dx, dy) or 1
            ux, uy = dx / d, dy / d
            if kind == "any":
                label, fg, bg = " 3:1 ", 231, 17
            else:
                label = f" {RES_ABBR[kind].upper()} 2:1 "
                fg, bg = RES_CHIP_FG[kind], RES_CHIP_BG[kind]
            lx = int(round(mx + ux * 7))
            ly = int(round(my + uy * 2.6))
            cv.ctext(lx, ly, label, fg=fg, bg=bg, bold=True)
            # little dock marks next to the two port corners
            for vx, vy in ((x1, y1), (x2, y2)):
                px = int(round(vx + ux * 2))
                py = int(round(vy + uy * 1))
                if cv.bg[py][px] == OCEAN_BG:
                    cv.put(px, py, "◦", 231, OCEAN_BG, bold=True)
        # vertices / buildings
        for v, (x, y) in enumerate(b.vertex_xy):
            o = s.v_owner[v]
            if o == -1:
                cv.put(x, y, "·", GROUT_FG, GROUT_BG)
            else:
                pc = PLAYER_COLORS[o]
                if self.color:
                    mark = " ▲ " if s.v_level[v] == 1 else "▲█▲"
                else:
                    mark = f"[{o + 1}]" if s.v_level[v] == 1 else f"<{o + 1}>"
                cv.text(x - 1, y, mark, fg=16, bg=pc, bold=True)
        # selection overlays
        if edge_ids:
            for e in edge_ids:
                x, y = self._edge_label_xy(e)
                cv.text(x - 1, y, f"{e:^3}", fg=16, bg=226, bold=True)
        if vertex_ids:
            for v in vertex_ids:
                x, y = b.vertex_xy[v]
                cv.text(x - 1, y, f"{v:^3}", fg=16, bg=51, bold=True)
        return cv.lines(self.color)

    def _edge_cells(self, x1, y1, x2, y2):
        if x1 == x2:
            return [(x1, y, "│") for y in range(min(y1, y2) + 1, max(y1, y2))]
        mx, my = (x1 + x2) // 2, (y1 + y2) // 2
        return [(mx, my, "╲" if (x2 - x1) * (y2 - y1) > 0 else "╱")]

    def _edge_label_xy(self, e):
        a, c = self.b.edge_vertices[e]
        (x1, y1), (x2, y2) = self.b.vertex_xy[a], self.b.vertex_xy[c]
        return (x1 + x2) // 2, (y1 + y2) // 2

    # ------------------------------------------------------------------ panel
    def chip(self, r, n):
        if not self.color:
            return f"{RES_ABBR[r]}:{n}"
        txt = f" {RES_ABBR[r]} {n} "
        if n == 0:
            return self.st(txt, fg=240, bg=235)
        return self.st(txt, fg=RES_CHIP_FG[r], bg=RES_CHIP_BG[r], bold=True)

    def pname(self, p, width=None):
        name = self.s.names[p]
        if width:
            name = name[:width].ljust(width)
        return self.st(name, fg=PLAYER_COLORS[p], bold=True)

    def swatch(self, p):
        return self.st("██", fg=PLAYER_COLORS[p]) if self.color else f"P{p + 1} "

    def dice(self):
        lr = self.s.last_roll
        if not lr:
            return self.st("no roll yet", dim=True)
        d1, d2, t = lr
        if d1 is None:
            return f"{t}"
        col = 160 if t == 7 else 231
        return self.st(f"{DICE[d1 - 1]} {DICE[d2 - 1]}", fg=231, bold=True) + "  = " + \
            self.st(f"{t:>2}", fg=col, bold=True)

    def panel_lines(self, width=46, log_lines=10):
        s, st = self.s, self.st
        L = []
        cur = s.actor() if s.phase != "game_over" else s.current
        title = f" Turn {s.turn} · {s.names[cur]} · {s.phase.replace('_', ' ')} "
        L.append(st("╭" + "─" * (width - 2) + "╮", fg=240))
        L.append(st("│", fg=240) + pad(st(title, bold=True), width - 2) + st("│", fg=240))
        L.append(st("│", fg=240) + pad(" Dice " + self.dice(), width - 2) + st("│", fg=240))
        L.append(st("╰" + "─" * (width - 2) + "╯", fg=240))
        hdr = f"   {'player':<9}{'VP':>3} {'cards':>5} {'dev':>3} {'kn':>3} {'road':>4}  awards"
        L.append(st(hdr, dim=True))
        for p in range(s.n):
            vp = s.public_vp(p) + (s.vp_cards(p) if p == self.human else 0)
            awards = []
            if s.longest == p:
                awards.append(st("LONGEST", fg=226, bold=True))
            if s.army == p:
                awards.append(st("ARMY", fg=213, bold=True))
            marker = st("▶", fg=226, bold=True) if p == cur else " "
            row = (f"{marker} {self.swatch(p)}{self.pname(p, 8)}"
                   f"{st(f'{vp:>3}', bold=True)} {s.hand_size(p):>5} {s.dev_count(p):>3} "
                   f"{s.knights[p]:>3} {s.road_len[p]:>4}  {' '.join(awards)}")
            L.append(row)
            if p == self.human:
                L.append("      " + " ".join(self.chip(r, s.hands[p][r]) for r in range(5)))
        L.append("")
        # human detail
        h = self.human
        dv, nd = s.devs[h], s.new_devs[h]
        cards = []
        for c in (KNIGHT, ROAD_BUILDING, MONOPOLY, YEAR_OF_PLENTY, VP_CARD):
            if dv[c] + nd[c]:
                tag = f"{DEV_NAMES[c]} x{dv[c] + nd[c]}"
                if nd[c]:
                    tag += st(f" ({nd[c]} new)", dim=True)
                cards.append(tag)
        L.append(st(" Your dev cards: ", bold=True) + (", ".join(cards) if cards else st("none", dim=True)))
        hidden_vp = s.vp_cards(h)
        L.append(f" Pieces left: {MAX_SETTLEMENTS - len(s.settlements[h])} settl, "
                 f"{MAX_CITIES - len(s.cities[h])} city, {MAX_ROADS - len(s.roads[h])} road"
                 + (st(f"  (+{hidden_vp} hidden VP)", fg=213) if hidden_vp else ""))
        rat = s.ratios(h)
        L.append(" Trade ratios: " + " ".join(f"{RES_ABBR[r]} {rat[r]}:1" for r in range(5)))
        bank = " ".join(f"{RES_ABBR[r]}{s.bank[r]}" for r in range(5))
        L.append(st(f" Bank {bank}  ·  dev deck {s.deck_size()}", dim=True))
        L.append(st(" " + "─" * (width - 2), fg=240))
        return L + self.recent_log(log_lines)

    def recent_log(self, k):
        evs = [e for e in self.s.events if self.visible_event(e)]
        return [self.fmt_event(p, kind, msg) for _, p, kind, msg in evs[-k:]]

    def visible_event(self, e):
        turn, p, kind, msg = e
        if kind == "private":
            return p == self.human
        if kind == "private_steal":
            return self.human in p
        return True

    def fmt_event(self, p, kind, msg):
        st = self.st
        msg = second_person(msg, self.s.names[self.human])
        if kind in ("private", "private_steal"):
            return st("  " + msg, fg=213, italic=True)
        col = PLAYER_COLORS[p] if isinstance(p, int) and p >= 0 else 250
        icon = EVENT_ICONS.get(kind, "·")
        if kind == "turn":
            return st(f" {msg}", fg=col, bold=True)
        if kind == "win":
            return st(f" {icon} {msg}", fg=226, bold=True)
        return " " + st(icon, fg=col, bold=True) + " " + msg

    # ---------------------------------------------------------------- layout
    def screen(self, vertex_ids=None, edge_ids=None, highlight=None):
        board = self.board_lines(vertex_ids, edge_ids, highlight=highlight)
        term_w = shutil.get_terminal_size((140, 50)).columns
        panel_w = 52
        panel = self.panel_lines(width=panel_w, log_lines=max(6, len(board) - 26))
        bw = self.b.width
        if term_w >= bw + panel_w + 2:
            board += [" " * bw] * (len(panel) - len(board))
            return "\n".join(left + "  " + right for left, right in
                             zip_longest(board, panel, fillvalue=""))
        return "\n".join(board + [""] + panel)


_VERBS = {"builds": "build", "rolls": "roll", "receives": "receive", "collects": "collect",
          "trades": "trade", "steals": "steal", "plays": "play", "buys": "buy", "moves": "move",
          "discards": "discard", "takes": "take", "loses": "lose", "upgrades": "upgrade",
          "offers": "offer", "WINS": "WIN"}


def second_person(msg, human_name):
    """'You builds' -> 'You build' when the human kept the default name."""
    if human_name != "You":
        return msg
    msg = msg.replace("You's turn", "Your turn").replace("You starts", "You start")
    words = msg.split(" ")
    for i in range(len(words) - 1):
        if words[i] in ("You", "(You") and words[i + 1] in _VERBS:
            words[i + 1] = _VERBS[words[i + 1]]
    return " ".join(words)

"""Board geometry and generation.

Hexes are pointy-top, laid out in rows of 3-4-5-4-3.  Every hex centre gets an
integer *screen* coordinate (column, row) on the character grid used by the
renderer; corners and edges are derived from those coordinates, so vertex
identity is simply "same screen cell".  That keeps geometry and rendering in
perfect agreement.
"""

import math
import random

from .constants import DESERT, NUMBER_BAG, PIPS, PORT_BAG, RES_NAMES, TILE_BAG

# character-grid geometry
HEX_DX = 12          # horizontal distance between hex centres in a row
HEX_DY = 6           # vertical distance between rows
MARGIN_X = 14
MARGIN_Y = 6
CORNER_OFFSETS = ((0, -4), (6, -2), (6, 2), (0, 4), (-6, 2), (-6, -2))  # N NE SE S SW NW
AXIAL_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1))


def axial_coords():
    return [(q, r) for r in range(-2, 3) for q in range(-2, 3) if abs(q + r) <= 2]


# Hex names skip every single-key command, so typing a hex letter can never be
# read as something else: B/H/Q in the terminal (board, hint, quit) and
# D/E/H/R/T in the browser (dev card, end turn, hint, roll, trade).
HEX_LETTERS = "ACFGIJKLMNOPSUVWXYZ"


class Board:
    """Immutable (after generation) topology + tile layout."""

    def __init__(self, seed=None):
        rng = random.Random(seed)
        self.coords = axial_coords()
        self.H = len(self.coords)
        self.hex_xy = []
        for q, r in self.coords:
            cx = MARGIN_X + 24 + HEX_DX * q + 6 * r
            cy = MARGIN_Y + 12 + HEX_DY * r
            self.hex_xy.append((cx, cy))
        self.width = MARGIN_X * 2 + 49
        self.height = MARGIN_Y * 2 + 25

        # ---- vertices
        raw = set()
        for cx, cy in self.hex_xy:
            for dx, dy in CORNER_OFFSETS:
                raw.add((cx + dx, cy + dy))
        self.vertex_xy = sorted(raw, key=lambda p: (p[1], p[0]))
        vid = {p: i for i, p in enumerate(self.vertex_xy)}
        self.V = len(self.vertex_xy)
        self.hex_vertices = []
        self.vertex_hexes = [[] for _ in range(self.V)]
        for h, (cx, cy) in enumerate(self.hex_xy):
            vs = [vid[(cx + dx, cy + dy)] for dx, dy in CORNER_OFFSETS]
            self.hex_vertices.append(vs)
            for v in vs:
                self.vertex_hexes[v].append(h)

        # ---- edges
        epairs = set()
        for vs in self.hex_vertices:
            for i in range(6):
                a, b = vs[i], vs[(i + 1) % 6]
                epairs.add((min(a, b), max(a, b)))

        def emid(pr):
            (x1, y1), (x2, y2) = self.vertex_xy[pr[0]], self.vertex_xy[pr[1]]
            return ((x1 + x2) / 2, (y1 + y2) / 2)

        self.edge_vertices = sorted(epairs, key=lambda pr: (emid(pr)[1], emid(pr)[0]))
        self.E = len(self.edge_vertices)
        self.edge_id = {pr: i for i, pr in enumerate(self.edge_vertices)}
        self.vertex_edges = [[] for _ in range(self.V)]
        self.vertex_neighbors = [[] for _ in range(self.V)]
        for e, (a, b) in enumerate(self.edge_vertices):
            self.vertex_edges[a].append(e)
            self.vertex_edges[b].append(e)
            self.vertex_neighbors[a].append(b)
            self.vertex_neighbors[b].append(a)
        self.edge_hexes = [[h for h in self.vertex_hexes[a] if h in self.vertex_hexes[b]]
                           for a, b in self.edge_vertices]

        # hex adjacency
        index = {c: i for i, c in enumerate(self.coords)}
        self.hex_neighbors = []
        for q, r in self.coords:
            self.hex_neighbors.append([index[(q + dq, r + dr)] for dq, dr in AXIAL_NEIGHBORS
                                       if (q + dq, r + dr) in index])

        self._generate_tiles(rng)
        self._generate_ports(rng)
        self._precompute()

    # ------------------------------------------------------------------ tiles
    def _generate_tiles(self, rng):
        for _ in range(10000):
            tiles = TILE_BAG[:]
            rng.shuffle(tiles)
            nums = NUMBER_BAG[:]
            rng.shuffle(nums)
            it = iter(nums)
            numbers = [0 if t == DESERT else next(it) for t in tiles]
            if self._fair_numbers(numbers):
                break
        self.hex_resource = tiles
        self.hex_number = numbers
        self.desert = tiles.index(DESERT)

    def _fair_numbers(self, numbers):
        """No 6/8 touching another 6/8, and no identical numbers touching."""
        for h, nbrs in enumerate(self.hex_neighbors):
            for n in nbrs:
                if numbers[h] in (6, 8) and numbers[n] in (6, 8):
                    return False
                if numbers[h] and numbers[h] == numbers[n]:
                    return False
        return True

    # ------------------------------------------------------------------ ports
    def _generate_ports(self, rng):
        coastal = [e for e in range(self.E) if len(self.edge_hexes[e]) == 1]
        cx = sum(x for x, _ in self.hex_xy) / self.H
        cy = sum(y for _, y in self.hex_xy) / self.H
        self.center = (cx, cy)

        def ang(e):
            a, b = self.edge_vertices[e]
            (x1, y1), (x2, y2) = self.vertex_xy[a], self.vertex_xy[b]
            return math.atan2(((y1 + y2) / 2 - cy) * 2, (x1 + x2) / 2 - cx)

        coastal.sort(key=ang)
        start = rng.randrange(len(coastal))
        gaps = [3, 3, 4] * 3
        rng.shuffle(gaps)
        kinds = PORT_BAG[:]
        rng.shuffle(kinds)
        self.ports = []  # (edge, kind) kind = 'any' or resource int
        idx = start
        for i, g in enumerate(gaps):
            self.ports.append((coastal[idx % len(coastal)], kinds[i]))
            idx += g
        self.vertex_port = [None] * self.V
        for e, kind in self.ports:
            for v in self.edge_vertices[e]:
                self.vertex_port[v] = kind

    # ------------------------------------------------------------- analytics
    def _precompute(self):
        self.hex_pips = [PIPS[n] for n in self.hex_number]  # the desert has 0
        self.vertex_total_pips = [sum(self.hex_pips[h] for h in hs) for hs in self.vertex_hexes]
        tot = [0] * 5
        for h in range(self.H):
            if self.hex_resource[h] != DESERT:
                tot[self.hex_resource[h]] += self.hex_pips[h]
        avg = sum(tot) / 5.0
        # scarcer resources are worth more; clamp so it stays a gentle nudge
        self.scarcity = [max(0.8, min(1.35, (avg / t) ** 0.5 if t else 1.35)) for t in tot]

    def hex_label(self, h):
        return HEX_LETTERS[h]

    def hex_from_label(self, s):
        s = s.strip().upper()
        i = HEX_LETTERS.find(s) if len(s) == 1 else -1
        return i if 0 <= i < self.H else None

    def describe_vertex(self, v):
        parts = []
        for h in self.vertex_hexes[v]:
            r = self.hex_resource[h]
            if r == DESERT:
                parts.append("desert")
            else:
                parts.append(f"{self.hex_number[h]} {RES_NAMES[r]}")
        port = self.vertex_port[v]
        if port is not None:
            parts.append("3:1 port" if port == "any" else f"2:1 {RES_NAMES[port]} port")
        return ", ".join(parts)


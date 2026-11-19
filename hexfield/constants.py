"""Static rules data: costs, tile, number and dock bags, and the fixed beginner map."""

TIMBER, CLAY, WOOL, GRAIN, STONE = range(5)
BADLANDS = 5
RESOURCES = (TIMBER, CLAY, WOOL, GRAIN, STONE)
RES_NAMES = ("timber", "clay", "wool", "grain", "stone")

COSTS = {
    "trail": (1, 1, 0, 0, 0),
    "village": (1, 1, 1, 1, 0),
    "town": (0, 0, 0, 2, 3),
    "venture": (0, 0, 1, 1, 1),
}

RANGER, LANDMARK, PATHFINDER, HOARD, WINDFALL = range(5)
UNKNOWN_VENTURE = 5  # only used inside AI simulations (card bought but not yet seen)
VENTURE_NAMES = ("ranger", "landmark", "pathfinder", "hoard", "windfall", "unknown")
VENTURE_DECK_COUNTS = (14, 5, 2, 2, 2)

PIPS = {0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

MAX_VILLAGES = 5
MAX_TOWNS = 4
MAX_TRAILS = 15
BANK_START = 19
WIN_FAME = 10

TILE_BAG = [TIMBER] * 4 + [WOOL] * 4 + [GRAIN] * 4 + [CLAY] * 3 + [STONE] * 3 + [BADLANDS]
NUMBER_BAG = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
DOCK_BAG = ["any"] * 4 + list(RESOURCES)

# 5-6 player extension: a 30-hex board (rows of 3-4-5-6-5-4-3), 11 docks, a bigger
# bank and venture deck. Pieces per player and the 10 fame target stay the same.
LARGE_TILE_BAG = [TIMBER] * 6 + [WOOL] * 6 + [GRAIN] * 6 + [CLAY] * 5 + [STONE] * 5 + [BADLANDS] * 2
LARGE_NUMBER_BAG = [2, 2, 12, 12] + [n for n in (3, 4, 5, 6, 8, 9, 10, 11) for _ in range(3)]
LARGE_DOCK_BAG = ["any"] * 5 + list(RESOURCES) + [WOOL]
LARGE_BANK_START = 24
LARGE_VENTURE_DECK_COUNTS = (20, 5, 3, 3, 3)
LARGE_FROM = 5  # this many players or more play on the large board

# The fixed beginner board, row by row (3-4-5-4-3), as (terrain, number).
BEGINNER_TILES = [
    (STONE, 10), (WOOL, 2), (TIMBER, 9),
    (GRAIN, 12), (CLAY, 6), (WOOL, 4), (CLAY, 10),
    (GRAIN, 9), (TIMBER, 11), (BADLANDS, 0), (TIMBER, 3), (STONE, 8),
    (TIMBER, 8), (STONE, 3), (GRAIN, 4), (WOOL, 5),
    (CLAY, 5), (GRAIN, 6), (WOOL, 11),
]
# its docks clockwise from the north-west corner
BEGINNER_DOCKS = ["any", GRAIN, STONE, "any", WOOL, "any", "any", CLAY, TIMBER]

RES_ALIASES = {
    "t": TIMBER, "ti": TIMBER, "timber": TIMBER,
    "c": CLAY, "cl": CLAY, "clay": CLAY,
    "w": WOOL, "wo": WOOL, "wool": WOOL,
    "g": GRAIN, "gr": GRAIN, "grain": GRAIN,
    "s": STONE, "st": STONE, "stone": STONE,
}


def parse_res(tok):
    tok = tok.lower().strip(",.")
    if tok in RES_ALIASES:
        return RES_ALIASES[tok]
    for name, r in RES_ALIASES.items():
        if len(tok) >= 3 and name.startswith(tok):
            return r
    return None

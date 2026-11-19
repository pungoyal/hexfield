"""Static rules data: costs, tile, number and port bags, and the fixed beginner map."""

WOOD, BRICK, SHEEP, WHEAT, ORE = range(5)
DESERT = 5
RESOURCES = (WOOD, BRICK, SHEEP, WHEAT, ORE)
RES_NAMES = ("wood", "brick", "sheep", "wheat", "ore")

COSTS = {
    "road": (1, 1, 0, 0, 0),
    "settlement": (1, 1, 1, 1, 0),
    "city": (0, 0, 0, 2, 3),
    "dev": (0, 0, 1, 1, 1),
}

KNIGHT, VP_CARD, ROAD_BUILDING, MONOPOLY, YEAR_OF_PLENTY = range(5)
UNKNOWN_DEV = 5  # only used inside AI simulations (card bought but not yet seen)
DEV_NAMES = ("knight", "victory point", "road building", "monopoly", "year of plenty", "unknown")
DEV_DECK_COUNTS = (14, 5, 2, 2, 2)

PIPS = {0: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

MAX_SETTLEMENTS = 5
MAX_CITIES = 4
MAX_ROADS = 15
BANK_START = 19
WIN_VP = 10

TILE_BAG = [WOOD] * 4 + [SHEEP] * 4 + [WHEAT] * 4 + [BRICK] * 3 + [ORE] * 3 + [DESERT]
NUMBER_BAG = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
PORT_BAG = ["any"] * 4 + list(RESOURCES)

# 5-6 player extension: a 30-hex board (rows of 3-4-5-6-5-4-3), 11 harbours, a bigger
# bank and development deck. Pieces per player and the 10 VP target stay the same.
LARGE_TILE_BAG = [WOOD] * 6 + [SHEEP] * 6 + [WHEAT] * 6 + [BRICK] * 5 + [ORE] * 5 + [DESERT] * 2
LARGE_NUMBER_BAG = [2, 2, 12, 12] + [n for n in (3, 4, 5, 6, 8, 9, 10, 11) for _ in range(3)]
LARGE_PORT_BAG = ["any"] * 5 + list(RESOURCES) + [SHEEP]
LARGE_BANK_START = 24
LARGE_DEV_DECK_COUNTS = (20, 5, 3, 3, 3)
LARGE_FROM = 5  # this many players or more play on the large board

# The fixed beginner board, row by row (3-4-5-4-3), as (terrain, number).
BEGINNER_TILES = [
    (ORE, 10), (SHEEP, 2), (WOOD, 9),
    (WHEAT, 12), (BRICK, 6), (SHEEP, 4), (BRICK, 10),
    (WHEAT, 9), (WOOD, 11), (DESERT, 0), (WOOD, 3), (ORE, 8),
    (WOOD, 8), (ORE, 3), (WHEAT, 4), (SHEEP, 5),
    (BRICK, 5), (WHEAT, 6), (SHEEP, 11),
]
# its harbours clockwise from the north-west corner
BEGINNER_PORTS = ["any", WHEAT, ORE, "any", SHEEP, "any", "any", BRICK, WOOD]

RES_ALIASES = {
    "w": WOOD, "wd": WOOD, "wood": WOOD, "lumber": WOOD, "lum": WOOD,
    "b": BRICK, "br": BRICK, "brick": BRICK, "bricks": BRICK, "clay": BRICK,
    "s": SHEEP, "sh": SHEEP, "sheep": SHEEP, "wool": SHEEP,
    "wh": WHEAT, "wheat": WHEAT, "grain": WHEAT, "g": WHEAT,
    "o": ORE, "or": ORE, "ore": ORE, "stone": ORE,
}


def parse_res(tok):
    tok = tok.lower().strip(",.")
    if tok in RES_ALIASES:
        return RES_ALIASES[tok]
    for name, r in RES_ALIASES.items():
        if len(tok) >= 3 and name.startswith(tok):
            return r
    return None

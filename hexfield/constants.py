"""Static rules data for the base game."""

WOOD, BRICK, SHEEP, WHEAT, ORE = range(5)
DESERT = 5
RESOURCES = (WOOD, BRICK, SHEEP, WHEAT, ORE)
RES_NAMES = ("wood", "brick", "sheep", "wheat", "ore")
RES_SHORT = ("Wd", "Br", "Sh", "Wh", "Or")
TERRAIN_NAMES = ("FOREST", "HILLS", "PASTURE", "FIELDS", "MOUNTAIN", "DESERT")

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

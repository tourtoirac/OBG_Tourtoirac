"""
Hexagonal grid laid over a board image: a token released on the board has its
center pulled onto the center of the nearest hex.

The grid is described by a few numbers in the "grid" field of a board in the
game_json, rather than by the list of its hexes:

    "grid": {
        "type": "hex",
        "orientation": "flat",   # "flat" (flat top) or "pointy" (pointed top)
        "origin_x": 112.5,       # center of one hex, relative to the board's
        "origin_y": 98.0,        # top-left corner, in game_json units
        "size": 64.3,            # center-to-corner radius of a hex
        "size_y": 63.8,          # optional: vertical radius, for a scan that
                                 # is slightly stretched (defaults to size)
        "snap_radius": 40        # optional: no snap beyond this distance
    }

Coordinates are in the units of the board's game_json width and height (the
size the board is drawn at), not in pixels of the image file.
"""
import math

SQRT3 = math.sqrt(3)

GRID_TYPES = ('hex',)
ORIENTATIONS = ('flat', 'pointy')
DEFAULT_ORIENTATION = 'flat'


def read_number(value):
    """
    Reads a number from a hand-written game_json.
    :return: the number, or None when the value is not a finite number
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def cube_round(q: float, r: float) -> tuple[int, int]:
    """
    Rounds fractional axial coordinates to the hex that contains them. Rounding
    q and r separately would pick a wrong neighbour near the corners: the three
    cube coordinates are rounded, and the one that moved most is recomputed so
    that they still sum to zero.
    """
    s = -q - r
    rq, rr, rs = round(q), round(r), round(s)
    dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
    if dq > dr and dq > ds:
        rq = -rr - rs
    elif dr > ds:
        rr = -rq - rs
    return rq, rr


class HexGrid:
    def __init__(self, orientation: str, origin_x: float, origin_y: float,
                 size: float, size_y: float | None = None,
                 snap_radius: float | None = None):
        self.orientation = orientation
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.size = size
        self.size_y = size if size_y is None else size_y
        self.snap_radius = snap_radius

    @classmethod
    def from_json(cls, raw):
        """
        Builds the grid of a board from its game_json. A game_json is written by
        hand: a malformed grid is ignored, the board then simply does not snap,
        rather than preventing the game from starting.
        :param raw: the "grid" field of the board, possibly absent
        :return: a HexGrid, or None when there is no usable grid
        """
        if not isinstance(raw, dict):
            return None
        if raw.get('type', 'hex') not in GRID_TYPES:
            return None
        orientation = raw.get('orientation', DEFAULT_ORIENTATION)
        if orientation not in ORIENTATIONS:
            return None
        origin_x = read_number(raw.get('origin_x'))
        origin_y = read_number(raw.get('origin_y'))
        size = read_number(raw.get('size'))
        if origin_x is None or origin_y is None or size is None or size <= 0:
            return None
        size_y = read_number(raw.get('size_y'))
        if size_y is not None and size_y <= 0:
            return None
        snap_radius = read_number(raw.get('snap_radius'))
        # a null or negative radius means "no limit", like an absent one
        if snap_radius is not None and snap_radius <= 0:
            snap_radius = None
        return cls(orientation, origin_x, origin_y, size, size_y, snap_radius)

    def nearest_center(self, x: float, y: float) -> tuple[float, float]:
        """
        Center of the hex that contains (x, y). Both points are relative to the
        board's top-left corner.
        """
        # scale to a grid of unit hexes whose origin hex is centered on (0, 0)
        px = (x - self.origin_x) / self.size
        py = (y - self.origin_y) / self.size_y
        if self.orientation == 'pointy':
            q = SQRT3 / 3 * px - py / 3
            r = 2 / 3 * py
        else:
            q = 2 / 3 * px
            r = -px / 3 + SQRT3 / 3 * py
        q, r = cube_round(q, r)
        if self.orientation == 'pointy':
            cx = SQRT3 * (q + r / 2)
            cy = 1.5 * r
        else:
            cx = 1.5 * q
            cy = SQRT3 * (r + q / 2)
        return self.origin_x + cx * self.size, self.origin_y + cy * self.size_y

    def snap(self, x: float, y: float) -> tuple[float, float] | None:
        """
        Where a point dropped at (x, y) lands, relative to the board.
        :return: the center of the nearest hex, or None when it is further
            than snap_radius
        """
        cx, cy = self.nearest_center(x, y)
        if self.snap_radius is not None and math.hypot(cx - x, cy - y) > self.snap_radius:
            return None
        return cx, cy

    def return_json(self) -> dict:
        grid = {
            "type": "hex",
            "orientation": self.orientation,
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "size": self.size,
        }
        # optional fields are only written back when the game set them
        if self.size_y != self.size:
            grid["size_y"] = self.size_y
        if self.snap_radius is not None:
            grid["snap_radius"] = self.snap_radius
        return grid

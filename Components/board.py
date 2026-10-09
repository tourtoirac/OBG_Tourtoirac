from Components.component import Component
from Components.hex_grid import HexGrid

class Board(Component):
    # boards are immovable images. A board is never turned over on its own: the
    # local flip belongs to the board_group that holds it
    def __init__(self, component_id, x, y, src, width, height, grid = None):
        super().__init__(component_id)
        self.kind = 'board'
        self.x = x
        self.y = y
        self.src = src
        self.height = height
        self.width = width
        self.coordinates = (x, y)
        # grid: optional hex grid of the map, onto which released tokens snap.
        # None on a board without grid, or whose grid cannot be read.
        self.grid = HexGrid.from_json(grid)
        # group: the board_group holding this board, set by the group. The
        # magnetism belongs to the group: a board alone never snaps tokens, its
        # grid is only drawn to calibrate it.
        self.group = None

    def contains(self, x, y) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def snap_point(self, x, y):
        """
        Where a token center dropped at (x, y) lands on this board.
        :return: the world position of the nearest hex center, or None when the
            board has no grid, is not in a group with magnetism, or the point is
            too far from any
            center
        """
        if self.grid is None or self.group is None or not self.group.magnetism:
            return None
        snapped = self.grid.snap(x - self.x, y - self.y)
        if snapped is None:
            return None
        return self.x + snapped[0], self.y + snapped[1]

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        return {
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "id": self.id,
            "src": self.src,
            "height": self.height,
            "width": self.width,
            # the grid is part of the saved state: a resumed session keeps it
            "grid": self.grid.return_json() if self.grid is not None else None,
            "sat_list": sat_list
        }

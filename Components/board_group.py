from Components.board import Board
from Components.component import Component


class BoardGroup(Component):
    """
    One to n boards that make up a single map, turned over together. The flip
    is local to each player, like a board's own: the server never turns
    anything, it only keeps the group so the client can rotate all its boards
    by a half-turn around the center of the whole group, and so the group
    survives a save.

    Each board of the group is still a Board of its own: it is indexed by its
    id, moved by the setup and snaps tokens to its grid exactly like a board
    declared alone.
    """
    def __init__(self, component_id, boards: list[Board], flippable = False, magnetism = False):
        super().__init__(component_id)
        self.kind = 'board_group'
        self.boards = list(boards)
        # view_flippable: the game allows the local flip of the group. The name
        # differs from flippable() (a face without back): the group is seen from
        # either side depending on the player, it is not turned over for all
        self.view_flippable = flippable is True
        # magnetism: released tokens snap to the grids of the boards of the
        # group only when the game asks for it. Only the boolean True counts: a
        # string "false" is no more than an absent value.
        self.magnetism = magnetism is True
        for board in self.boards:
            board.group = self

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        return {
            "kind": self.kind,
            "id": self.id,
            "flippable": self.view_flippable,
            "magnetism": self.magnetism,
            "boards": [board.return_json() for board in self.boards],
            "sat_list": sat_list
        }

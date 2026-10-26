from Components.component import Component, nationality_allows, read_nationality
from user import User

# the two columns of an mw_character, and how many states a box of each goes
# through: the left one counts the wounds (empty, one slash, a cross), the
# right one the fatigue (empty, a circle)
CHARACTER_COLUMN_STATES = {'left': 3, 'right': 2}
# how many boxes a column holds, below its icon
CHARACTER_MIN_BOXES = 3
CHARACTER_MAX_BOXES = 6
CHARACTER_SIDES = ('front', 'back')


def read_column(column) -> dict | None:
    """
    Reads a column of an mw_character from the game_json. A column badly written is
    left out rather than stopping the game.
    :param column: {"icon": src, "boxes": 3 to 6, "marks": [...]}; marks is
        only there on a saved session
    :return: {"icon", "boxes"}, or None when there is no usable column: not an
        object, or boxes that is not a number. A number of boxes out of range
        is brought back between 3 and 6.
    """
    if not isinstance(column, dict):
        return None
    boxes = column.get('boxes')
    if isinstance(boxes, bool) or not isinstance(boxes, (int, float)):
        return None
    boxes = min(CHARACTER_MAX_BOXES, max(CHARACTER_MIN_BOXES, int(boxes)))
    icon = column.get('icon')
    return {'icon': icon if isinstance(icon, str) and icon else None, 'boxes': boxes}


def read_marks(marks, boxes: int, states: int) -> list:
    """
    The marks of a saved column, one per box. Anything that does not read as
    a state of that column starts empty.
    """
    marks = marks if isinstance(marks, list) else []
    read = []
    for index in range(boxes):
        mark = marks[index] if index < len(marks) else 0
        valid = isinstance(mark, int) and not isinstance(mark, bool) and 0 <= mark < states
        read.append(mark if valid else 0)
    return read


class MwCharacter(Component):
    """
    An mw_character is a card that never moves and carries two columns of boxes,
    one on each side, an icon on top of each. A click on a box of the left
    column adds a slash, then a cross, then empties it; on a box of the right
    column it draws a circle, then empties it. The boxes are kept by the
    server, sent to every screen and saved.

    It may be turned over (double click): the columns only show, and only
    take clicks, on its front.

    It also holds cards: a card released on it goes under it, face up, and
    the last one put is the first taken back (Session.put_under_character,
    Session.take_from_character). An mw_character that belongs to a nationality is only
    marked and turned over by the players of that nationality.
    """
    def __init__(self, component_id, x, y, width, height, front_src, back_src=None,
                 side=None, nationality=None, left=None, right=None):
        super().__init__(component_id)
        self.kind = 'mw_character'
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.coordinates = (x, y)
        self.image_src = {'front': front_src, 'back': back_src if back_src else None}
        self.side = 'back' if side == 'back' and self.image_src['back'] is not None else 'front'
        self.nationality = read_nationality(nationality)
        # the cards under the character, the last one put on top; built by
        # the session, which indexes them
        self.cards = []
        self.columns = {}
        for name, column in (('left', left), ('right', right)):
            read = read_column(column)
            if read is None:
                continue
            read['marks'] = read_marks(column.get('marks'), read['boxes'], CHARACTER_COLUMN_STATES[name])
            self.columns[name] = read

    def return_json(self, sat_list=None) -> dict:
        state = {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "front_src": self.image_src['front'],
            "back_src": self.image_src['back'],
            "side": self.side,
            "nationality": self.nationality,
        }
        for name, column in self.columns.items():
            state[name] = {"icon": column['icon'], "boxes": column['boxes'], "marks": list(column['marks'])}
        # every screen shows how many cards lie under it, and their front
        state["cards"] = [card.return_json() for card in self.cards]
        return state

    def contains(self, x, y) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def acquirable_by(self, user: User) -> bool:
        """
        Who may mark the boxes and turn the card over: a player of its
        nationality, anybody when it has none. It is never taken in hand.
        """
        return nationality_allows(self.nationality, user)

    def flippable(self) -> bool:
        return self.image_src['back'] is not None

    def flip(self):
        self.set_side('back' if self.side == 'front' else 'front')

    def set_side(self, side: str) -> bool:
        if not self.flippable():
            return False
        self.side = 'back' if side == 'back' else 'front'
        return True

    def markable(self) -> bool:
        """Whether a click on a box has an effect: only an mw_character has boxes."""
        return True

    def mark(self, column: str, index: int) -> int | None:
        """
        Moves a box on to its next state, back to empty after the last one.
        :return: the new state of the box, or None when there is no such box
        """
        read = self.columns.get(column)
        if read is None or isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < read['boxes']:
            return None
        read['marks'][index] = (read['marks'][index] + 1) % CHARACTER_COLUMN_STATES[column]
        return read['marks'][index]

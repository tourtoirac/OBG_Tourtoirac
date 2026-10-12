from Components.token import Token


class Card(Token):
    """
    A card is a token that lives in decks: it is moved, flipped and kept in a
    private hand like any token, and it is the only component a deck accepts.

    What sets it apart is its face: the front of a card lying on its back is
    told to nobody but the player keeping it in a private hand (see
    Session.sees_hidden_face). A card has no starting square, so it never
    shows the green rectangle of a token.
    """
    def __init__(self, component_id, x, y, front_image, back_image, width, height,
                 orientation=0, side=None, nationality=None, orientable=False, border=None):
        super().__init__(
            component_id, x, y, front_image, back_image, width, height,
            move_border=False,
            border=border,
            in_place=False,
            orientable=orientable,
            orientation=orientation,
            side=side,
            nationality=nationality,
        )
        self.kind = 'card'

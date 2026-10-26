from Components.token import Token


class Card(Token):
    """
    A card is a token that lives in decks: it is moved, flipped and kept in a
    private hand like any token, and it is the only component a deck accepts.

    What sets it apart is its face: the front of a card lying on its back is
    told to nobody but the player keeping it in a private hand (see
    Session.sees_hidden_face). A card has no starting square, so it never
    shows the green rectangle of a token. A card uses its configured rotation
    step (45 degrees by default) only when declared orientable. It is set back
    upright when it joins a private hand, and takes the orientation of the
    deck or stack it is put on (Session.enter_hand, Session.put_in_deck).
    """
    def __init__(self, component_id, x, y, front_image, back_image, width, height,
                 orientation=0, side=None, nationality=None, orientable=False, border=None, rotation_step=45):
        super().__init__(
            component_id, x, y, front_image, back_image, width, height,
            move_border=False,
            border=border,
            in_place=False,
            orientable=orientable,
            rotation_step=rotation_step,
            orientation=orientation,
            side=side,
            nationality=nationality,
        )
        self.kind = 'card'

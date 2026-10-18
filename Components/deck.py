import random

from Components.component import Component

# the piles of each type of deck: a "single" deck is one pile of cards, a
# "duo" deck is a draw pile and its discard pile
DECK_PILES = {
    'single': ('draw',),
    'duo': ('draw', 'discard'),
}

# pixels left between the draw pile and the discard pile of a "duo" deck when
# the game does not place the discard pile itself
DECK_GAP = 20

# the side a card takes when it joins a pile: the draw pile hides its cards,
# the discard pile shows them
PILE_SIDES = {
    'draw': 'back',
    'discard': 'front',
}

# card_pickup_quantity: the numbers of cards the multi-draw buttons of a pile
# take, one button each. Absent, the game gets these buttons; false, none.
DEFAULT_PICKUP_QUANTITIES = (2, 5, 10)
PICKUP_QUANTITIES_MAX = 5


def read_deck_orientation(value) -> int:
    """
    Reads the "orientation" key of a deck or a stack: the angle, in degrees,
    its piles are turned by, around their center.
    :return: 0, 90, 180 or 270; 0 for anything that is not a number or not a
        quarter turn
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    angle = int(value) % 360
    return angle if angle % 90 == 0 else 0


def read_pickup_quantities(value) -> list:
    """
    Reads the "card_pickup_quantity" key of a deck or a stack.
    :param value: absent (None), false, or a list of whole numbers
    :return: the quantities of the buttons, in the order of the game_json:
        the default ones when the key is absent or neither false nor a list,
        none for false. From a list, only the whole numbers above zero are
        kept, without duplicates, PICKUP_QUANTITIES_MAX at most.
    """
    if value is False:
        return []
    if not isinstance(value, list):
        return list(DEFAULT_PICKUP_QUANTITIES)
    quantities = []
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool) and item >= 1 and item not in quantities:
            quantities.append(item)
    return quantities[:PICKUP_QUANTITIES_MAX]


class Deck(Component):
    """
    A fixed component holding cards, and nothing but cards. A card released
    over one of its piles joins the top of it, and a click on a pile takes its
    top card out, straight into the hand of the player.

    A card inside a deck is still a Card of its own, indexed by its id, but it
    is on no list of the table and has no position: its x and y are None until
    it is drawn. In each pile the last card of the list is the one on top.
    """
    def __init__(self, component_id, x, y, width, height, deck_type='single', back_src=None,
                 draw=None, discard=None, text=None, discard_x=None, discard_y=None,
                 card_pickup_quantity=None, orientation=None, display_shuffle=None):
        super().__init__(component_id)
        self.kind = 'deck'
        # type: anything but "duo" is a single pile
        self.type = deck_type if deck_type in DECK_PILES else 'single'
        self.x = x
        self.y = y
        # width and height are those of one pile, that is of one card
        self.width = width
        self.height = height
        self.coordinates = (x, y)
        # back_src: the back the game gives the cards of this deck; a card
        # declaring its own keeps it
        self.back_src = back_src
        # text: optional caption the client writes above the deck. Absent,
        # null, empty or not a text, there is none: null is sent
        self.text = text if isinstance(text, str) and text != '' else None
        # where the discard pile lies; next to the draw pile when the game
        # does not say
        self.discard_x = discard_x if discard_x is not None else x + width + DECK_GAP
        self.discard_y = discard_y if discard_y is not None else y
        # the multi-draw buttons the client shows on each pile
        self.card_pickup_quantity = read_pickup_quantities(card_pickup_quantity)
        # orientation: the piles are turned around their center; x, y,
        # width and height stay those of the pile before it is turned, like
        # for a token. A card drawn comes out turned the same way.
        self.orientation = read_deck_orientation(orientation)
        # display_shuffle: only false hides the Shuffle buttons, and the
        # piles can then no longer be shuffled by the players
        self.display_shuffle = display_shuffle is not False
        self.piles = {pile: [] for pile in DECK_PILES[self.type]}
        for card in draw or []:
            self.add('draw', card)
        for card in discard or []:
            # a single deck has no discard pile: its cards are all in the draw
            self.add('discard' if 'discard' in self.piles else 'draw', card)

    def pile_position(self, pile: str) -> tuple:
        """
        The top-left corner of a pile.
        """
        if pile == 'discard':
            return self.discard_x, self.discard_y
        return self.x, self.y

    def pile_at(self, x, y) -> str | None:
        """
        The pile of this deck the point (x, y) lies on, or None.
        """
        for pile in self.piles:
            left, top, width, height = self.footprint(pile)
            if left <= x <= left + width and top <= y <= top + height:
                return pile
        return None

    def footprint(self, pile: str) -> tuple:
        """
        The rectangle a pile covers on the table once turned by orientation:
        a quarter turn swaps its width and height around the same center.
        :return: (x, y, width, height)
        """
        pile_x, pile_y = self.pile_position(pile)
        width, height = self.width, self.height
        if self.orientation in (90, 270):
            width, height = height, width
        center_x = pile_x + self.width / 2
        center_y = pile_y + self.height / 2
        return center_x - width / 2, center_y - height / 2, width, height

    def pile_holding(self, card) -> str | None:
        """
        The pile a card is waiting in, or None when this deck does not hold it.
        """
        for pile, cards in self.piles.items():
            if any(card is held for held in cards):
                return pile
        return None

    def holds(self, card) -> bool:
        return self.pile_holding(card) is not None

    def add(self, pile: str, card) -> bool:
        """
        Puts a card on top of a pile: it loses its position on the table and
        takes the side of the pile, hidden in the draw, shown in the discard.
        :return: False when this deck has no such pile
        """
        if pile not in self.piles:
            return False
        card.leave_table()
        # a card without a back has one single face and keeps it
        card.set_side(PILE_SIDES[pile])
        self.piles[pile].append(card)
        return True

    def top(self, pile: str):
        """
        The card on top of a pile, or None when the pile is empty or unknown.
        """
        cards = self.piles.get(pile)
        return cards[-1] if cards else None

    def draw(self, pile: str = 'draw', user=None):
        """
        Takes the top card out of a pile.
        :param user: when given, the card only comes out if that user may
            acquire it: a card of another nationality stays on its pile
        :return: the card, or None when there is none to take
        """
        card = self.top(pile)
        if card is None or (user is not None and not card.acquirable_by(user)):
            return None
        return self.piles[pile].pop()

    def shuffle(self, pile: str = 'draw') -> bool:
        """
        Puts the cards of a pile in a random order.
        :return: False when this deck has no such pile
        """
        if pile not in self.piles:
            return False
        random.shuffle(self.piles[pile])  # NOSONAR
        return True

    def refill(self) -> int:
        """
        Puts every card of the discard pile back into the draw pile, face
        down, under the cards the draw pile still holds. The order of the
        discarded cards is kept: shuffling is another action.
        :return: the number of cards moved, 0 for a deck without discard pile
        """
        discarded = self.piles.get('discard')
        if not discarded:
            return 0
        for card in discarded:
            card.set_side(PILE_SIDES['draw'])
        self.piles['draw'][:0] = discarded
        moved = len(discarded)
        self.piles['discard'] = []
        return moved

    def pile_json(self, pile: str) -> dict:
        """
        What everybody may know of a pile: where it lies, how many cards it
        holds and the picture its top card shows there. Neither the id nor the
        other face of that card is given.
        """
        pile_x, pile_y = self.pile_position(pile)
        card = self.top(pile)
        top = None
        if card is not None:
            top = {
                # a card without a back has nothing to show on a draw pile:
                # the client draws a plain back
                "src": card.image_src[PILE_SIDES[pile]],
                "width": card.width,
                "height": card.height,
            }
        return {
            "x": pile_x,
            "y": pile_y,
            "count": len(self.piles[pile]),
            "top": top,
        }

    def return_json(self, sat_list = None) -> dict:
        """
        The deck as the clients read it: its piles, without the cards they
        hold. The cards only leave the server one by one, when they are drawn.
        """
        return {
            "kind": self.kind,
            "id": self.id,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "back_src": self.back_src,
            "text": self.text,
            "discard_x": self.discard_x,
            "discard_y": self.discard_y,
            "card_pickup_quantity": list(self.card_pickup_quantity),
            "orientation": self.orientation,
            "display_shuffle": self.display_shuffle,
            "piles": {pile: self.pile_json(pile) for pile in self.piles},
        }

    def state_json(self) -> dict:
        """
        The deck as the saved state stores it, in the format the session reads
        back: the same description without what is computed from the cards,
        and the cards of each pile, bottom first.
        """
        state = self.return_json()
        del state['piles']
        state['components'] = [card.return_json() for card in self.piles['draw']]
        if 'discard' in self.piles:
            state['discard'] = [card.return_json() for card in self.piles['discard']]
        return state

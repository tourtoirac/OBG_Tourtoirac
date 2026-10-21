from Components.deck import Deck


class Slot(Deck):
    """
    A slot is a place on the table where cards are put down, one on top of
    the other. Like a stack it never moves, only holds cards and has one
    single pile, whose width and height are those of the area the cards are
    released on: the client centers them in it, which is all the magnetism
    it has.

    What sets it apart from a stack: a card keeps the side it showed when it
    was released, the top card shows that side, and the pile is never
    shuffled, dealt nor drawn from by the multi-draw buttons. A card drawn
    comes out upright, like from any deck.
    """
    def __init__(self, component_id, x, y, width, height, back_src=None, cards=None, text=None,
                 orientation=None):
        # a slot has no discard pile, no multi-draw button and no Shuffle button
        super().__init__(component_id, x, y, width, height, 'single', back_src, cards, None, text,
                         card_pickup_quantity=False, orientation=orientation,
                         display_shuffle=False)
        self.kind = 'slot'

    def add(self, pile: str, card) -> bool:
        """
        Puts a card on top of the pile: it loses its position on the table
        but keeps the side it shows.
        :return: False when this slot has no such pile
        """
        if pile not in self.piles:
            return False
        card.leave_table()
        self.piles[pile].append(card)
        return True

    def shuffle(self, pile: str = 'draw') -> bool:
        # the cards of a slot stay in the order they were put down
        return False

    def pile_json(self, pile: str) -> dict:
        state = super().pile_json(pile)
        card = self.top(pile)
        if card is not None:
            # the top card shows the side it lies on, not the side of the pile
            state['top']['src'] = card.image_src[card.side]
        return state

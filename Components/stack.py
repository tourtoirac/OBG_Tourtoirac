from Components.deck import Deck


class Stack(Deck):
    """
    A stack is a deck of one single pile that deals its cards. Like any deck
    it never moves and only holds cards, which join it face down, on top.

    What sets it apart: its width and height are those of the area the cards
    are released on, not those of a card, and the client centers the cards in
    that area; and a stack deals, giving its top cards to the private hands of
    the players, one each.
    """
    def __init__(self, component_id, x, y, width, height, back_src=None, cards=None, text=None):
        # a stack has no discard pile: it is always a "single" deck
        super().__init__(component_id, x, y, width, height, 'single', back_src, cards, None, text)
        self.kind = 'stack'

    def can_deal(self, players: int) -> bool:
        """
        Whether the stack holds one card for each of that many players.
        """
        return 0 < players <= len(self.piles['draw'])

    def deal(self, players: int) -> list:
        """
        Takes one card for each player out of the stack, from the top down.
        :return: the cards, the top one first; empty when the stack does not
            hold one for everybody, in which case it is left untouched
        """
        if not self.can_deal(players):
            return []
        return [self.piles['draw'].pop() for _ in range(players)]

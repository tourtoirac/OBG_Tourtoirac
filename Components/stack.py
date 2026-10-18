from Components.deck import Deck


class Stack(Deck):
    """
    A stack is a deck of one single pile that deals its cards. Like any deck
    it never moves and only holds cards, which join it face down, on top.

    What sets it apart: its width and height are those of the area the cards
    are released on, not those of a card, and the client centers the cards in
    that area; and a stack may deal, giving its top cards to the private
    hands of the players, one each, when the game declares "deal": true.
    """
    def __init__(self, component_id, x, y, width, height, back_src=None, cards=None, text=None,
                 card_pickup_quantity=None, orientation=None, deal=False, display_shuffle=None):
        # a stack has no discard pile: it is always a "single" deck
        super().__init__(component_id, x, y, width, height, 'single', back_src, cards, None, text,
                         card_pickup_quantity=card_pickup_quantity, orientation=orientation,
                         display_shuffle=display_shuffle)
        self.kind = 'stack'
        # whether the stack deals: only the boolean true shows the Deal button
        self.deal_enabled = deal is True

    def return_json(self, sat_list=None) -> dict:
        state = super().return_json(sat_list)
        state['deal'] = self.deal_enabled
        return state

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

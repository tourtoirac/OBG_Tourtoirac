import random

from card import Card

class Deck:
    # decks contains cards
    # fixed or movable
    # fixed object
    def __init__(self, x, y, src, width, height, image_src, fixed=False):
        self.x = x
        self.y = y
        self.src = image_src
        self.width = width
        self.height = height
        self.fixed = fixed
        self.draw_stack = []
        self.discard_stack = []

    def return_json(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height
        }

    def move(self, x, y):
        if not self.fixed:
            self.x = x
            self.y = y

    def draw(self):
        return random.choice(self.draw_stack)  # NOSONAR

    def discard(self, card):
        if isinstance(card, Card):
            self.discard_stack.append(card)

    def shuffle(self, stack):
        random.shuffle(stack) # NOSONAR

    def list_stack(self, stack):
        component_list = []
        for component in stack:
            component_list.append(component.return_json())
        return component_list

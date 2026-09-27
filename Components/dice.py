import random
from Components.component import Component
from user import User

class Dice(Component):
    # dice can be moved, rolled
    def __init__(self, id, x, y, src, width, height, src_list):
        super().__init__(id)
        self.id = id
        self.kind = 'dice'
        self.x = x
        self.y = y
        self.src = src_list[0]
        self.width = width
        self.height = height
        self.src_list = src_list
        self.acquired_by = None
        self.coordinates = (x, y)

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height
        }

    def move(self, x, y, user: User):
        if self.acquired_by == user:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)

    def roll(self):
        self.src = random.choice(self.src_list)  # NOSONAR

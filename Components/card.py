from Components.component import Component
from user import User

import random

class Card(Component):
    def __init__(self, id, x, y, front_scr, back_src, width, height, orientation):
        # cards are objects that can be moved, flipped, tapped
        super().__init__(id)
        self.id = id
        self.kind = 'card'
        self.x = x
        self.y = y
        self.orientation = orientation
        self.side = 'back'
        self.image_src = {
            "front": front_scr,
            "back": back_src
        }
        self.src = self.image_src[self.side]
        self.width = width
        self.height = height
        self.acquired_by = None
        self.coordinates = (x, y)

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation,
            "sat_list": sat_list
        }

    def flip(self):
        if self.side == 'front':
            self.side = 'back'
            self.src = self.image_src[self.side]
        else:
            self.side = 'front'

    def move(self, x, y, user: User):
        if self.acquired_by == user:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)

    def tap(self, orientation: int):
        self.orientation = orientation


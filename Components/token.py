from Components.component import Component
from user import User

class Token (Component):
    # tokens are objects that can be moved, flipped
    def __init__(self, component_id, x, y, front_image, back_image, width, height):
        super().__init__(component_id)
        self.kind = 'token'
        self.x = x
        self.y = y
        self.side = 'front'
        self.image_src = {
            "front": front_image,
            "back": back_image
        }
        self.src = self.image_src[self.side]
        self.width = width
        self.height = height
        self.orientation = 0
        self.acquired_by = None
        self.coordinates = (self.x, self.y)

    def acquire(self, user: User):
        if self.acquired_by is not None and self.acquired_by != user:
            return False
        self.acquired_by = user
        if self.id not in user.acquired:
            user.acquire(self.id)
        return True

    def flip(self):
        if self.side == 'front' and self.image_src['back'] is not None:
            self.side = 'back'
            self.src = self.image_src[self.side]
        else:
            self.side = 'front'
            self.src = self.image_src[self.side]

    def move(self, x, y, user: User):
        if self.acquired_by == user:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)

    def release(self, user: User):
        if self.acquired_by != user:
            return False
        self.acquired_by = None
        user.release(self.id)
        return True

    def return_json(self, sat_list = None) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "side": self.side,
            "front_src": self.image_src["front"],
            "back_src": self.image_src["back"],
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation
        }

class Token:
    # tokens are objects that can be moved, flipped
    def __init__(self, id, x, y, front_image, back_image, width, height):
        self.kind = 'token'
        self.id = id
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

    def flip(self):
        if self.side == 'front':
            self.side = 'back'
            self.src = self.image_src[self.side]
        else:
            self.side = 'front'
            self.src = self.image_src[self.side]

    def move(self, x, y):
        self.x = x
        self.y = y

    def return_json(self) -> dict:
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

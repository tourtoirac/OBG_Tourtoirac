class Card:
    def __init__(self, x, y, front_scr, back_src, width, height, orientation):
        # cards are objects that can be moved, flipped, tapped
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

    def flip(self):
        if self.side == 'front':
            self.side = 'back'
            self.src = self.image_src[self.side]
        else:
            self.side = 'front'

    def move(self, x, y):
        self.x = x
        self.y = y

    def tap(self, orientation):
        self.orientation = orientation

    def return_json(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation
        }

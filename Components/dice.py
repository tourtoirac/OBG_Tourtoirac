import random

class Dice:
    # dice can be moved, rolled
    def __init__(self, id, x, y, src, width, height, src_list):
        self.id = id
        self.kind = 'dice'
        self.x = x
        self.y = y
        self.src = src_list[0]
        self.width = width
        self.height = height
        self.src_list = src_list

    def move(self, x, y):
        self.x = x
        self.y = y

    def roll(self):
        self.src = random.choice(self.src_list)  # NOSONAR

    def return_json(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height
        }

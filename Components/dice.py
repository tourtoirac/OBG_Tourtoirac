import random

class Dice:
    # dice can be moved, rolled
    def __init__(self, x, y, src, width, height, src_list):
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
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height
        }

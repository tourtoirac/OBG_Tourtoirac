import random

class Bag:
    # bags contains objects and release them randomly
    # fixed object
    def __init__(self, x, y, width, height, image_src):
        self.x = x
        self.y = y
        self.src = image_src
        self.width = width
        self.height = height
        self.component_list = []

    def return_json(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height
        }

    def fill(self, component_list):
        for component in component_list:
            self.component_list.append(component)

    def get(self):
        return random.choice(self.component_list)  # NOSONAR

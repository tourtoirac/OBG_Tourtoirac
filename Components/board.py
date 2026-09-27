from Components.component import Component

class Board(Component):
    # boards are immovable images
    def __init__(self, x, y, src, height, width):
        super().__init__(id)
        self.kind = 'board'
        self.x = x
        self.y = y
        self.src = src
        self.height = height
        self.width = width
        self.coordinates = (x, y)

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        return {
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "id": self.id,
            "src": self.src,
            "height": self.height,
            "width": self.width,
            "sat_list": sat_list
        }

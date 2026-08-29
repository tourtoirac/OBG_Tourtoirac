class Board:
    # boards are immovable images
    def __init__(self, id, x, y, src, height, width):
        self.x = x
        self.y = y
        self.id = id
        self.src = src
        self.height = height
        self.width = width

    def return_json(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "id": self.id,
            "src": self.src,
            "height": self.height,
            "width": self.width
        }

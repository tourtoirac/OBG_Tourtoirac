class Board:
    def __init__(self, x, y, src, height, width):
        self.x = x
        self.y = y
        self.src = src
        self.height = height
        self.width = width

    def draw(self, canvas):
        canvas.drawImage(
            self.src,
            self.x,
            self.y,
            self.width,
            self.height
        )

class Token:
    def __init__(self, x, y, front_image, back_image, width, height):
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

    def draw(self, canvas):
        canvas.drawImage(
            self.src,
            self.x,
            self.y,
            self.width,
            self.height
        )
    def get_position(self):
        return self.x, self.y

    def check_collision(self, x, y):
        return self.x <= x <= self.x + self.width  and self.y <= y <= self.y + self.height


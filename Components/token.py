from Components.component import Component
from user import User

class Token (Component):
    # tokens are objects that can be moved, flipped
    def __init__(self, component_id, x, y, front_image, back_image, width, height, move_border=True):
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
        # move_border : le jeton peut-il être repositionne pendant le tour
        self.move_border = move_border
        # border : rectangle vert affiché autour du jeton
        self.border = move_border
        # emplacement initial, celui du jeu : y revenir rend le rectangle vert
        self.initial_x = x
        self.initial_y = y

    def at_initial_position(self, x, y) -> bool:
        return x == self.initial_x and y == self.initial_y

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
            # un jeton deplace perd son rectangle jusqu'au prochain "fixe la position"
            self.border = False

    def place(self, x, y, user: User):
        """
        Position finale au lacher. Contrairement a move(), elle n'exige pas que
        le jeton ait bouge pendant le glisser, et elle rend le rectangle vert au
        jeton qui retrouve son emplacement initial.
        :return: True si le jeton etait bien tenu par user
        """
        if self.acquired_by != user:
            return False
        self.x = x
        self.y = y
        self.coordinates = (self.x, self.y)
        self.border = self.move_border and self.at_initial_position(x, y)
        return True

    def fix_position(self):
        """
        Remet le rectangle vert sur le jeton, s'il est repositionnable.
        """
        self.border = self.move_border

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
            "orientation": self.orientation,
            "move_border": self.move_border,
            "border": self.border
        }

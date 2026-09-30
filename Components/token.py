from Components.component import Component
from user import User

# Un jeton lâché à moins de MOVE_THRESHOLD pixels de sa case de départ est
# automatiquement recadré dessus. La valeur est en dur dans le code : c'est une
# règle de jeu, pas un réglage.
MOVE_THRESHOLD = 30

class Token (Component):
    # tokens are objects that can be moved, flipped
    def __init__(self, component_id, x, y, front_image, back_image, width, height, move_border=True, initial=None, border=None):
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
        # emplacement initial, celui du jeu : y revenir rend le rectangle vert.
        # initial/border sont fournis quand la session est reprise après une sauvegarde.
        if initial is None:
            initial = (x, y)
        self.initial_x = initial[0]
        self.initial_y = initial[1]
        # un jeton non repositionnable n'affiche jamais le rectangle vert
        self.border = move_border if border is None else (border and move_border)

    def near_initial_position(self, x, y) -> bool:
        """
        Un jeton est considéré comme posé à sa case de départ s'il est à moins
        de MOVE_THRESHOLD pixels d'elle.
        """
        dx = x - self.initial_x
        dy = y - self.initial_y
        return dx * dx + dy * dy <= MOVE_THRESHOLD * MOVE_THRESHOLD

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
        le jeton ait bouge pendant le glisser.

        Un jeton repositionnable lache près de sa case de départ y est recadre et
        recupere son rectangle vert ; sinon il reste où il a été deposé et perd la
        bordure.
        :return: True si le jeton etait bien tenu par user
        """
        if self.acquired_by != user:
            return False
        if self.move_border and self.near_initial_position(x, y):
            x, y = self.initial_x, self.initial_y
            self.border = True
        else:
            self.border = False
        self.x = x
        self.y = y
        self.coordinates = (self.x, self.y)
        return True

    def fix_position(self):
        """
        La position courante devient la nouvelle case de départ du jeton, qui
        recupere son rectangle vert s'il est repositionnable.
        """
        self.initial_x = self.x
        self.initial_y = self.y
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
            "border": self.border,
            "initial_x": self.initial_x,
            "initial_y": self.initial_y
        }

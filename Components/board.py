from Components.component import Component

class Board(Component):
    # boards are immovable images
    def __init__(self, component_id, x, y, src, width, height, flippable = False):
        super().__init__(component_id)
        self.kind = 'board'
        self.x = x
        self.y = y
        self.src = src
        self.height = height
        self.width = width
        # flippable : le jeu autorise-t-il le retournement local de ce plateau.
        # Le nom differe de flippable() (une face n'a pas de dos) : le plateau
        # est visible de deux cotes selon le joueur, pas retourne pour tous.
        self.view_flippable = flippable
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
            "flippable": self.view_flippable,
            "sat_list": sat_list
        }

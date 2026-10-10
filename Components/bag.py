import random

from Components.component import Component


class Bag(Component):
    """
    A fixed component holding tokens out of sight. A token released over the
    bag joins its content, and a click on the bag takes one of them out at
    random, straight into the hand of the player.

    A token inside the bag is still a Token of its own, indexed by its id, but
    it is on no list of the table and has no position: its x and y are None
    until it is picked.
    """
    def __init__(self, component_id, x, y, width, height, image_src, components=None):
        super().__init__(component_id)
        self.kind = 'bag'
        self.x = x
        self.y = y
        self.src = image_src
        self.width = width
        self.height = height
        self.coordinates = (x, y)
        self.components = []
        for component in components or []:
            self.add(component)

    def contains(self, x, y) -> bool:
        """
        Whether the point (x, y) lies on the bag.
        """
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def holds(self, component) -> bool:
        return any(component is held for held in self.components)

    def add(self, component):
        """
        Puts a token into the bag: it loses its position on the table.
        """
        component.leave_table()
        self.components.append(component)

    def pick(self):
        """
        Takes one token out of the bag, at random.
        :return: the token, or None when the bag is empty
        """
        if not self.components:
            return None
        picked = random.choice(self.components)  # NOSONAR
        self.components.remove(picked)
        return picked

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height,
            "components": [component.return_json() for component in self.components]
        }

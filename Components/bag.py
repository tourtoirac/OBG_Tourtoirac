import random

from user import User
from component import Component

class Bag(Component):
    # bags contains objects and release them randomly
    # fixed object
    def __init__(self, id, x, y, width, height, image_src):
        super().__init__(id)
        self.kind = 'bag'
        self.x = x
        self.y = y
        self.src = image_src
        self.width = width
        self.height = height
        self.acquired_by = None
        self.coordinates = (x, y)
        self.containers = {
            'bag' : [],
        }

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height,
            "sat_list": sat_list,
        }

    def add(self, container_name: str, component, position: int = 0):
        self.containers[container_name].append(component)

    def empty(self, container_name: str):
        while self.containers[container_name]:
            yield self.pick(container_name)

    def pick(self, container_name: str):
        if container_name in self.containers:
            returned_component = random.choice(self.containers[container_name])  # NOSONAR
            self.containers[container_name].remove(returned_component)
            return returned_component
        return None


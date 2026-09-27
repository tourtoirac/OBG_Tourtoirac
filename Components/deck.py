from Components.component import Component
from user import User

import random


class Deck(Component):
    # decks contains cards
    # fixed or movable
    # fixed object
    def __init__(self, id, x, y, src, width, height, image_src, fixed= False):
        super().__init__(id)
        self.id = id
        self.kind = 'deck'
        self.x = x
        self.y = y
        self.src = image_src
        self.width = width
        self.height = height
        self.fixed = fixed
        self.containers = {
            'draw' : [],
            'discard': []
        }
        self.acquired_by = None
        self.coordinates = (x, y)

    def return_json(self, sat_list = None) -> dict:
        if sat_list is None:
            sat_list = []
        stacks_count = {}
        for stack in self.containers:
            stacks_count[stack] = len(self.containers[stack])
        returned_data = {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            "width": self.width,
            "height": self.height,
            "stacks_count": stacks_count,
        }
        if 'components' in sat_list:
            components_dict = {}
            for stack_name in self.containers:
                components_dict[stack_name] = []
                for component in self.containers[stack_name]:
                    components_dict[stack_name].append(component.return_json())
            returned_data['components'] = components_dict

        return returned_data

    def add(self, container_name: str, component, position: int = 0):
        if container_name in self.containers:
            if position < 0 or position > len(self.containers[container_name]):
                position = -1
            self.containers[container_name].insert(component, position)

    def move(self, x, y, user: User):
        if not self.fixed and user == self.acquired_by:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)

    def pick(self, container_name: str):
        if container_name in self.containers:
            returned_component = random.choice(self.containers[container_name])  # NOSONAR
            self.containers[container_name].remove(returned_component)
            return returned_component
        return None

    def shuffle(self, container_name: str):
        if container_name in self.containers:
            random.shuffle(self.containers[container_name]) # NOSONAR

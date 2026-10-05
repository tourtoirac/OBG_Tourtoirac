from user import User

class Component:
    # basic object. Used to build other components
    def __init__(self, component_id):
        self.kind = 'component'
        self.id = component_id
        self.coordinates = None

    def return_json(self, sat_list = None):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def acquire(self, user: User):
        """
        Marks the component as acquired by user.
        Returns True on success, False if the component cannot be acquired.
        """
        return False

    def add(self, container_name: str, component, position: int = 0):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def draw(self):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def empty(self, container_name: str):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def flippable(self) -> bool:
        """
        Whether this component shows another face when turned over. Only a
        token whose game_json gives it a back_src does: a counter with a single
        image has nothing to reveal.
        :return: True when the component may be flipped
        """
        return False

    def flip(self):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def set_side(self, side: str) -> bool:
        """
        Whether this component can be shown on that face. Only a token whose
        game_json gives it a back_src can: a board, a dice or a counter has one
        single face and stays on it.
        :param side: "front" or "back"
        :return: True when the face was actually set
        """
        return False

    def move(self, x: int, y: int, user: User):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def pick(self, container_name: str):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def release(self, user: User):
        """
        Releases the component held by user.
        Returns True on success, False if user did not hold the component.
        """
        return False

    def roll(self):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def clickable(self) -> bool:
        """
        Whether a click on this component triggers a roll. Only the dice does:
        tokens and boards are grabbed, never rolled.
        :return: True when a click has an effect on this component
        """
        return False

    def is_rolling_allowed(self) -> bool:
        """
        Whether the component may be rolled right now.
        :return: True when no cooldown is running
        """
        return True

    def roll_cooldown(self) -> float:
        """
        Delay, in seconds, that a roll puts on this component before another
        one is accepted.
        :return: number of seconds
        """
        return 0.0

    def rotatable(self) -> bool:
        """
        Whether this component can be turned. Only a token that its game_json
        marks as orientable can; boards and dice have nothing to turn.
        :return: True when the component may be rotated
        """
        return False

    def rotate(self, delta: int) -> int:
        """
        Turns the component by delta degrees, clockwise when positive.
        :return: the new orientation, in degrees
        """
        return 0

    def incrementable(self) -> bool:
        """
        Whether this component can count up. Only a counter does: nothing else
        displays a value a player may raise.
        :return: True when the component may count up
        """
        return False

    def decrementable(self) -> bool:
        """
        Whether this component can count down. A counter can, the other
        components have no value to lower.
        :return: True when the component may count down
        """
        return False

    def shuffle(self, container_name: str):
        """
        placeholder function
        :return: Nothing
        """
        pass

    def tap(self, orientation: int):
        """
        placeholder function
        :return: Nothing
        """
        pass


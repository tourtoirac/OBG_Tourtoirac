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

    def flip(self):
        """
        placeholder function
        :return: Nothing
        """
        pass

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


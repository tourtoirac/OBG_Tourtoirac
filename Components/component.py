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


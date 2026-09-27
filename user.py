import uuid
import json

class User:
    def __init__(self, name, protocol):
        self.id = str(uuid.uuid4())
        self.name = name
        self.protocol = protocol
        self.session = None
        self.acquired = []

    def return_user_json(self) -> dict:
        """
        returns a JSON representation of the user
        :return: dict
        """
        user_json = {
            "id": self.id,
            "name": self.name,
            "session": self.session.key,
            "acquired": self.acquired
        }
        return user_json

    def acquire(self, component_id):
        """
        Adds the component id to the acquired list
        """
        self.acquired.append(component_id)


    def release(self, component_id):
        """
        Removes the component id from the acquired list
        """
        if component_id in self.acquired:
            self.acquired.remove(component_id)

    def return_acquired(self) -> list:
        return self.acquired

    def send(self, message):
        """
        Sends a JSON message to the client
        """
        payload = json.dumps(message)

        self.protocol.sendMessage(
            payload.encode("utf-8"),
            isBinary=False
        )


import uuid
import json

class User:
    def __init__(self, name, protocol):
        self.id = str(uuid.uuid4())
        self.name = name
        self.protocol = protocol
        self.game = None

    def send(self, message):
        """
        Sends a JSON message to the client
        """
        payload = json.dumps(message)

        self.protocol.sendMessage(
            payload.encode("utf-8"),
            isBinary=False
        )

    def return_user_json(self) -> dict:
        """
        returns a JSON representation of the user
        :return: dict
        """
        user_json = {
            "id": self.id,
            "name": self.name,
            "game": self.game.key
        }
        return user_json

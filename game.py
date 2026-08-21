import uuid

class Game:
    def __init__(self, code: str):
        self.id = str(uuid.uuid4())
        self.code = code
        self.game_dict = {}

    def return_game_json(self) -> dict:
        """
        returns the description of the game
        :return: dict
        """
        game_json = {
            "id": self.id,
            "code": self.code,
            "game_dict": self.game_dict
        }
        return game_json
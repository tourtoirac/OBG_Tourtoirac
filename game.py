import uuid

class Game:
    def __init__(self, code: str, max_players: int, max_watchers: int, game: str):
        self.id = str(uuid.uuid4())
        self.code = code
        self.components = {
            "fixed": [],
            "movable": [],
        }
        self.max_players = max_players
        self.max_watchers = max_watchers
        self.players = []
        self.watchers = []

    def return_game_json(self) -> dict:
        """
        returns the description of the game
        :return: dict
        """
        game_json = {
            "id": self.id,
            "code": self.code,
            "max_players": self.max_players,
            "max_watchers": self.max_watchers,
            "players": f"{len(self.players)}/{self.max_players}",
            "watchers": f"{len(self.watchers)}/{self.max_watchers}",
            "components": self.components
        }
        return game_json

    def add_user(self, user, role):
        """
        Adds a user to the game. role determine where the user is added
        Returns True if successful,
        False if the game no longer has seats or if the user is already present
        """
        if user in self.players or user in self.watchers:
            return False
        match role:
            case 'player':
                if len(self.players) >= self.max_players:
                    return False
                self.players.append(user)
                return True
            case 'watcher':
                if len(self.watchers) >= self.max_watchers:
                    return False
                self.watchers.append(user)
                return True
            case _:
                return False

    def remove_user(self, user):
        """
        Removes a user from the table.
        """
        if user in self.players:
            self.players.remove(user)
        elif user in self.watchers:
            self.watchers.remove(user)

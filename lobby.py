import requests
import uuid

from chabanas import Chabanas
from game import Game
from user import User


class Lobby:

    def __init__(self, logger, chabanas):
        self.id = str(uuid.uuid4())
        self.games = {}
        self.users = {}
        self.logger = logger
        self.chabanas = chabanas

    def return_json(self):
        """
        Returns a description of the lobby.
        """
        return {
            "active": self.chabanas.get_lobby_active_games(),
        }

    def create_game(self, game_name: str, user: User, key: str = None):
        game_info = self.chabanas.get_game_init_info(game_name, user, key)
        if game_info:
            game = Game(
                name=game_name,
                key=game_info["key"],
                code=game_info["code"],
                player=user,
                game_json=game_info['game_json']
            )
            user.game = game
            self.add_game(game)
            self.games[game].add_user(user, "player")
            return True, None
        else:
            return False, "Unable to create game"

    def add_game(self, game: Game):
        if game not in self.games:
            self.games[game] = game

    def remove_game(self, game: Game):
        if game in self.games:
            del self.games[game]

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def delete_user(self, user: User):
        if user.protocol in self.users:
            if user.game.key is not None:
                self.games[user.game].remove_user(user)
            del self.users[user.protocol]
import uuid

from game import Game
from user import User


class Lobby:

    def __init__(self):
        self.id = str(uuid.uuid4())
        self.games = {}
        self.users = {}

    def return_lobby_json(self):
        """
        Returns a description of the lobby.
        """
        lobby_games_list = []

        for game in self.games:
            lobby_games_list.append(
                self.games[game].return_game_json()
            )

        return {
            "lobby": lobby_games_list
        }

    def add_game(self, game: Game):
        if game.id not in self.games:
            self.games[game.id] = game

    def remove_game(self, game: Game):
        if game.id in self.games:
            del self.games[game.id]

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def delete_user(self, user: User):
        if user.protocol in self.users:
            if user.game_id is not None:
                self.games[user.game_id].remove_user(user)
            del self.users[user.protocol]
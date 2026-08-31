import json
import uuid

from game import Game
from user import User


class Lobby:

    def __init__(self, logger, chabanas):
        self.id = str(uuid.uuid4())
        self.games = {}
        self.users = {}
        self.logger = logger
        self.chabanas = chabanas

    def return_json(self, sat_list):
        """
        Returns a description of the lobby.
        """
        return {
            "active": self.chabanas.get_lobby_active_games(sat_list),
        }

    def create_game(self, game_name: str, user: User, key: str = None):
        # create a new game with first user
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

    def start_game(self, game_name, game_code: str, user, key: str):
        # get game current json and add it to the lobby
        game_info = self.chabanas.get_game_info(game_name, game_code)
        if game_info:
            if (game_info['player1_nickname'] == user.name and game_info['player1_key'] == key) or (game_info['player2_nickname'] == user.name and game_info['player2_key'] == key):
                self.logger.debug(f"Game {game_name} started")
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
                return True, Game
            else:
                self.logger.error("User not allowed to join the game")
                return False, "User not allowed to join the game"
        else:
            self.logger.error(f"Game {game_name} not found")
            return False, "Game not found"

    def join_game(self, game_name: str, user: User, key: str = None):
        self.logger.debug(f"{user.name} joined game {game_name} with key {key}")

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
            if user.game is not None and user.game.key is not None:
                self.games[user.game].remove_user(user)
            del self.users[user.protocol]

    def send_keep_alive(self):
        message = {
            "action": "keep_alive"
        }

        encoded = json.dumps(message).encode("utf-8")

        for user in self.users.values():

            try:
                user.protocol.sendMessage(
                    encoded,
                    isBinary=False
                )

            except Exception as error:
                self.logger.error(
                    f"[KEEP_ALIVE] Error sending message to {user.name}: {error}"
                )
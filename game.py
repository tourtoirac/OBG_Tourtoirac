from unittest import loader

from user import User
from Components.board import Board

import uuid


class Game:
    def __init__(
            self,
            name: str,
            key: str,
            code: str,
            player: User,
            game_json: dict,
            ):
        self.key = key
        self.name = name
        self.code = code
        self.components = {
            "fixed": {
                "boards": []
            },
            "movable": [],
        }
        self.max_players = game_json["game"]["max_players"]
        self.max_watchers = game_json["game"]["max_watchers"]
        self.players = [player]
        self.watchers = []
        self.game_json = game_json
        self.load_game_components()

    def load_game_components(self):
        for component in self.game_json['board']:
            board = Board(component['id'], component['x'], component['y'], component['width'], component['height'], component['src'])
            self.components["fixed"]["boards"].append(board)

    def return_game_json(self) -> dict:
        """
        returns the description of the game
        :return: dict
        """
        game_json = {
            "key": self.key,
            "code": self.code,
            "max_players": self.max_players,
            "max_watchers": self.max_watchers,
            "players": f"{len(self.players)}/{self.max_players}",
            "watchers": f"{len(self.watchers)}/{self.max_watchers}",
            "components": {
                "fixed" : {
                    "boards": [board.return_json() for board in self.components["fixed"]["boards"]]
                }
            }
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

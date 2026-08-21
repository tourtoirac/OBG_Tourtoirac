import uuid
from game import Game

class Table:
    def __init__(
            self,
            game: Game,
            max_players: int=2,
            max_watchers: int=10,
    ):
        self.id = str(uuid.uuid4())
        self.max_players = max_players
        self.max_watchers = max_watchers
        self.players = []
        self.watchers = []
        self.game = game

    def add_player(self, player):
        """
        Adds a player to the table

        Returns True if the player was found,
        False if the table no longer has seats or if the player is already present
        """
        if player in self.players or player in self.watchers:
            return False
        if self.is_full():
            return False
        self.players.append(player)
        return True

    def add_watcher(self, watcher):
        """
        Adds a watcher to the table

        Returns True if the watcher was found,
        False if the table no longer has seats or if the watcher is already present
        """
        if watcher in self.players or watcher in self.watchers:
            return False
        if self.is_full():
            return False
        self.watchers.append(watcher)
        return True

    def remove_player(self, player):
        """
        Removes a player from the table.
        """
        if player in self.players:
            self.players.remove(player)

    def remove_watcher(self, watcher):
        """
        Removes a watcher from the table.
        """
        if watcher in self.watchers:
            self.watchers.remove(watcher)

    def is_full(self):
        return len(self.players) >= self.max_players

    def return_table_json(self):
        table_json = {
            "id":self.id,
            "players": f"{len(self.players)}/{self.max_players}",
            "watchers": f"{len(self.watchers)}/{self.max_watchers}",
        }
        return table_json

import uuid

from table import Table
from user import User


class Lobby:

    def __init__(self):
        self.tables = {}
        self.users = {}

    def return_lobby_json(self):
        """
        Returns a description of the lobby.
        """
        lobby_tables_list = []

        for table in self.tables:
            lobby_tables_list.append(
                self.tables[table].return_table_json()
            )

        return {
            "lobby": lobby_tables_list
        }

    def add_table(self, table: Table):
        if table.id not in self.tables:
            self.tables[table.id] = table

    def remove_table(self, table: Table):
        if table.id in self.tables:
            del self.tables[table.id]

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def delete_user(self, user: User):

        if user.protocol in self.users:

            if user.table_id is not None:
                self.tables[user.table_id].remove_user(user)

            del self.users[user.protocol]
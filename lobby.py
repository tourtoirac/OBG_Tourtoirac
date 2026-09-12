import json
import uuid

from chabanas import Chabanas
from session import Session
from user import User


class Lobby:

    def __init__(self, logger, chabanas):
        self.id = str(uuid.uuid4())
        self.sessions = {}
        self.users = {}
        self.logger = logger
        self.chabanas = chabanas

    def return_active_sessions(self, game_name_list, sat_list):
        """
        Returns a list of the active sessions of the lobby
        """
        return {
            "active": self.chabanas.get_active_sessions(game_name_list, sat_list),
        }

    def create_session(self, game_code: str, user: User, key: str = ""):
        # checks if a session can be created with that user
        session_info = self.chabanas.create_session(game_code, user, key)
        if session_info:
            session = Session(
                user=user,
                name=session_info['name'],
                key=session_info["key"],
                code=session_info["code"],
                active=session_info["active"],
                variant=session_info["variant"],
                game_json=session_info['game_json']
            )

            user.session = session

            self.add_session(session)
            self.sessions[session.key].add_user(user, "player")
            return True, None
        else:
            return False, "Unable to create session"

    def add_session(self, session: Session):
        if session.key not in self.sessions:
            self.sessions[session.key] = session

    def list_game(self, game_name_list: list):
        game_list = self.chabanas.get_game_list(game_name_list)
        if game_list:
            return game_list, None
        else:
            return False, "Unable to retrieve game information"

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def delete_user(self, user: User):
        if user.protocol in self.users:
            if user.session is not None and user.session.key is not None:
                self.sessions[user.session].remove_user(user)
            del self.users[user.protocol]

    def send_keep_alive(self):
        self.logger.debug("Sending keep alive message")
        message = {
            "event": "keep_alive"
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
                self.delete_user(user)
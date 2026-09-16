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

    def create_session(self, game_name: str, user: User, key: str = ""):
        # checks if a session can be created with that user
        session_info = self.chabanas.create_session(game_name, user, key)
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


    def join_session(self, session_code: str, user: User, key: str = ""):
        session = None
        self.logger.debug(f"Trying to find opened session with code {session_code}")

        for current_session in self.sessions:
            if current_session.code == session_code:
                session = current_session
                break

        if session is None:
            # checks if a user can join an already existing session
            self.logger.debug(f"Session with code {session_code} not found. Checking if it can be started")
            session_info = self.chabanas.join_session(session_code, user, key)
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
            else:
                return False, "Unable to join session"

        user.session = session

        self.add_session(session)
        self.sessions[session.key].add_user(user, "player")
        return True


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
                session = self.sessions.get(user.session.key)
                if session is not None:
                    session.remove_user(user)
                else:
                    self.logger.warning(
                        f"[DELETE_USER] Session {user.session.key} not found in lobby for user {user.name}"
                    )
            del self.users[user.protocol]

    def shutdown(self):
        """
        Notifies all connected users and disconnects them gracefully.
        Called by the reactor shutdown trigger.
        """
        self.logger.info("[SERVER] Shutdown initiated, notifying all users")
        message = {
            "event": "server_shutdown",
            "reason": "Server is shutting down"
        }
        encoded = json.dumps(message).encode("utf-8")
        for user in list(self.users.values()):
            try:
                user.protocol.sendMessage(
                    encoded,
                    isBinary=False
                )
                user.protocol.sendClose()
            except Exception as error:
                self.logger.error(
                    f"[SHUTDOWN] Error notifying user {user.name}: {error}"
                )
        self.users.clear()
        self.sessions.clear()
        self.logger.info("[SERVER] All users disconnected")

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
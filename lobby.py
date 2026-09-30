import json
import uuid

from twisted.internet import defer

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

    @defer.inlineCallbacks
    def return_active_sessions(self, game_name_list, sat_list):
        """
        Returns a list of the active sessions of the lobby
        """
        active = yield self.chabanas.get_active_sessions(game_name_list, sat_list)
        return {
            "active": active,
        }

    def _build_session(self, session_info):
        return Session(
            user=None,
            name=session_info['name'],
            key=session_info["key"],
            code=session_info["code"],
            active=session_info["active"],
            variant=session_info["variant"],
            game_json=session_info['game_json']
        )

    def _find_session_by_code(self, session_code):
        for session in self.sessions.values():
            if session.code == session_code:
                return session
        return None

    @defer.inlineCallbacks
    def create_session(
            self,
            game_name: str,
            user: User,
            key: str = "",
            allows_watchers: bool = True,
            session_min_players: int | None = None,
            session_max_players: int | None = None,
            access_key: str | None = None,
            variant_name: str | None = None
    ):
        # checks if a session can be created with that user
        session_info = yield self.chabanas.create_session(
            game_name,
            user,
            key,
            allows_watchers,
            session_min_players,
            session_max_players,
            access_key,
            variant_name
        )
        if not session_info:
            return False, "Unable to create session"

        session = self._build_session(session_info)
        self.add_session(session)

        success, reason = session.add_user(user, "player")
        if not success:
            self.remove_session(session)
            return False, reason

        user.session = session
        return True, None

    @defer.inlineCallbacks
    def join_session(self, session_code: str, user: User, key: str = "", role: str = "player"):
        self.logger.debug(f"Trying to find opened session with code {session_code}")

        session = self._find_session_by_code(session_code)

        if session is None:
            # checks if a user can join an already existing session
            self.logger.debug(f"Session with code {session_code} not found. Checking if it can be started")
            session_info = yield self.chabanas.join_session(session_code, user, key)
            if not session_info:
                return False, "Unable to join session"
            session = self._build_session(session_info)
            self.add_session(session)
        else:
            self.logger.debug(f"Session with code {session_code} found in lobby")

        success, reason = session.add_user(user, role)
        if not success:
            return False, reason

        user.session = session
        self._notify_lobby_users("join", session, user)
        return True, None

    def resume_session(self, session_key: str, user: User, role: str = "player"):
        """
        Rebinds a freshly connected user to a session it already belonged to from
        another page. The lobby and the game are two distinct documents, so the
        game page opens its own WebSocket and gets a brand new User with no
        session attached. It has to claim the session again by key, otherwise
        acquire/release/move are all rejected with "no_session".
        """
        session = self.sessions.get(session_key)
        if session is None:
            return False, "session_not_found"

        if user.session is session:
            return True, None

        # the same player may still be attached through a connection that has
        # not been torn down yet: the newest connection wins.
        previous = session.find_user(user.name)
        if previous is not None and previous is not user:
            self.logger.debug(
                f"[RESUME] Replacing stale connection of {user.name} on session {session.code}"
            )
            session.remove_user(previous)

        if role not in ("player", "watcher"):
            return False, "invalid_role"

        success, reason = session.add_user(user, role)
        if not success:
            return False, reason

        user.session = session
        self._notify_lobby_users("join", session, user)
        return True, None


    def _notify_lobby_users(self, event: str, session: Session, user: User):
        """
        Broadcasts a session membership change to every connected user so the
        lobby can refresh the seats available on each session.
        """
        message = {
            "event": "session_players_changed",
            "code": session.code,
            "game_name": session.name,
            "action": event,
            "nickname": user.name,
            "players": len(session.players),
            "max_players": session.max_players,
        }
        for lobby_user in list(self.users.values()):
            lobby_user.send(message)

    def add_session(self, session: Session):
        if session.key not in self.sessions:
            self.sessions[session.key] = session

    def remove_session(self, session: Session):
        if self.sessions.get(session.key) is session:
            del self.sessions[session.key]

    @defer.inlineCallbacks
    def list_game(self, game_name_list: list):
        game_list = yield self.chabanas.get_game_list(game_name_list)
        if game_list:
            return game_list, None
        else:
            return False, "Unable to retrieve game information"

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def _save_state_if_empty(self, session: Session):
        """
        Stores the current situation of the session as soon as nobody is left in
        it, so it can be resumed later on. The session is kept in the lobby:
        players coming back find the state they left.
        """
        if session.players or session.watchers:
            return
        if session.empty_since is not None:
            # already stored for this empty period
            return
        session.empty_since = True
        state = session.game_json_state()
        self.logger.info(
            f"[SESSION] {session.key} is now empty, storing its state "
            f"({len(state['movable'])} movable components)"
        )
        deferred = self.chabanas.update_session_state(session.key, state)
        deferred.addErrback(
            self.chabanas._log_failure,
            f"[LOBBY] Storing the state of session {session.key}"
        )

    def delete_user(self, user: User):
        if user is None:
            return
        if user.protocol in self.users:
            if user.session is not None and user.session.key is not None:
                session = self.sessions.get(user.session.key)
                if session is not None:
                    session.remove_user(user)
                    self._notify_lobby_users("leave", session, user)
                    self._save_state_if_empty(session)
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
        # delete_user() below mutates self.users, so iterate over a copy
        for user in list(self.users.values()):
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

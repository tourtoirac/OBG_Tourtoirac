import json
import logging

import pytest
from twisted.internet import defer

from chabanas import Chabanas
from lobby import Lobby
from session import Session
from user import User

LOGGER = logging.getLogger("tests")


class FakeProtocol:
    """Stands in for an autobahn connection."""

    def __init__(self, raise_on_send=False):
        self.sent = []
        self.closed = False
        self._raise_on_send = raise_on_send

    def sendMessage(self, payload, isBinary=False):
        if self._raise_on_send:
            raise Exception("connection already closed")
        self.sent.append(payload)

    def is_connection_open(self):
        return not self.closed

    def sendClose(self):
        self.closed = True

    @property
    def events(self):
        return [msg["event"] for msg in self.sent if isinstance(msg, dict) and "event" in msg]


class FakeChabanas(Chabanas):
    """Back-end stub. Every method returns an already-fired Deferred."""

    def __init__(self, info):
        super().__init__(LOGGER)
        self.info = info
        self.available = True
        self.calls = []
        self._agent = object()  # prevents building a real Agent

    def _post_json(self, path, payload):
        self.calls.append(path)
        if not self.available:
            return defer.succeed(False)
        if path == "/session/create":
            return defer.succeed({"session_code": self.info["code"]})
        if path in ("/session/get", "/session/join"):
            return defer.succeed({"session": self.info})
        if path == "/session/list":
            return defer.succeed({"sessions": {}})
        if path == "/game/list":
            return defer.succeed({"game_list": [{"name": self.info["name"]}]})
        return defer.succeed({})


@pytest.fixture
def lobby(session_info):
    return Lobby(LOGGER, FakeChabanas(session_info))


def connect(lobby, name):
    protocol = FakeProtocol()
    user = User(name=name, protocol=protocol)
    lobby.add_user(user)
    return user


class TestCreateSession:
    def test_creates_and_registers_the_session(self, lobby, sync):
        user = connect(lobby, "alice")

        success, error = sync(lobby.create_session("Waterloo", user, "key"))

        assert success is True
        assert error is None
        assert user.session is not None
        assert lobby.sessions[user.session.key] is user.session
        assert [u.name for u in user.session.players] == ["alice"]

    def test_components_keep_their_ids(self, lobby, sync):
        """Regression guard for bugs 1 and 2, through the full session build."""
        user = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", user, "key"))

        described = user.session.return_session_json()
        assert described["components"]["fixed"][0]["id"] == "b1"
        assert described["components"]["movable"][0]["id"] == "t1"

    def test_back_end_failure_is_reported_without_creating_a_session(self, lobby, sync):
        lobby.chabanas.available = False
        user = connect(lobby, "alice")

        success, error = sync(lobby.create_session("Waterloo", user, "key"))

        assert success is False
        assert error == "Unable to create session"
        assert user.session is None
        assert lobby.sessions == {}


class TestJoinSession:
    def test_finds_a_session_already_in_the_lobby(self, lobby, sync, session_info):
        """
        Bug 3: join_session iterated self.sessions, which is a dict keyed by
               session.key, so it iterated str keys and raised
               AttributeError: 'str' object has no attribute 'code'.
        """
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")

        success, error = sync(lobby.join_session(session_info["code"], guest, "", "player"))

        assert success is True
        assert error is None
        assert guest.session is host.session, "la session du lobby doit etre reutilisee"
        assert lobby.chabanas.calls == ["/session/create", "/session/get"], (
            "aucun appel back-end n'etait necessaire"
        )

    def test_queries_the_back_end_for_an_unknown_code(self, lobby, sync):
        user = connect(lobby, "alice")
        success, _ = sync(lobby.join_session("UNKNOWN", user, "", "player"))
        assert success is True
        assert "/session/join" in lobby.chabanas.calls

    def test_back_end_failure_is_reported(self, lobby, sync):
        # code absent du lobby : le back-end est sollicite et echoue
        lobby.chabanas.available = False
        guest = connect(lobby, "bob")

        success, error = sync(lobby.join_session("UNKNOWN", guest, "", "player"))

        assert success is False
        assert error == "Unable to join session"
        assert guest.session is None

    def test_no_back_end_call_when_the_session_is_cached(self, lobby, sync, session_info):
        """The point of the lobby cache: joining a known code never hits the back-end."""
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.chabanas.calls.clear()
        lobby.chabanas.available = False
        guest = connect(lobby, "bob")

        success, _ = sync(lobby.join_session(session_info["code"], guest, "", "player"))

        assert success is True
        assert lobby.chabanas.calls == []


class TestCapacityAndRoles:
    """Bug 4: the return value of add_user was discarded, so a client was told
    it had joined a full session, and the `role` field was ignored."""

    @pytest.fixture
    def full_lobby(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        return lobby, host, session_info

    def test_max_players_is_enforced(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        second = connect(lobby, "bob")
        assert sync(lobby.join_session(session_info["code"], second, "", "player"))[0] is True

        third = connect(lobby, "carol")
        success, error = sync(lobby.join_session(session_info["code"], third, "", "player"))

        assert success is False
        assert error == "Session is full (2 players)"
        assert third.session is None, "un utilisateur refuse ne doit pas avoir de session"
        assert len(host.session.players) == 2

    def test_watchers_are_accepted_up_to_max_watchers(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        watcher = connect(lobby, "watcher")

        success, error = sync(lobby.join_session(session_info["code"], watcher, "", "watcher"))

        assert success is True
        assert error is None
        assert [u.name for u in host.session.watchers] == ["watcher"]
        assert host.session.return_session_json()["watchers"] == "1/1"

    def test_max_watchers_is_enforced(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        sync(lobby.join_session(session_info["code"], connect(lobby, "w1"), "", "watcher"))
        second = connect(lobby, "w2")

        success, error = sync(lobby.join_session(session_info["code"], second, "", "watcher"))

        assert success is False
        assert error == "Session is full (1 watchers)"

    def test_watchers_do_not_consume_player_seats(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        sync(lobby.join_session(session_info["code"], connect(lobby, "w1"), "", "watcher"))

        player = connect(lobby, "bob")
        success, _ = sync(lobby.join_session(session_info["code"], player, "", "player"))

        assert success is True, "un watcher ne doit pas occuper un siege de joueur"

    def test_unknown_role_is_refused(self, full_lobby, sync, session_info):
        lobby, _, _ = full_lobby
        intruder = connect(lobby, "mallory")

        success, error = sync(lobby.join_session(session_info["code"], intruder, "", "admin"))

        assert success is False
        assert error == "Invalid role 'admin'"

    def test_joining_twice_is_refused(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        success, error = sync(lobby.join_session(session_info["code"], host, "", "player"))

        assert success is False
        assert error == "User is already in this session"


class TestSessionBasics:
    def test_add_user_returns_a_reason(self):
        session = Session(None, "n", "k", "c", True, "std", {
            "game": {"max_players": 1, "max_watchers": 1},
            "fixed": [], "movable": [],
        })
        first, second = User(name="a", protocol=None), User(name="b", protocol=None)

        assert session.add_user(first, "player") == (True, None)
        ok, reason = session.add_user(second, "player")
        assert ok is False and "full" in reason

    def test_remove_user_works_for_players_and_watchers(self):
        session = Session(None, "n", "k", "c", True, "std", {
            "game": {"max_players": 2, "max_watchers": 2},
            "fixed": [], "movable": [],
        })
        player = User(name="a", protocol=None)
        watcher = User(name="w", protocol=None)
        session.add_user(player, "player")
        session.add_user(watcher, "watcher")

        session.remove_user(player)
        session.remove_user(watcher)

        assert session.players == []
        assert session.watchers == []


class TestKeepAlive:
    """
    Bug 6: send_keep_alive deleted users while iterating self.users.values(),
           raising RuntimeError: dictionary changed size during iteration and
           stopping the reactor.
    """

    def test_keeps_sending_to_live_users(self, lobby):
        alive = connect(lobby, "alive")

        lobby.send_keep_alive()

        assert len(alive.protocol.sent) == 1
        assert b"keep_alive" in alive.protocol.sent[0]

    def test_removes_dead_users_without_raising(self, lobby):
        alive = connect(lobby, "alive")
        dead = connect(lobby, "dead")
        dead.protocol._raise_on_send = True

        lobby.send_keep_alive()  # must not raise

        assert dead.protocol not in lobby.users
        assert alive.protocol in lobby.users
        assert len(alive.protocol.sent) == 1, "les autres clients recoivent le message"

    def test_handles_several_dead_users(self, lobby):
        alive = connect(lobby, "alive")
        for index in range(5):
            zombie = connect(lobby, f"zombie{index}")
            zombie.protocol._raise_on_send = True

        lobby.send_keep_alive()

        assert len(lobby.users) == 1
        assert alive.protocol in lobby.users


class TestDeleteUser:
    """Bug 7: shutdown() clears the user registry before the close handshakes
    finish, so onClose() then called delete_user(None)."""

    def test_delete_user_accepts_none(self, lobby):
        lobby.delete_user(None)  # must not raise

    def test_delete_user_removes_from_session_and_registry(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")
        sync(lobby.join_session(session_info["code"], guest, "", "player"))

        lobby.delete_user(guest)

        assert guest.protocol not in lobby.users
        assert [u.name for u in host.session.players] == ["alice"]

    def test_delete_user_twice_is_harmless(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))

        lobby.delete_user(host)
        lobby.delete_user(host)


class TestLobbyNotifications:
    """A lobby client must learn about arrivals and departures without
    polling, so it can hide a Join button once a session is full."""

    def test_join_is_broadcast_to_every_connected_user(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        watcher = connect(lobby, "eve")

        sync(lobby.join_session(session_info["code"], connect(lobby, "bob"), "", "player"))

        assert json.loads(watcher.protocol.sent[-1])["event"] == "session_players_changed"
        assert json.loads(host.protocol.sent[-1])["event"] == "session_players_changed"

    def test_broadcast_reports_the_seat_count(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        eve = connect(lobby, "eve")

        sync(lobby.join_session(session_info["code"], connect(lobby, "bob"), "", "player"))

        payload = json.loads(eve.protocol.sent[-1])
        assert payload["event"] == "session_players_changed"
        assert payload["action"] == "join"
        assert payload["nickname"] == "bob"
        assert payload["code"] == session_info["code"]
        assert payload["players"] == 2
        assert payload["max_players"] == host.session.max_players

    def test_leave_is_broadcast(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")
        sync(lobby.join_session(session_info["code"], guest, "", "player"))
        eve = connect(lobby, "eve")
        eve.protocol.sent.clear()

        lobby.delete_user(guest)

        payload = json.loads(eve.protocol.sent[-1])
        assert payload["action"] == "leave"
        assert payload["nickname"] == "bob"
        assert payload["players"] == 1

    def test_no_broadcast_when_the_join_is_refused(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        eve = connect(lobby, "eve")
        eve.protocol.sent.clear()

        success, _ = sync(lobby.join_session(session_info["code"], host, "", "player"))

        assert success is False
        assert eve.protocol.sent == []


class TestShutdown:
    def test_clears_registry_and_notifies_everyone(self, lobby, sync):
        first = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", first, ""))
        second = connect(lobby, "bob")

        lobby.shutdown()

        assert lobby.users == {}
        assert lobby.sessions == {}
        assert b"server_shutdown" in first.protocol.sent[0]
        assert b"server_shutdown" in second.protocol.sent[0]
        assert first.protocol.closed and second.protocol.closed

    def test_keep_alive_after_shutdown_is_harmless(self, lobby, sync):
        first = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", first, ""))
        lobby.shutdown()

        lobby.send_keep_alive()  # must not raise

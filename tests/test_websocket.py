"""
Tests of the WebSocket layer: message parsing, dispatch and lifecycle.

autobahn is required to import main. The module is skipped when the
dependency is not installed rather than failing the whole run.
"""
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from twisted.internet import defer  # noqa: E402

import handle_message as HM  # noqa: E402,F401
import main as main_module  # noqa: E402
from chabanas import Chabanas  # noqa: E402
from lobby import Lobby  # noqa: E402
from main import GameWebSocketFactory, GameWebSocketProtocol  # noqa: E402
from user import User  # noqa: E402

LOGGER = logging.getLogger("tests")


class FakeChabanas(Chabanas):
    def __init__(self, info):
        super().__init__(LOGGER)
        self.info = info
        self.calls = []
        self._agent = object()

    def _post_json(self, path, payload, with_status=False):
        self.calls.append(path)
        if path == "/session/create":
            body = {"session_code": self.info["code"]}
        elif path in ("/session/get", "/session/join"):
            if payload.get("session_code") not in (self.info["code"], "CODE1"):
                body = False  # the back-end refuses unknown codes
            else:
                body = {"session": self.info}
        elif path == "/session/list":
            body = {"sessions": {}}
        elif path == "/game/list":
            body = {"game_list": [{"name": self.info["name"]}]}
        else:
            body = {}
        if with_status:
            return defer.succeed((404 if body is False else 200, body))
        return defer.succeed(body)


class Protocol(GameWebSocketProtocol):
    """Concrete protocol that records what is sent, instead of using a socket."""

    def __init__(self, lobby):
        self.sent = []
        self.closed = False
        self.state = self.STATE_OPEN
        self.factory = GameWebSocketFactory("ws://localhost:9000", lobby)

    def sendMessage(self, payload, isBinary=False):
        self.sent.append(json.loads(payload.decode()))

    def sendClose(self):
        self.closed = True
        self.state = self.STATE_CLOSED

    # helpers -----------------------------------------------------------
    @property
    def last(self):
        return self.sent[-1] if self.sent else None

    def last_event(self):
        return self.last.get("event") if self.last else None

    def last_error(self):
        return self.last.get("error", {}).get("code") if self.last else None

    def clear(self):
        self.sent.clear()


@pytest.fixture
def lobby(session_info):
    return Lobby(LOGGER, FakeChabanas(session_info))


@pytest.fixture
def client(lobby):
    protocol = Protocol(lobby)
    lobby.add_user(User(name="anonymous", protocol=protocol))
    return protocol


def receive(protocol, message):
    protocol.onMessage(json.dumps(message).encode(), False)


class TestMessageParsing:
    def test_invalid_json(self, client):
        client.onMessage(b"pas du json", False)
        assert client.last_error() == "invalid_json"

    def test_binary_payloads_are_rejected(self, client):
        client.onMessage(b"\x00\x01", True)
        assert client.last_error() == "binary_not_supported"

    @pytest.mark.parametrize("payload", [b"[1, 2, 3]", b'"chaine"', b"42", b"null"])
    def test_json_that_is_not_an_object(self, client, payload):
        """
        json.loads('[1,2,3]') succeeds, then message.get('action') raised
        AttributeError and dropped the connection.
        """
        client.onMessage(payload, False)
        assert client.last_error() == "invalid_message"

    def test_unknown_action(self, client):
        receive(client, {"action": "fly_to_the_moon"})
        assert client.last_error() == "unknown_action"

    def test_missing_action(self, client):
        receive(client, {"no_action": True})
        assert client.last_error() == "unknown_action"


class TestDispatch:
    def test_create_then_join(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        assert client.last_event() == "session_created"
        assert client.last["session"]["components"]["movable"][0]["id"] == "t1"

        other = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=other))
        receive(other, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "bob", "key": "", "role": "player",
        })
        assert other.last_event() == "session_joined"
        assert other.last["game"]["players"] == "2/2"

    def test_list_sessions(self, client):
        receive(client, {"action": "list_sessions"})
        assert client.last_event() == "sessions_info"

    def test_list_game(self, client):
        receive(client, {"action": "list_game"})
        assert client.last_event() == "list_game"
        assert client.last["game_list"] == [{"name": "Waterloo"}]

    def test_create_session_reports_a_missing_field(self, client):
        receive(client, {"action": "create_session", "game_name": "Waterloo"})
        assert client.last_error() == "missing_field"

    def test_join_session_reports_a_missing_role(self, client):
        receive(client, {
            "action": "join_session", "session_code": "X",
            "nickname": "bob", "key": "",
        })
        assert client.last_error() == "missing_field"

    def test_join_session_accepts_a_player_without_a_key(self, lobby, client, session_info):
        """
        The user key is optional: the message is forwarded to the back-end,
        which is the only place entitled to open a seat.
        """
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        peer = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=peer))
        receive(peer, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "bob", "role": "player",
        })

        assert peer.last_event() == "session_joined"
        assert peer.last["role"] == "player"
        assert "/session/join" in lobby.chabanas.calls

    def test_watcher_joins_without_a_key(self, lobby, client, session_info):
        """
        A spectator has no key: only a nickname and, when the creator set one,
        the access key. Rejecting the message for a missing key would make the
        button unusable.
        """
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })

        watcher_proto = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher_proto))
        receive(watcher_proto, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "carol", "role": "watcher",
        })

        assert watcher_proto.last_event() == "session_joined"
        assert watcher_proto.last["role"] == "watcher"
        watcher = lobby.get_user(watcher_proto)
        assert watcher.acquired == []
        assert [u.name for u in watcher.session.players] == ["alice"], (
            "un spectateur ne doit pas occuper un siege de joueur"
        )

    def test_watcher_cannot_acquire(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        watcher_proto = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher_proto))
        receive(watcher_proto, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "carol", "role": "watcher",
        })
        watcher = lobby.get_user(watcher_proto)
        watcher_proto.clear()

        receive(watcher_proto, {"action": "acquire", "component_id": "t1"})

        assert watcher_proto.last_error() == "watcher_not_allowed"
        assert watcher.acquired == [], "un spectateur ne prend rien en main"
        token = watcher.session.get_component("t1")
        assert token.acquired_by is None, "le jeton reste disponible"

    def test_watcher_cannot_move_or_release(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        watcher_proto = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher_proto))
        receive(watcher_proto, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "carol", "role": "watcher",
        })
        watcher = lobby.get_user(watcher_proto)

        receive(watcher_proto, {"action": "move", "component_id": "t1", "x": 42, "y": 24})
        assert watcher_proto.last_error() == "watcher_not_allowed"
        assert (watcher.session.get_component("t1").x) == 1

        watcher_proto.clear()
        receive(watcher_proto, {"action": "release", "component_id": "t1"})
        assert watcher_proto.last_error() == "watcher_not_allowed"

    def test_fix_positions_stays_reserved_for_players(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        watcher_proto = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher_proto))
        receive(watcher_proto, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "carol", "role": "watcher",
        })

        receive(watcher_proto, {"action": "fix_positions"})

        assert watcher_proto.last_error() == "watcher_not_allowed"

    def test_join_session_rejects_an_unknown_role(self, client):
        receive(client, {
            "action": "join_session", "session_code": "X",
            "nickname": "mallory", "key": "", "role": "admin",
        })
        assert client.last_error() == "invalid_role"

    def test_join_session_error_is_well_formed(self, client):
        """
        The failure path called send_error() with a single argument, raising
        TypeError instead of reporting the problem.
        """
        receive(client, {
            "action": "join_session", "session_code": "INCONNU",
            "nickname": "bob", "key": "", "role": "player",
        })
        assert client.last["event"] == "error"
        assert client.last_error() == "Unable to join session"
        assert isinstance(client.last["error"]["message"], str)


class TestComponentActions:
    """
    Bug 8: acquire/release/move indexed message['component_id'] and called
           user.session.get_component() with no validation, so a KeyError or a
           None session killed the connection.
    """

    @pytest.fixture
    def in_session(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        client.clear()
        return client, lobby.get_user(client)

    @pytest.mark.parametrize("action,message,expected", [
        ("acquire", {"x": 1}, "missing_field"),
        ("release", {"x": 1}, "missing_field"),
        ("move", {"component_id": "t1", "x": 1}, "missing_field"),
        ("move", {"component_id": "t1", "y": 1}, "missing_field"),
        ("acquire", {"component_id": "nope"}, "component_not_found"),
    ])
    def test_invalid_component_messages(self, in_session, action, message, expected):
        client, _ = in_session
        receive(client, {"action": action, **message})
        assert client.last_error() == expected

    @pytest.mark.parametrize("action", ["acquire", "release", "move"])
    def test_actions_require_a_session(self, lobby, client, action):
        message = {"component_id": "t1", "x": 1, "y": 2}
        receive(client, {"action": action, **message})
        assert client.last_error() == "no_session"

    def test_acquire_then_release_round_trip(self, in_session):
        client, user = in_session

        receive(client, {"action": "acquire", "component_id": "t1"})
        assert client.last_event() == "acquire"
        assert client.last["success"] is True
        assert user.acquired == ["t1"]

        receive(client, {"action": "release", "component_id": "t1"})
        assert client.last_event() == "release"
        assert client.last["success"] is True
        assert user.acquired == []

    def test_a_second_user_cannot_steal_a_component(self, lobby, in_session, session_info):
        client, alice = in_session
        receive(client, {"action": "acquire", "component_id": "t1"})

        bob_proto = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=bob_proto))
        receive(bob_proto, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "bob", "key": "", "role": "player",
        })
        bob = lobby.get_user(bob_proto)
        bob_proto.clear()

        receive(bob_proto, {"action": "acquire", "component_id": "t1"})

        assert bob_proto.last["success"] is False, "un acquire refuse doit etre signale"
        assert bob.acquired == []
        assert alice.acquired == ["t1"], "alice reste detentrice"

    def test_move_is_broadcast_to_the_others_only(self, lobby, in_session, session_info):
        client, alice = in_session
        peer = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=peer))
        receive(peer, {
            "action": "join_session", "session_code": session_info["code"],
            "nickname": "bob", "key": "", "role": "player",
        })
        peer.clear()
        client.clear()

        receive(client, {"action": "acquire", "component_id": "t1"})
        client.clear()
        peer.clear()
        receive(client, {"action": "move", "component_id": "t1", "x": 42, "y": 24})

        assert peer.last_event() == "move", "les autres recoivent le deplacement"
        assert client.sent == [], "l'emetteur ne recoit pas d'echo"
        token = alice.session.get_component("t1")
        assert (token.x, token.y) == (42, 24)

    def test_move_without_acquiring_is_ignored(self, in_session):
        client, alice = in_session
        receive(client, {"action": "move", "component_id": "t1", "x": 7, "y": 8})

        token = alice.session.get_component("t1")
        assert (token.x, token.y) == (1, 2), "un deplacement sans acquire est ignore"


class TestErrorReporting:
    def test_send_error_is_ignored_on_a_closed_connection(self, client):
        client.state = client.STATE_CLOSED
        client.send_error("code", "message")  # must not raise
        assert client.sent == []

    def test_user_send_is_ignored_on_a_closed_connection(self, client, lobby):
        user = lobby.get_user(client)
        client.state = client.STATE_CLOSED
        user.send({"event": "anything"})  # must not raise
        assert client.sent == []

    def test_an_exception_in_a_handler_is_reported_not_fatal(self, client, monkeypatch):
        def exploding(*args, **kwargs):
            raise RuntimeError("boom")

        # main imports the handlers by name, so patch them where they are used
        monkeypatch.setattr(main_module, "list_game", exploding)
        client.onMessage(
            json.dumps({"action": "list_game"}).encode(),
            False,
        )

        assert client.last_error() == "internal_error"
        assert client.closed is False, "la connexion doit survivre"


class TestConnectionLifecycle:
    def test_on_close_removes_the_user(self, lobby, client, session_info):
        receive(client, {
            "action": "create_session", "game_name": "Waterloo",
            "nickname": "alice", "key": "",
        })
        user = lobby.get_user(client)
        assert user.session is not None

        client.onClose(True, 1000, "normal")

        assert client not in lobby.users
        assert lobby.sessions[user.session.key].players == []

    def test_on_close_after_shutdown(self, lobby, client):
        """
        Bug 7: shutdown() clears the user registry, so the onClose triggered by
        the close handshake then called delete_user(None) -> AttributeError.
        """
        lobby.shutdown()
        client.onClose(True, 1001, "going away")  # must not raise
        client.onClose(True, 1001, "going away")  # and must be idempotent

    def test_on_close_for_an_unknown_connection(self, lobby, client):
        stranger = Protocol(lobby)
        stranger.onClose(True, 1000, "never connected")  # must not raise

    def test_shutdown_notifies_every_client(self, lobby, client):
        lobby.shutdown()

        assert client.last_event() == "server_shutdown"
        assert client.closed is True
        assert lobby.users == {}

    def test_is_connection_open_follows_the_websocket_state(self, client):
        """
        The connection state is the one autobahn keeps: isClosed() no longer
        exists on WebSocketServerProtocol.
        """
        assert client.is_connection_open() is True
        client.state = client.STATE_CLOSING
        assert client.is_connection_open() is False
        client.state = client.STATE_OPEN
        client.onClose(True, 1000, "normal")
        client.state = client.STATE_CLOSED
        assert client.is_connection_open() is False

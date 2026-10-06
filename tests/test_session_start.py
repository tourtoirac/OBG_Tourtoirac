"""
Tests du demarrage d'une partie : qui peut la demarrer, ce que le demarrage
ferme, et quand un pion peut etre pris en main.
"""
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from conftest import SESSION_INFO  # noqa: E402
from twisted.internet import defer  # noqa: E402

from chabanas import Chabanas  # noqa: E402
from lobby import Lobby  # noqa: E402
from main import GameWebSocketFactory, GameWebSocketProtocol  # noqa: E402
from user import User  # noqa: E402

LOGGER = logging.getLogger("tests")


class FakeChabanas(Chabanas):
    """
    Back-end qui tient les sieges et le demarrage comme Chabanas : le dernier
    siege pris demarre la partie, une partie demarree refuse tout nouveau
    joueur, et /session/update ne fait que passer started a True.
    """

    def __init__(self, max_players=3, update_ok=True):
        super().__init__(LOGGER)
        self.info = json.loads(json.dumps(SESSION_INFO))
        self.info["started"] = False
        self.info["players"] = []
        self.info["game_json"]["game"]["max_players"] = max_players
        self.max_players = max_players
        self.update_ok = update_ok
        self.payloads = []
        self._agent = object()

    def _seat(self, nickname, owner=False):
        if nickname not in [p["nickname"] for p in self.info["players"]]:
            self.info["players"].append({"nickname": nickname, "owner": owner})
        if len(self.info["players"]) >= self.max_players:
            self.info["started"] = True

    def _post_json(self, path, payload, with_status=False):
        self.payloads.append((path, payload))
        status, body = 200, {}
        if path == "/session/create":
            self._seat(payload["nickname"], owner=True)
            body = {"session_code": self.info["code"]}
        elif path == "/session/get":
            body = {"session": self.info}
        elif path == "/session/join":
            nickname = payload["nickname"]
            seated = nickname in [p["nickname"] for p in self.info["players"]]
            if payload.get("role") == "watcher" or seated:
                body = self.info
            elif self.info["started"]:
                status, body = 409, False
            else:
                self._seat(nickname)
                body = self.info
        elif path == "/session/update":
            if "game_json" in payload:
                self.info["game_json"] = payload["game_json"]
            if payload.get("started") is True:
                self.info["started"] = True
            body = {"status": "success"} if self.update_ok else False
        body = json.loads(json.dumps(body))
        return defer.succeed((status, body) if with_status else body)

    def update_payloads(self):
        return [payload for path, payload in self.payloads if path == "/session/update"]


class Protocol(GameWebSocketProtocol):
    """Protocole concret qui garde ce qui est envoye, au lieu d'un socket."""

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

    def last_error(self):
        errors = [m for m in self.sent if m.get("event") == "error"]
        return errors[-1]["error"]["code"] if errors else None

    def events(self, name):
        return [m for m in self.sent if m.get("event") == name]

    def last_status(self):
        statuses = self.events("session_status")
        return statuses[-1] if statuses else None


def receive(protocol, message):
    protocol.onMessage(json.dumps(message).encode(), False)


def connect(lobby):
    protocol = Protocol(lobby)
    lobby.add_user(User(name="Anonymous", protocol=protocol))
    return protocol


def create(lobby, nickname="alice"):
    protocol = connect(lobby)
    receive(protocol, {
        "action": "create_session", "game_name": "Waterloo",
        "nickname": nickname, "key": "",
    })
    assert protocol.events("session_joined"), protocol.sent
    return protocol


def join(lobby, nickname, role="player"):
    protocol = connect(lobby)
    receive(protocol, {
        "action": "join_session", "session_code": SESSION_INFO["code"],
        "nickname": nickname, "key": "", "role": role,
    })
    return protocol


def resume(lobby, nickname, role="player"):
    protocol = connect(lobby)
    receive(protocol, {
        "action": "resume_session", "session_key": SESSION_INFO["key"],
        "session_code": SESSION_INFO["code"], "nickname": nickname, "role": role,
    })
    return protocol


def acquire(protocol):
    receive(protocol, {"action": "acquire", "component_id": "t1"})


@pytest.fixture
def chabanas():
    return FakeChabanas(max_players=3)


@pytest.fixture
def lobby(chabanas):
    return Lobby(LOGGER, chabanas)


def session_of(lobby):
    return lobby.sessions[SESSION_INFO["key"]]


class TestBeforeTheStart:
    def test_a_new_session_is_not_started(self, lobby):
        alice = create(lobby)

        assert session_of(lobby).started is False
        assert alice.events("session_joined")[-1]["session"]["started"] is False

    def test_nobody_takes_a_token_before_the_start(self, lobby):
        alice = create(lobby)

        acquire(alice)

        assert alice.last_error() == "session_not_started"
        assert alice.events("acquire") == []
        assert session_of(lobby).components_dict["t1"].acquired_by is None

    def test_new_players_may_join(self, lobby):
        create(lobby)

        bob = join(lobby, "bob")

        assert bob.events("session_joined")
        assert session_of(lobby).player_names == ["alice", "bob"]


class TestStartButton:
    def test_the_owner_starts_the_session(self, lobby, chabanas):
        alice = create(lobby)
        bob = join(lobby, "bob")

        receive(alice, {"action": "start_session"})

        assert session_of(lobby).started is True
        assert {"key": SESSION_INFO["key"], "started": True} in chabanas.update_payloads()
        for protocol in (alice, bob):
            assert protocol.last_status() == {
                "event": "session_status", "started": True, "missing_players": [],
            }

    def test_the_lobby_hears_about_the_start(self, lobby):
        alice = create(lobby)
        onlooker = connect(lobby)

        receive(alice, {"action": "start_session"})

        changes = onlooker.events("session_players_changed")
        assert changes and changes[-1]["action"] == "started"

    def test_another_player_cannot_start_the_session(self, lobby, chabanas):
        create(lobby)
        bob = join(lobby, "bob")

        receive(bob, {"action": "start_session"})

        assert bob.last_error() == "not_session_owner"
        assert session_of(lobby).started is False
        assert chabanas.update_payloads() == []

    def test_a_watcher_cannot_start_the_session(self, lobby, chabanas):
        create(lobby)
        eve = join(lobby, "eve", role="watcher")

        receive(eve, {"action": "start_session"})

        assert eve.last_error() == "watcher_not_allowed"
        assert session_of(lobby).started is False
        assert chabanas.update_payloads() == []

    def test_a_started_session_cannot_be_started_again(self, lobby):
        alice = create(lobby)
        receive(alice, {"action": "start_session"})

        receive(alice, {"action": "start_session"})

        assert alice.last_error() == "session_already_started"

    def test_nothing_starts_when_chabanas_does_not_store_it(self, lobby, chabanas):
        chabanas.update_ok = False
        alice = create(lobby)

        receive(alice, {"action": "start_session"})

        assert alice.last_error() == "session_start_not_stored"
        assert session_of(lobby).started is False

    def test_tokens_are_free_once_started(self, lobby):
        alice = create(lobby)
        receive(alice, {"action": "start_session"})

        acquire(alice)

        assert alice.events("acquire")[-1]["success"] is True


class TestStartedSessionIsClosedToNewPlayers:
    def test_a_new_player_is_refused(self, lobby):
        alice = create(lobby)
        receive(alice, {"action": "start_session"})

        carol = join(lobby, "carol")

        assert carol.last_error() == "session_unavailable"
        assert session_of(lobby).player_names == ["alice"]

    def test_a_watcher_may_still_come(self, lobby):
        alice = create(lobby)
        receive(alice, {"action": "start_session"})

        eve = join(lobby, "eve", role="watcher")

        assert eve.events("session_joined")

    def test_resume_refuses_a_nickname_without_a_seat(self, lobby):
        alice = create(lobby)
        receive(alice, {"action": "start_session"})

        mallory = resume(lobby, "mallory")

        assert mallory.last_error() == "session_started"


class TestAutomaticStart:
    def test_the_last_seat_starts_the_session(self, lobby):
        alice = create(lobby)
        join(lobby, "bob")
        carol = join(lobby, "carol")  # 3 sieges sur 3

        assert session_of(lobby).started is True
        assert carol.last_status()["started"] is True
        assert alice.last_status()["started"] is True

    def test_a_free_seat_left_does_not_start(self, lobby):
        create(lobby)
        join(lobby, "bob")

        assert session_of(lobby).started is False

    def test_a_one_seat_session_starts_at_creation(self):
        lobby = Lobby(LOGGER, FakeChabanas(max_players=1))

        alice = create(lobby)

        assert session_of(lobby).started is True
        acquire(alice)
        assert alice.events("acquire")[-1]["success"] is True


class TestEveryPlayerMustBeAtTheTable:
    def start_with(self, lobby, *others):
        alice = create(lobby)
        players = [alice] + [join(lobby, name) for name in others]
        receive(alice, {"action": "start_session"})
        return players

    def test_a_disconnected_player_blocks_acquire(self, lobby):
        alice, bob = self.start_with(lobby, "bob")

        lobby.delete_user(lobby.get_user(bob))
        acquire(alice)

        assert alice.last_error() == "players_missing"
        assert alice.last_status()["missing_players"] == ["bob"]

    def test_the_player_coming_back_frees_the_tokens(self, lobby):
        alice, bob = self.start_with(lobby, "bob")
        lobby.delete_user(lobby.get_user(bob))

        bob_again = resume(lobby, "bob")
        acquire(alice)

        assert bob_again.events("session_joined")
        assert alice.last_status()["missing_players"] == []
        assert alice.events("acquire")[-1]["success"] is True

    def test_a_watcher_does_not_count_as_a_player(self, lobby):
        alice, bob = self.start_with(lobby, "bob")
        lobby.delete_user(lobby.get_user(bob))

        join(lobby, "bob", role="watcher")
        acquire(alice)

        assert alice.last_error() == "players_missing"

    def test_a_session_cut_and_resumed_keeps_started(self, lobby, chabanas):
        alice, bob = self.start_with(lobby, "bob")
        # tout le monde part : la session est stockee puis retiree de la memoire
        lobby.delete_user(lobby.get_user(alice))
        lobby.delete_user(lobby.get_user(bob))
        assert SESSION_INFO["key"] not in lobby.sessions

        alice_again = resume(lobby, "alice")

        rebuilt = session_of(lobby)
        assert rebuilt.started is True
        assert rebuilt.player_names == ["alice", "bob"]
        acquire(alice_again)
        assert alice_again.last_error() == "players_missing"

        resume(lobby, "bob")
        acquire(alice_again)
        assert alice_again.events("acquire")[-1]["success"] is True

    def test_a_session_cut_before_the_start_stays_not_started(self, lobby):
        alice = create(lobby)
        lobby.delete_user(lobby.get_user(alice))

        alice_again = resume(lobby, "alice")

        assert session_of(lobby).started is False
        acquire(alice_again)
        assert alice_again.last_error() == "session_not_started"

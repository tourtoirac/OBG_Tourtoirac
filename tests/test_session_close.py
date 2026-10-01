"""
Tests of the game closure: who may archive a session, in what order the
back-end is called, and what the players still connected become.
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

OWNER = "alice"


def session_info(owner=OWNER, max_players=4, max_watchers=1):
    """The shared session description, with the player list Chabanas returns."""
    info = json.loads(json.dumps(SESSION_INFO))
    info["game_json"]["game"]["max_players"] = max_players
    info["game_json"]["game"]["max_watchers"] = max_watchers
    info["players"] = [{"nickname": owner, "owner": True}]
    return info


class FakeChabanas(Chabanas):
    def __init__(self, info, archive_status=200, update_body=None):
        super().__init__(LOGGER)
        self.info = info
        self.calls = []
        self.archive_status = archive_status
        self.update_body = {"status": "success"} if update_body is None else update_body
        # une session archivee n'est plus ni listee ni rejoignable
        self.archived = False
        self._agent = object()

    def _post_json(self, path, payload, with_status=False):
        self.calls.append(path)
        if path == "/session/create":
            body = {"session_code": self.info["code"]}
        elif path in ("/session/get", "/session/join"):
            known = payload.get("session_code") in (self.info["code"], "CODE1")
            body = {"session": self.info} if known and not self.archived else False
        elif path == "/session/list":
            body = {"sessions": {}}
        elif path == "/game/list":
            body = {"game_list": [{"name": self.info["name"]}]}
        elif path == "/session/update":
            body = self.update_body
        elif path == "/session/archive":
            # the real view answers with the plain text "Success", and its status
            # is what tells the game server whether the session is archived
            if self.archive_status == 200:
                self.archived = True
            body = "Success"
        else:
            body = {}
        if with_status:
            status = self.archive_status if path == "/session/archive" else (
                404 if body is False else 200
            )
            return defer.succeed((status, body))
        return defer.succeed(body)

    def paths(self):
        return [path for path in self.calls if path.startswith("/session/")]


class Protocol(GameWebSocketProtocol):
    """Concrete protocol that records what is sent, instead of using a socket."""

    def __init__(self, lobby):
        self.sent = []
        # le dernier evenement d'adhesion, conserve apres clear()
        self.joined = None
        self.closed = False
        self.state = self.STATE_OPEN
        self.factory = GameWebSocketFactory("ws://localhost:9000", lobby)

    def sendMessage(self, payload, isBinary=False):
        self.sent.append(json.loads(payload.decode()))

    def sendClose(self):
        self.closed = True
        self.state = self.STATE_CLOSED

    @property
    def last(self):
        return self.sent[-1] if self.sent else None

    def last_event(self):
        return self.last.get("event") if self.last else None

    def last_error(self):
        return self.last.get("error", {}).get("code") if self.last else None

    def clear(self):
        self.sent.clear()

    def events(self, name):
        return [m for m in self.sent if m.get("event") == name]


def receive(protocol, message):
    protocol.onMessage(json.dumps(message).encode(), False)


def connect(lobby, name="anonymous"):
    protocol = Protocol(lobby)
    lobby.add_user(User(name=name, protocol=protocol))
    return protocol


def open_session(lobby, nickname=OWNER):
    """A connection that has just created the session, so it owns it."""
    protocol = connect(lobby)
    receive(protocol, {
        "action": "create_session", "game_name": "Waterloo",
        "nickname": nickname, "key": "",
    })
    assert protocol.last_event() == "session_joined", protocol.last
    protocol.joined = protocol.last
    protocol.clear()
    return protocol


def join(lobby, nickname, role="player"):
    protocol = connect(lobby)
    receive(protocol, {
        "action": "join_session",
        "session_code": SESSION_INFO["code"],
        "nickname": nickname, "key": "", "role": role,
    })
    assert protocol.last_event() == "session_joined", protocol.last
    protocol.joined = protocol.last
    protocol.clear()
    return protocol


def reconnect_as_owner(lobby, nickname=OWNER):
    """A new connection for the owner, as the game page does on reload."""
    protocol = connect(lobby)
    receive(protocol, {
        "action": "resume_session",
        "session_key": SESSION_INFO["key"],
        "nickname": nickname, "role": "player",
    })
    assert protocol.last_event() == "session_joined", protocol.last
    protocol.joined = protocol.last
    protocol.clear()
    return protocol


def only_session(lobby):
    return next(iter(lobby.sessions.values()))


@pytest.fixture
def chabanas():
    return FakeChabanas(session_info())


@pytest.fixture
def lobby(chabanas):
    lobby = Lobby(LOGGER, chabanas)
    open_session(lobby)
    chabanas.calls.clear()
    return lobby


def close(protocol):
    receive(protocol, {"action": "close_session"})


class TestWhoOwnsTheGame:
    def test_the_owner_is_told_it_owns_the_game(self):
        lobby = Lobby(LOGGER, FakeChabanas(session_info()))
        owner = open_session(lobby)

        assert owner.joined["owner"] is True

    def test_another_player_is_told_it_does_not_own_the_game(self):
        lobby = Lobby(LOGGER, FakeChabanas(session_info()))
        open_session(lobby)
        peer = join(lobby, "bob")

        assert peer.joined["owner"] is False

    def test_a_watcher_is_told_it_does_not_own_the_game(self):
        lobby = Lobby(LOGGER, FakeChabanas(session_info()))
        open_session(lobby)
        watcher = join(lobby, "carol", role="watcher")

        assert watcher.joined["owner"] is False

    def test_the_owner_nickname_is_announced_with_the_session(self):
        lobby = Lobby(LOGGER, FakeChabanas(session_info()))

        open_session(lobby)

        assert only_session(lobby).return_session_json()["owner"] == OWNER

    def test_a_session_without_any_owner_can_be_closed_by_nobody(self):
        """
        Chabanas always names the creator. Should it ever fail to, the game must
        stay open rather than be closable by whoever asks first.
        """
        lobby = Lobby(LOGGER, FakeChabanas(session_info(owner=None)))
        owner = open_session(lobby)

        assert only_session(lobby).owner_nickname is None
        close(owner)
        assert owner.last_error() == "not_session_owner"


class TestCloseSession:
    def test_the_owner_closes_its_game(self, lobby):
        owner = reconnect_as_owner(lobby)

        close(owner)

        assert owner.events("session_closed"), owner.sent

    def test_the_owner_is_announced_by_its_session(self, chabanas):
        lobby = Lobby(LOGGER, chabanas)
        owner = open_session(lobby)

        assert owner.joined["session"]["owner"] == OWNER

    def test_the_state_is_stored_before_the_session_is_archived(self, lobby, chabanas):
        """
        A session that has never been emptied was never written: archiving it
        as is would keep the initial layout instead of where the tokens ended up.
        """
        owner = reconnect_as_owner(lobby)

        close(owner)

        assert chabanas.paths()[-2:] == ["/session/update", "/session/archive"]

    def test_the_session_stops_being_active(self, lobby):
        owner = reconnect_as_owner(lobby)

        close(owner)

        session = only_session(lobby)
        assert session.closed is True
        assert session.active is False

    def test_everyone_still_connected_learns_the_game_is_closed(self, lobby):
        owner = reconnect_as_owner(lobby)
        peer = join(lobby, "bob")
        watcher = join(lobby, "carol", role="watcher")

        close(owner)

        assert owner.events("session_closed")
        assert peer.events("session_closed")
        assert watcher.events("session_closed")

    def test_the_lobby_is_told_the_session_left(self, lobby):
        owner = reconnect_as_owner(lobby)
        bystander = connect(lobby, "somebody-else")
        bystander.clear()

        close(owner)

        message = bystander.last
        assert message["event"] == "session_players_changed"
        assert message["action"] == "closed"
        assert message["nickname"] == OWNER

    def test_closing_twice_is_refused(self, lobby, chabanas):
        owner = reconnect_as_owner(lobby)
        close(owner)
        owner.clear()

        close(owner)

        assert owner.last_error() == "session_closed"
        assert chabanas.paths().count("/session/archive") == 1

    def test_a_closed_session_cannot_be_resumed(self, lobby):
        owner = reconnect_as_owner(lobby)
        close(owner)

        latecomer = connect(lobby)
        receive(latecomer, {"action": "resume_session",
                            "session_key": SESSION_INFO["key"],
                            "nickname": OWNER, "role": "player"})

        assert latecomer.last_error() == "session_closed"

    def test_a_closed_session_is_not_reachable_anymore(self, lobby):
        """
        Chabanas vide le code de la session, donc plus personne ne peut la
        rejoindre par un code. Le serveur garde la session en memoire le temps
        que ses connexions se ferment, et c'est resume_session qui refuse de la
        rendre à quiconque.
        """
        owner = reconnect_as_owner(lobby)
        close(owner)

        assert only_session(lobby).closed is True

        latecomer = connect(lobby)
        receive(latecomer, {"action": "join_session",
                            "session_code": SESSION_INFO["code"],
                            "nickname": "dave", "key": "", "role": "player"})
        assert latecomer.last["error"]["message"] == "Unable to join session CODE1"
        assert only_session(lobby).find_user("dave") is None


class TestCloseRefusals:
    def test_another_player_cannot_close_the_game(self, lobby, chabanas):
        peer = join(lobby, "bob")

        close(peer)

        assert peer.last_error() == "not_session_owner"
        assert "/session/archive" not in chabanas.paths()
        assert only_session(lobby).closed is False

    def test_a_watcher_cannot_close_the_game(self, lobby, chabanas):
        watcher = join(lobby, "carol", role="watcher")

        close(watcher)

        assert watcher.last_error() == "not_session_owner"
        assert "/session/archive" not in chabanas.paths()

    def test_nobody_can_close_a_game_they_did_not_join(self, lobby, chabanas):
        bystander = connect(lobby, "somebody-else")

        close(bystander)

        assert bystander.last_error() == "no_session"
        assert "/session/archive" not in chabanas.paths()

    def test_the_game_stays_open_when_the_state_cannot_be_stored(self, chabanas):
        chabanas.update_body = {}
        lobby = Lobby(LOGGER, chabanas)
        open_session(lobby)
        owner = reconnect_as_owner(lobby)
        chabanas.calls.clear()

        close(owner)

        assert owner.last_error() == "session_state_not_stored"
        assert "/session/archive" not in chabanas.paths()
        assert only_session(lobby).closed is False

    def test_the_game_stays_open_when_the_archive_is_refused(self, chabanas):
        chabanas.archive_status = 500
        lobby = Lobby(LOGGER, chabanas)
        open_session(lobby)
        owner = reconnect_as_owner(lobby)
        chabanas.calls.clear()

        close(owner)

        assert owner.last_error() == "session_not_archived"
        assert only_session(lobby).closed is False
        assert only_session(lobby).players


class TestPlayersBecomeWatchers:
    def test_a_remaining_player_becomes_a_watcher(self, lobby):
        owner = reconnect_as_owner(lobby)
        peer = join(lobby, "bob")

        close(owner)

        session = only_session(lobby)
        assert peer.events("session_closed"), peer.sent
        assert session.find_user("bob") in session.watchers
        assert session.find_user("bob") not in session.players

    def test_the_demoted_player_cannot_play_anymore(self, lobby):
        owner = reconnect_as_owner(lobby)
        peer = join(lobby, "bob")
        session = only_session(lobby)

        close(owner)
        peer.clear()
        receive(peer, {"action": "acquire", "component_id": "t1"})

        assert peer.last_error() == "watcher_not_allowed"
        assert session.find_user("bob").role == "watcher"

    def test_a_watcher_stays_a_watcher(self, lobby):
        owner = reconnect_as_owner(lobby)
        watcher = join(lobby, "carol", role="watcher")

        close(owner)

        session = only_session(lobby)
        # l'owner compte aussi : il a ete demotionne avec les autres
        assert len(session.watchers) == 2
        assert session.find_user("carol").role == "watcher"

    def test_the_watchers_quota_does_not_block_the_conversion(self):
        """
        One watcher slot, three players left. Refusing the closure would leave
        somebody holding a token of a game nobody may play any more, so the quota
        is ignored once the game is over.
        """
        lobby = Lobby(LOGGER, FakeChabanas(session_info(max_players=4, max_watchers=1)))
        open_session(lobby)
        owner = reconnect_as_owner(lobby)
        join(lobby, "bob")
        join(lobby, "carol")
        session = only_session(lobby)
        assert len(session.players) == 3

        close(owner)

        assert session.players == []
        assert len(session.watchers) == 3

    def test_the_owner_is_demoted_as_well(self, lobby):
        owner = reconnect_as_owner(lobby)
        session = only_session(lobby)

        close(owner)

        assert session.find_user(OWNER) in session.watchers

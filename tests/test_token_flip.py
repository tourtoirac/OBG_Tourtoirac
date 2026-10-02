"""
Tests of the face of a token: a token whose game_json gives it a back_src turns
over, one that has none does not, and every screen shows the same face.

The face is chosen by the server, so most of what follows goes through the
WebSocket layer rather than calling Token.flip() directly.
"""
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from conftest import SESSION_INFO  # noqa: E402
from twisted.internet import defer  # noqa: E402

from Components.board import Board  # noqa: E402
from Components.deck import Deck  # noqa: E402
from Components.dice import Dice  # noqa: E402
from Components.token import Token  # noqa: E402
from chabanas import Chabanas  # noqa: E402
from lobby import Lobby  # noqa: E402
from main import GameWebSocketFactory, GameWebSocketProtocol  # noqa: E402
from user import User  # noqa: E402

LOGGER = logging.getLogger("tests")

FRONT_SRC = "/Games/Vietnam/resources/counters/NVA/Front/001.jpg"
BACK_SRC = "/Games/Vietnam/resources/counters/NVA/Back/001.jpg"

TOKEN_JSON = {
    "x": 1000,
    "y": 1000,
    "id": "token",
    "kind": "token",
    "front_src": FRONT_SRC,
    "back_src": BACK_SRC,
    "width": 100,
    "height": 100,
}


def session_info_with_token(**fields):
    """The shared session description, with a token added to it."""
    info = json.loads(json.dumps(SESSION_INFO))
    token = dict(TOKEN_JSON)
    token.update(fields)
    info["game_json"].setdefault("movable", []).append(token)
    return info


def make_lobby(**fields):
    return Lobby(LOGGER, FakeChabanas(session_info_with_token(**fields)))


def only_session(lobby):
    return next(iter(lobby.sessions.values()))


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
            body = {"session": self.info} if payload.get("session_code") in (
                self.info["code"], "CODE1"
            ) else False
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


def open_session(lobby, nickname="alice"):
    """A connection that has just created the session, so it owns a seat."""
    protocol = Protocol(lobby)
    lobby.add_user(User(name="anonymous", protocol=protocol))
    receive(protocol, {
        "action": "create_session", "game_name": "Waterloo",
        "nickname": nickname, "key": "",
    })
    return protocol


def join_watcher(player, nickname="carol"):
    """A spectator connected to the player's session, watching it."""
    protocol = Protocol(player.factory)
    # le Protocol de ces tests se contente d'enregistrer ce qu'il envoie, sans
    # factorycomplete : il en faut une pour que le lobby retrouve le user
    protocol.factory = GameWebSocketFactory(
        "ws://localhost:9000", player.factory.lobby
    )
    player.factory.lobby.add_user(User(name="anonymous", protocol=protocol))
    receive(protocol, {
        "action": "join_session",
        "session_code": SESSION_INFO["code"],
        "nickname": nickname, "key": "", "role": "watcher",
    })
    assert protocol.last_event() == "session_joined", protocol.last
    protocol.clear()
    return protocol


@pytest.fixture
def player():
    """A connection holding the owner seat of a session with a flippable token."""
    return open_session(make_lobby())


def flip(protocol):
    receive(protocol, {"action": "flip", "component_id": "token"})


class TestFlippable:
    def test_a_token_with_a_back_is_flippable(self):
        lobby = make_lobby()
        open_session(lobby)

        assert only_session(lobby).get_component("token").flippable() is True

    def test_a_token_without_a_back_is_not_flippable(self):
        """
        A counter with a single image has nothing to reveal: it must stay on its
        face rather than point at a src that does not exist.
        """
        lobby = make_lobby(back_src=None)
        open_session(lobby)

        assert only_session(lobby).get_component("token").flippable() is False

    def test_a_token_starts_on_its_front(self):
        lobby = make_lobby()
        open_session(lobby)

        assert only_session(lobby).get_component("token").side == "front"

    def test_the_face_is_announced_to_the_client(self):
        lobby = make_lobby()
        open_session(lobby)

        tokens = only_session(lobby).return_session_json()["components"]["movable"]

        sent = next(c for c in tokens if c["id"] == "token")
        assert sent["side"] == "front"
        assert sent["back_src"] == BACK_SRC

    def test_the_saved_face_is_restored_when_the_session_resumes(self):
        """
        A session is saved with each token's face. If it were dropped on reload,
        every resumed game would show all its counters face up.
        """
        lobby = make_lobby(side="back")
        open_session(lobby)
        token = only_session(lobby).get_component("token")

        assert token.side == "back"
        assert token.src == BACK_SRC

    def test_a_token_saved_on_a_face_it_has_not_stays_on_the_front(self):
        lobby = make_lobby(side="back", back_src=None)
        open_session(lobby)

        assert only_session(lobby).get_component("token").side == "front"


class TestFlipAction:
    def test_a_flip_is_accepted(self, player):
        flip(player)

        assert player.last_error() is None
        assert player.events("flip")[0]["side"] == "back"

    def test_the_token_shows_its_back(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        flip(player)

        assert token.side == "back"
        assert token.src == BACK_SRC

    def test_flipping_twice_comes_back_to_the_front(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        flip(player)
        flip(player)

        assert token.side == "front"
        assert token.src == FRONT_SRC

    def test_the_new_face_is_sent_to_everyone(self, player):
        """
        Every screen, including the spectators', must show the same token on the
        same face: the face is broadcast, not returned to the clicker only.
        """
        watcher = join_watcher(player)

        flip(player)

        assert watcher.events("flip")[0]["side"] == "back"

    def test_the_clicker_also_receives_the_face(self, player):
        """
        The player who double-clicked is not told the result separately: the
        broadcast is what redraws their own screen, so they wait for it as the
        others do.
        """
        flip(player)

        assert player.events("flip")[0]["side"] == "back"

    def test_the_event_names_both_images(self, player):
        """
        The client swaps the displayed image on this event, so it has to be
        told which image each face is.
        """
        flip(player)

        event = player.events("flip")[0]
        assert event["front_src"] == FRONT_SRC
        assert event["back_src"] == BACK_SRC

    def test_a_flip_does_not_grab_the_token(self, player):
        flip(player)

        assert player.events("acquire") == []
        assert only_session(player.factory.lobby).get_component("token").acquired_by is None

    def test_a_flip_does_not_move_the_token(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        flip(player)

        assert (token.x, token.y) == (1000, 1000)

    def test_a_held_token_still_turns_over(self, player):
        """
        Changing face does not move the token, so it is allowed while someone
        holds it. This is what makes the double-click work: the first click
        grabs the counter and the second turns it over, as the Vietnam of old
        did.
        """
        token = only_session(player.factory.lobby).get_component("token")
        receive(player, {"action": "acquire", "component_id": "token"})
        player.clear()

        flip(player)

        assert player.last_error() is None
        assert token.side == "back"


class TestFlipRefusals:
    def test_a_token_without_a_back_is_refused(self):
        protocol = open_session(make_lobby(back_src=None))
        token = only_session(protocol.factory.lobby).get_component("token")

        flip(protocol)

        assert protocol.last_error() == "component_not_flippable"
        assert protocol.events("flip") == []
        assert token.side == "front"

    def test_a_dice_is_not_flipped(self, player):
        lobby = player.factory.lobby
        session = only_session(lobby)
        # un dé déclaré à côté du pion : seul son propre jeu de règles doit
        # décider, or il n'a pas de face à retourner
        session.components_dict["dice"] = Dice(
            "dice", 0, 0, "/Games/Waterloo/resources/dice/1.png", 90, 90,
            ["/Games/Waterloo/resources/dice/1.png"],
            "/Games/Waterloo/resources/dice/1.png",
        )
        player.clear()

        receive(player, {"action": "flip", "component_id": "dice"})

        assert player.last_error() == "component_not_flippable"
        assert player.events("flip") == []

    def test_a_board_is_not_flipped(self, player):
        lobby = player.factory.lobby
        session = only_session(lobby)
        session.components_dict["b1"] = Board("b1", 0, 0, "board.png", 800, 600)
        player.clear()

        receive(player, {"action": "flip", "component_id": "b1"})

        assert player.last_error() == "component_not_flippable"
        assert player.events("flip") == []

    def test_a_deck_is_not_flipped(self, player):
        lobby = player.factory.lobby
        only_session(lobby).components_dict["deck"] = Deck(
            "deck", 0, 0, "/front.png", 50, 50, ["/front.png"],
        )
        player.clear()

        receive(player, {"action": "flip", "component_id": "deck"})

        assert player.last_error() == "component_not_flippable"

    def test_an_unknown_component_is_refused(self, player):
        receive(player, {"action": "flip", "component_id": "absent"})

        assert player.last_error() == "component_not_found"

    def test_a_missing_component_id_is_refused(self, player):
        receive(player, {"action": "flip"})

        assert player.last_error() == "missing_field"

    def test_a_watcher_does_not_flip(self, player):
        token = only_session(player.factory.lobby).get_component("token")
        watcher = join_watcher(player)

        flip(watcher)

        assert watcher.last_error() == "watcher_not_allowed"
        assert token.side == "front"

    def test_flip_without_a_session_is_refused(self, player):
        player.factory.lobby.get_user(player).session = None

        flip(player)

        assert player.last_error() == "no_session"


class TestFlipSurvivesTheSavedState:
    def test_the_face_is_part_of_the_session_state(self, player):
        lobby = player.factory.lobby
        flip(player)

        state = only_session(lobby).game_json_state()
        saved = next(c for c in state["movable"] if c["id"] == "token")

        assert saved["side"] == "back"


class TestFlipMethod:
    def test_flip_switches_face_and_image(self):
        token = Token("t", 0, 0, "f.png", "b.png", 40, 40)

        token.flip()

        assert token.side == "back"
        assert token.src == "b.png"

    def test_src_always_follows_the_face(self):
        token = Token("t", 0, 0, "f.png", "b.png", 40, 40)

        token.flip()
        token.flip()

        assert token.src == token.image_src[token.side]

    def test_a_token_without_a_back_keeps_its_face(self):
        """
        Flipping it must not fall through to the front while pretending it
        turned, nor point at a missing image.
        """
        token = Token("t", 0, 0, "f.png", None, 40, 40)

        token.flip()

        assert token.side == "front"
        assert token.src == "f.png"

    def test_a_token_without_a_back_can_be_flipped_endlessly(self):
        token = Token("t", 0, 0, "f.png", None, 40, 40)

        for _ in range(3):
            token.flip()

        assert token.side == "front"

    def test_the_face_is_visible_in_return_json(self):
        token = Token("t", 0, 0, "f.png", "b.png", 40, 40)

        token.flip()

        assert token.return_json()["side"] == "back"

    def test_a_board_is_not_flippable(self):
        assert Board("b", 0, 0, "board.png", 800, 600).flippable() is False

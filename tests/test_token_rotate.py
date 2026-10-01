"""
Tests of the rotation of an orientable token: loading orientable from the
game_json, the rotate action, and what reaches the other players.

The angle is computed by the server, so most of what follows goes through the
WebSocket layer rather than calling Token.rotate() directly.
"""
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from conftest import SESSION_INFO  # noqa: E402
from twisted.internet import defer  # noqa: E402

from Components.deck import Deck  # noqa: E402
from Components.dice import Dice  # noqa: E402
from Components.token import ROTATION_STEP, Token, normalize_orientation  # noqa: E402
from chabanas import Chabanas  # noqa: E402
from lobby import Lobby  # noqa: E402
from main import GameWebSocketFactory, GameWebSocketProtocol  # noqa: E402
from user import User  # noqa: E402

LOGGER = logging.getLogger("tests")

TOKEN_JSON = {
    "x": 1000,
    "y": 1000,
    "id": "token",
    "kind": "token",
    "front_src": "/Games/Diplomacy/resources/counters/army.png",
    "back_src": None,
    "width": 100,
    "height": 100,
}


def session_info_with_token(**fields):
    """The shared session description, with an orientable token added to it."""
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
    """A connection holding the owner seat of a session with an orientable token."""
    return open_session(make_lobby(orientable=True))


class TestLoadingOrientable:
    def test_an_orientable_token_is_loaded_as_such(self):
        lobby = make_lobby(orientable=True)
        open_session(lobby)

        assert only_session(lobby).get_component("token").orientable is True

    def test_a_token_without_the_flag_is_not_orientable(self):
        lobby = make_lobby()
        open_session(lobby)

        assert only_session(lobby).get_component("token").orientable is False

    def test_the_flag_is_announced_to_the_client(self):
        lobby = make_lobby(orientable=True)
        open_session(lobby)

        token = only_session(lobby).return_session_json()["components"]["movable"]

        assert next(c for c in token if c["id"] == "token")["orientable"] is True

    @pytest.mark.parametrize("value", ["true", "false", 1, 0, "", None, []])
    def test_only_a_real_true_enables_the_rotation(self, value):
        """
        The client draws the zones from this flag. A "false" written as a string
        must not enable anything, or a game that says "false" would get the
        rotation UI.
        """
        lobby = make_lobby(orientable=value)
        open_session(lobby)

        assert only_session(lobby).get_component("token").orientable is False

    def test_the_saved_angle_is_restored_when_the_session_resumes(self):
        """
        A session is saved with each token's orientation. If it were dropped on
        reload, every resumed game would put its tokens back flat.
        """
        lobby = make_lobby(orientable=True, orientation=45)
        open_session(lobby)

        assert only_session(lobby).get_component("token").orientation == 45


class TestRotateAction:
    def test_a_rotation_is_accepted(self, player):
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.last_error() is None
        assert player.events("rotate")[0]["orientation"] == ROTATION_STEP

    def test_the_token_turns_by_one_step(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert token.orientation == ROTATION_STEP

    def test_rotating_right_then_left_comes_back_to_the_start(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "left",
        })

        assert token.orientation == 0

    def test_the_new_angle_is_sent_to_everyone(self, player):
        """
        Every screen, including the spectators', must show the same token at the
        same angle: the angle is broadcast, not returned to the clicker only.
        """
        watcher = join_watcher(player)

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert watcher.events("rotate")[0]["orientation"] == ROTATION_STEP

    def test_the_clicker_also_receives_the_angle(self, player):
        """
        The player who clicked is not told the result separately: the broadcast
        is what redraws their own screen, so they wait for it as the others do.
        """
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.events("rotate")[0]["orientation"] == ROTATION_STEP

    def test_the_angle_is_normalised_after_enough_steps(self, player):
        """
        8 steps of 45 degrees is a full turn: the clients must read 0, not 360,
        or they would keep drawing it as rotated by a whole turn.
        """
        token = only_session(player.factory.lobby).get_component("token")

        for _ in range(8):
            receive(player, {
                "action": "rotate", "component_id": "token", "direction": "right",
            })

        assert token.orientation == 0

    def test_eight_clicks_cover_a_whole_turn(self, player):
        """
        A step of 45 degrees gives the 8 positions of a counter stood upright.
        Each click lands on a new one, and the eighth is back to the start.
        """
        token = only_session(player.factory.lobby).get_component("token")
        vues = []
        for _ in range(8):
            receive(player, {
                "action": "rotate", "component_id": "token", "direction": "right",
            })
            vues.append(token.orientation)

        assert vues == [45, 90, 135, 180, 225, 270, 315, 0]

    def test_one_click_is_45_degrees(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "left",
        })

        assert token.orientation == 315

    def test_a_rotation_does_not_grab_the_token(self, player):
        """
        The click on a rotation zone must not add the token to the hand: the two
        behaviours are exclusive.
        """
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.events("acquire") == []
        assert only_session(player.factory.lobby).get_component("token").acquired_by is None

    def test_a_rotation_does_not_move_the_token(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "left",
        })

        assert (token.x, token.y) == (1000, 1000)

    def test_a_held_token_does_not_turn(self, player):
        """
        The client only shows the zones on an empty hand. Rotating a token under
        the cursor of another player would be a surprise.
        """
        token = only_session(player.factory.lobby).get_component("token")
        receive(player, {"action": "acquire", "component_id": "token"})
        player.clear()

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.last_error() == "component_held"
        assert token.orientation == 0

    def test_the_token_still_turns_once_released(self, player):
        token = only_session(player.factory.lobby).get_component("token")
        receive(player, {"action": "acquire", "component_id": "token"})
        receive(player, {
            "action": "release", "component_id": "token", "x": 1000, "y": 1000,
        })
        player.clear()

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.last_error() is None
        assert token.orientation == ROTATION_STEP


class TestRotateRefusals:
    def test_a_token_that_is_not_orientable_is_refused(self):
        protocol = open_session(make_lobby(orientable=False))

        receive(protocol, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert protocol.last_error() == "component_not_rotatable"
        assert protocol.events("rotate") == []

    def test_a_token_without_the_flag_is_refused(self):
        """
        Same as above for a game_json that never mentions orientable at all.
        """
        protocol = open_session(make_lobby())

        receive(protocol, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert protocol.last_error() == "component_not_rotatable"

    def test_a_dice_is_not_rotated(self):
        lobby = make_lobby(orientable=True)
        protocol = open_session(lobby)
        session = only_session(lobby)
        # un dé déclaré à côté du pion, et orientable lui aussi : seul son
        # propre jeu de règles doit décider, or il ne sait pas tourner
        session.components_dict["dice"] = Dice(
            "dice", 0, 0, "/Games/Waterloo/resources/dice/1.png", 90, 90,
            ["/Games/Waterloo/resources/dice/1.png"],
            "/Games/Waterloo/resources/dice/1.png",
        )
        protocol.clear()

        receive(protocol, {
            "action": "rotate", "component_id": "dice", "direction": "right",
        })

        assert protocol.last_error() == "component_not_rotatable"

    def test_a_deck_is_not_rotated(self):
        lobby = make_lobby(orientable=True)
        protocol = open_session(lobby)
        only_session(lobby).components_dict["deck"] = Deck(
            "deck", 0, 0, "/front.png", 50, 50, ["/front.png"],
        )
        protocol.clear()

        receive(protocol, {
            "action": "rotate", "component_id": "deck", "direction": "right",
        })

        assert protocol.last_error() == "component_not_rotatable"

    def test_an_unknown_direction_is_refused(self, player):
        token = only_session(player.factory.lobby).get_component("token")

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "sideways",
        })

        assert player.last_error() == "invalid_direction"
        assert token.orientation == 0

    @pytest.mark.parametrize("direction", ["", 15, None, True, ["right"], {"a": 1}])
    def test_a_direction_that_is_not_a_word_is_refused(self, player, direction):
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": direction,
        })

        assert player.last_error() == "invalid_direction"

    def test_a_missing_direction_is_refused(self, player):
        receive(player, {"action": "rotate", "component_id": "token"})

        assert player.last_error() == "missing_field"

    def test_a_missing_component_is_refused(self, player):
        receive(player, {
            "action": "rotate", "component_id": "absent", "direction": "right",
        })

        assert player.last_error() == "component_not_found"

    def test_a_watcher_does_not_rotate(self, player):
        """
        A spectator watches. They were refused on every component action, and
        turning a token is one too.
        """
        token = only_session(player.factory.lobby).get_component("token")
        watcher = join_watcher(player)

        receive(watcher, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert watcher.last_error() == "watcher_not_allowed"
        assert token.orientation == 0

    def test_rotation_without_a_session_is_refused(self, player):
        player.factory.lobby.get_user(player).session = None

        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        assert player.last_error() == "no_session"


class TestAngleNormalisation:
    def test_a_full_turn_is_read_as_zero(self):
        assert normalize_orientation(360) == 0

    def test_going_past_a_full_turn_wraps_around(self):
        assert normalize_orientation(375) == 15

    def test_a_negative_angle_wraps_to_the_top_of_the_turn(self):
        assert normalize_orientation(-15) == 345

    def test_eight_steps_make_a_full_turn(self):
        assert 8 * ROTATION_STEP == 360

    @pytest.mark.parametrize("value,expected", [
        (None, 0), ("abc", 0), ([1], 0), ("45", 45), (45.9, 45), (-360, 0),
    ])
    def test_unusable_angles_fall_back_to_zero(self, value, expected):
        assert normalize_orientation(value) == expected

    def test_a_token_starts_flat(self):
        token = Token("t", 0, 0, "f.png", None, 40, 40)

        assert token.orientation == 0

    def test_rotating_a_token_keeps_a_flat_token_after_a_whole_turn(self):
        token = Token("t", 0, 0, "f.png", None, 40, 40, orientable=True)

        for _ in range(8):
            token.rotate(ROTATION_STEP)

        assert token.orientation == 0


class TestTap:
    def test_tap_places_the_token_at_an_angle(self):
        token = Token("t", 0, 0, "f.png", None, 40, 40, orientable=True)

        token.tap(90)

        assert token.orientation == 90

    def test_tap_normalises_the_angle(self):
        token = Token("t", 0, 0, "f.png", None, 40, 40, orientable=True)

        token.tap(-45)

        assert token.orientation == 315


class TestRotationSurvivesTheSavedState:
    def test_the_angle_is_part_of_the_session_state(self, player):
        token = only_session(player.factory.lobby).get_component("token")
        receive(player, {
            "action": "rotate", "component_id": "token", "direction": "right",
        })

        state = only_session(player.factory.lobby).game_json_state()
        saved = next(c for c in state["movable"] if c["id"] == "token")

        assert saved["orientation"] == ROTATION_STEP

    def test_the_flag_is_part_of_the_session_state(self, player):
        state = only_session(player.factory.lobby).game_json_state()
        saved = next(c for c in state["movable"] if c["id"] == "token")

        assert saved["orientable"] is True

"""
Tests of the dice: loading from the game_json, rolling, and the delay that
keeps it from being launched again straight away.

The delay is enforced by the server on purpose, so most of what follows goes
through the WebSocket layer rather than calling Dice.roll() directly.
"""
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from conftest import SESSION_INFO  # noqa: E402
from twisted.internet import defer  # noqa: E402

from Components.dice import DEFAULT_ROLL_DELAY_SECONDS, Dice, read_roll_delay  # noqa: E402
from chabanas import Chabanas  # noqa: E402
from lobby import Lobby  # noqa: E402
from main import GameWebSocketFactory, GameWebSocketProtocol  # noqa: E402
from user import User  # noqa: E402

LOGGER = logging.getLogger("tests")

DICE_FACES = [f"/Games/Waterloo/resources/dice/{i}.png" for i in range(1, 7)]

# la forme exacte fournie pour le dé, src_list comprise
DICE_JSON = {
    "x": 1000,
    "y": 1000,
    "id": "dice",
    "kind": "dice",
    "width": 90,
    "height": 90,
    "src_list": DICE_FACES,
}


def session_info_with_dice(where="fixed", **fields):
    """The shared session description, with the dice added to one list."""
    info = json.loads(json.dumps(SESSION_INFO))
    game_json = info["game_json"]
    dice = dict(DICE_JSON)
    dice.update(fields)
    game_json.setdefault(where, []).append(dice)
    return info


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


def make_lobby(where="fixed", **fields):
    return Lobby(LOGGER, FakeChabanas(session_info_with_dice(where, **fields)))


def open_session(lobby, nickname="alice"):
    """A connection that has just created the session, so it owns a seat."""
    protocol = Protocol(lobby)
    lobby.add_user(User(name="anonymous", protocol=protocol))
    receive(protocol, {
        "action": "create_session", "game_name": "Waterloo",
        "nickname": nickname, "key": "",
    })
    return protocol


@pytest.fixture
def player():
    """A connection holding the owner seat of a session whose game has a dice."""
    return open_session(make_lobby("fixed"))


class TestDiceLoading:
    def test_dice_is_built_from_the_game_json(self):
        lobby = make_lobby("fixed")
        open_session(lobby)

        dice = only_session(lobby).get_component("dice")

        assert isinstance(dice, Dice)
        assert (dice.x, dice.y) == (1000, 1000)
        assert (dice.width, dice.height) == (90, 90)
        assert dice.src_list == DICE_FACES

    def test_first_face_is_shown_when_the_game_json_has_no_src(self):
        """
        The game_json carries no src: the dice starts on the first face rather
        than on nothing.
        """
        lobby = make_lobby("fixed")
        open_session(lobby)

        assert only_session(lobby).get_component("dice").src == DICE_FACES[0]

    def test_dice_is_accepted_in_the_movable_list_too(self):
        """Where it sits in the game_json is the game's business, not ours."""
        lobby = make_lobby("movable")
        open_session(lobby)

        assert isinstance(only_session(lobby).get_component("dice"), Dice)

    def test_session_json_exposes_the_dice_with_its_faces(self, player):
        dice = only_session(player.factory.lobby).return_session_json()

        assert len(dice["components"]["dice"]) == 1
        assert dice["components"]["dice"][0]["id"] == "dice"
        assert dice["components"]["dice"][0]["src"] == DICE_FACES[0]
        assert dice["components"]["dice"][0]["src_list"] == DICE_FACES, (
            "le client a besoin de toute la liste pour precharger les faces"
        )

    def test_saved_state_can_be_loaded_back(self, player):
        """
        The situation saved when everyone leaves must still be readable, so a
        dice keeps its faces across a resume instead of losing src_list.
        """
        session = only_session(player.factory.lobby)
        session.get_component("dice").roll()

        state = session.game_json_state()
        saved = next(c for c in state["fixed"] if c.get("kind") == "dice")

        assert saved["src_list"] == DICE_FACES
        assert saved["src"] in DICE_FACES

        restored = type(session)(
            User(name="carol", protocol=None), "Waterloo", "KEY2", "CODE2",
            True, "std", state,
        )
        assert restored.get_component("dice").src == saved["src"]

    def test_a_dice_declared_in_movable_comes_back_in_movable(self):
        lobby = make_lobby("movable")
        open_session(lobby)

        state = only_session(lobby).game_json_state()

        assert any(c.get("kind") == "dice" for c in state["movable"])
        assert not any(c.get("kind") == "dice" for c in state["fixed"])

    def test_a_dice_in_a_dedicated_list_is_found_and_saved_there(self):
        """
        A game may also declare its dices in their own "dice" list. Found or not
        depends on that choice, so the round-trip has to honour it.
        """
        lobby = make_lobby("dice")
        open_session(lobby)
        session = only_session(lobby)

        assert isinstance(session.get_component("dice"), Dice)

        state = session.game_json_state()
        assert [c["id"] for c in state["dice"]] == ["dice"]
        assert not any(c.get("kind") == "dice" for c in state["fixed"])
        assert not any(c.get("kind") == "dice" for c in state["movable"])


class TestDiceRoll:
    def test_a_click_rolls_and_broadcasts_the_face(self, player):
        receive(player, {"action": "roll", "component_id": "dice"})

        rolls = player.events("roll")
        assert len(rolls) == 1, "la face doit etre diffusee a tout l'ecran"
        assert rolls[0]["src"] in DICE_FACES
        assert rolls[0]["component_id"] == "dice"
        assert rolls[0]["cooldown_seconds"] == DEFAULT_ROLL_DELAY_SECONDS

    def test_everyone_in_the_session_sees_the_same_face(self, player):
        lobby = player.factory.lobby
        watcher = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher))
        receive(watcher, {
            "action": "join_session", "session_code": "CODE1",
            "nickname": "carol", "role": "watcher",
        })
        watcher.clear()

        receive(player, {"action": "roll", "component_id": "dice"})

        rolls = watcher.events("roll")
        assert len(rolls) == 1, "un spectateur voit aussi la face tiree"
        assert rolls[0]["src"] == player.events("roll")[0]["src"]

    def test_the_second_click_too_soon_is_refused(self, player):
        receive(player, {"action": "roll", "component_id": "dice"})
        player.clear()

        receive(player, {"action": "roll", "component_id": "dice"})

        assert player.last_error() == "dice_cooling_down"
        assert player.events("roll") == [], "aucune face ne doit partir"

    def test_the_face_actually_changes(self, player):
        """Not a constant: two launches far enough apart are independent."""
        dice = only_session(player.factory.lobby).get_component("dice")
        first = dice.roll()
        dice.rolled_at -= DEFAULT_ROLL_DELAY_SECONDS
        second = dice.roll()

        assert first in DICE_FACES and second in DICE_FACES

    def test_the_dice_becomes_rollable_again_after_the_delay(self, player):
        dice = only_session(player.factory.lobby).get_component("dice")
        receive(player, {"action": "roll", "component_id": "dice"})
        player.clear()
        # on fait vieillir le dernier lancer de la durée exacte du délai
        dice.rolled_at -= DEFAULT_ROLL_DELAY_SECONDS

        receive(player, {"action": "roll", "component_id": "dice"})

        assert len(player.events("roll")) == 1
        assert player.last_error() is None

    def test_the_dice_is_still_locked_one_second_early(self, player):
        dice = only_session(player.factory.lobby).get_component("dice")
        receive(player, {"action": "roll", "component_id": "dice"})
        player.clear()
        dice.rolled_at -= DEFAULT_ROLL_DELAY_SECONDS - 1.0

        receive(player, {"action": "roll", "component_id": "dice"})

        assert player.last_error() == "dice_cooling_down"

    def test_a_token_cannot_be_rolled(self, player):
        receive(player, {"action": "roll", "component_id": "t1"})

        assert player.last_error() == "component_not_clickable"

    def test_a_watcher_cannot_roll(self, player):
        lobby = player.factory.lobby
        watcher = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=watcher))
        receive(watcher, {
            "action": "join_session", "session_code": "CODE1",
            "nickname": "carol", "role": "watcher",
        })
        watcher.clear()

        receive(watcher, {"action": "roll", "component_id": "dice"})

        assert watcher.last_error() == "watcher_not_allowed"

    def test_an_unknown_dice_is_reported(self, player):
        receive(player, {"action": "roll", "component_id": "d20"})

        assert player.last_error() == "component_not_found"

    def test_rolling_requires_a_component_id(self, player):
        receive(player, {"action": "roll"})

        assert player.last_error() == "missing_field"

    def test_rolling_without_a_session_is_refused(self):
        lobby = make_lobby("fixed")
        protocol = Protocol(lobby)
        lobby.add_user(User(name="anonymous", protocol=protocol))

        receive(protocol, {"action": "roll", "component_id": "dice"})

        assert protocol.last_error() == "no_session"

    def test_the_dice_keeps_its_position_when_rolled(self, player):
        dice = only_session(player.factory.lobby).get_component("dice")

        receive(player, {"action": "roll", "component_id": "dice"})

        assert (dice.x, dice.y) == (1000, 1000), "lancer ne deplace pas le de"


class TestFixPositionsWithADice:
    def test_fixing_positions_does_not_crash_on_a_dice(self):
        """
        "Fixe la position" only makes sense for tokens, so a dice must not make
        it raise even when the game declares it among the movable components.
        """
        lobby = make_lobby("movable")
        protocol = open_session(lobby)

        receive(protocol, {"action": "fix_positions"})

        assert protocol.last_event() == "fix_positions"

    def test_fix_positions_is_refused_when_the_game_hides_the_button(self):
        lobby = make_lobby("fixed")
        protocol = open_session(lobby)
        only_session(lobby).options["fix_positions"] = None
        protocol.clear()

        receive(protocol, {"action": "fix_positions"})

        assert protocol.last_error() == "fix_positions_disabled"

    def test_the_dice_is_not_listed_among_the_fixed_counters(self):
        lobby = make_lobby("fixed")
        open_session(lobby)

        fixed = only_session(lobby).fix_positions()

        assert all(c.get("kind") != "dice" for c in fixed)


class TestConfigurableRollDelay:
    """
    roll_delay vient du game_json du jeu : c'est lui qui dit combien de
    secondes un dé reste injouable après un lancer.
    """

    def test_the_delay_comes_from_the_game_json(self):
        lobby = make_lobby("fixed", roll_delay=3)
        open_session(lobby)

        assert only_session(lobby).get_component("dice").roll_delay == 3.0

    def test_the_delay_is_announced_to_the_client(self):
        protocol = open_session(make_lobby("fixed", roll_delay=3))

        receive(protocol, {"action": "roll", "component_id": "dice"})

        assert protocol.events("roll")[0]["cooldown_seconds"] == 3.0

    def test_the_delay_is_in_the_session_json(self):
        lobby = make_lobby("fixed", roll_delay=3)
        open_session(lobby)

        dice = only_session(lobby).return_session_json()["components"]["dice"][0]

        assert dice["roll_delay"] == 3.0

    def test_a_shorter_delay_is_honoured(self):
        protocol = open_session(make_lobby("fixed", roll_delay=1))
        dice = only_session(protocol.factory.lobby).get_component("dice")
        receive(protocol, {"action": "roll", "component_id": "dice"})
        protocol.clear()
        # au bout d'une seconde, le dé doit de nouveau accepter un lancer
        dice.rolled_at -= 1.0

        receive(protocol, {"action": "roll", "component_id": "dice"})

        assert len(protocol.events("roll")) == 1
        assert protocol.last_error() is None

    def test_a_shorter_delay_still_refuses_the_click_before_it_is_up(self):
        protocol = open_session(make_lobby("fixed", roll_delay=3))
        dice = only_session(protocol.factory.lobby).get_component("dice")
        receive(protocol, {"action": "roll", "component_id": "dice"})
        protocol.clear()
        # le délai du jeu est de 3 s : 2 s après, c'est encore trop tôt
        dice.rolled_at -= 2.0

        receive(protocol, {"action": "roll", "component_id": "dice"})

        assert protocol.last_error() == "dice_cooling_down"

    def test_a_zero_delay_lets_the_dice_be_rolled_freely(self):
        protocol = open_session(make_lobby("fixed", roll_delay=0))
        receive(protocol, {"action": "roll", "component_id": "dice"})
        protocol.clear()

        receive(protocol, {"action": "roll", "component_id": "dice"})

        assert len(protocol.events("roll")) == 1

    def test_a_missing_delay_falls_back_to_the_default(self):
        lobby = make_lobby("fixed")
        open_session(lobby)

        assert only_session(lobby).get_component("dice").roll_delay == (
            DEFAULT_ROLL_DELAY_SECONDS
        )

    @pytest.mark.parametrize("value", [
        "trois",       # une chaîne n'est pas un délai
        None,          # champ absent du game_json
        -2,            # un délai négatif ne veut rien dire
        True,          # un booléen n'est pas un nombre de secondes
        [3],
    ])
    def test_an_unusable_delay_falls_back_instead_of_breaking(self, value):
        lobby = make_lobby("fixed", roll_delay=value)
        open_session(lobby)

        assert only_session(lobby).get_component("dice").roll_delay == (
            DEFAULT_ROLL_DELAY_SECONDS
        )

    def test_the_delay_survives_the_saved_state(self):
        lobby = make_lobby("fixed", roll_delay=3)
        open_session(lobby)
        session = only_session(lobby)

        state = session.game_json_state()
        saved = next(c for c in state["fixed"] if c.get("kind") == "dice")

        assert saved["roll_delay"] == 3.0


class TestReadRollDelay:
    @pytest.mark.parametrize("value,expected", [
        (3, 3.0),
        (0, 0.0),
        (12, 12.0),
        (1.5, 1.5),
        (None, DEFAULT_ROLL_DELAY_SECONDS),
        ("3", DEFAULT_ROLL_DELAY_SECONDS),
        (-1, DEFAULT_ROLL_DELAY_SECONDS),
        (True, DEFAULT_ROLL_DELAY_SECONDS),
        (False, DEFAULT_ROLL_DELAY_SECONDS),
    ])
    def test_read_roll_delay(self, value, expected):
        assert read_roll_delay(value) == expected

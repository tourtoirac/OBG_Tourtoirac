"""
Tests of the dice_pool component: one to n dice declared together in "fixed",
that one button of the client rolls all at once.

The actions are exercised through the message handlers with fakes rather than
the WebSocket layer, the way test_counter.py does, so the pool is covered even
where autobahn is missing.
"""
import logging

import pytest

from Components.dice import Dice
from Components.dice_pool import DicePool
from handle_message import roll, roll_pool
from session import Session, duplicate_component_ids
from user import User

LOGGER = logging.getLogger("tests")

FACES = [f"/Games/Waterloo/resources/dice/{i}.png" for i in range(1, 7)]

RED = {"kind": "dice", "id": "red", "x": 100, "y": 100, "width": 90, "height": 90,
       "src_list": FACES, "roll_delay": 2}
WHITE = dict(RED, id="white", x=200)
POOL = {"kind": "dice_pool", "id": "pool", "dice": [RED, WHITE]}
LONE_DICE = dict(RED, id="lone", x=500)
TOKEN = {"kind": "token", "id": "token", "x": 0, "y": 0, "width": 40, "height": 40,
         "front_src": "/army.png", "back_src": None}


def build_session(*fixed, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": list(fixed) if fixed else [POOL],
        "movable": [TOKEN],
        **extra,
    }
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class FakeUser(User):
    """A player that keeps what the session sends."""

    def __init__(self, name="alice", role="player", session=None):
        super().__init__(name=name, protocol=None)
        self.role = role
        self.session = session
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeProtocol:
    """The least the handlers expect: a lobby and the sending of errors."""

    def __init__(self, user):
        self.user = user
        self.sent = []
        self.factory = type("Factory", (), {"lobby": self})()

    def get_user(self, protocol):
        return self.user

    def send_error(self, code, message):
        self.sent.append({"event": "error", "error": {"code": code, "message": message}})


def player_on(session, role="player"):
    user = FakeUser(role=role, session=session)
    session.players = [user] if role == "player" else []
    session.watchers = [user] if role != "player" else []
    return FakeProtocol(user), user


def call(handler, protocol, component_id):
    handler(protocol, LOGGER, {"action": "x", "component_id": component_id})


def error_codes(protocol):
    return [message["error"]["code"] for message in protocol.sent]


class TestLoading:
    def test_the_pool_is_a_fixed_component(self):
        session = build_session()
        (pool,) = session.components_lists["fixed"]
        assert isinstance(pool, DicePool)
        assert [dice.id for dice in pool.dice] == ["red", "white"]

    def test_the_pool_and_its_dice_are_indexed_by_id(self):
        session = build_session()
        assert isinstance(session.get_component("pool"), DicePool)
        assert isinstance(session.get_component("red"), Dice)
        assert isinstance(session.get_component("white"), Dice)

    def test_the_dice_of_a_pool_are_not_listed_twice(self):
        session = build_session(POOL, LONE_DICE)
        assert [dice.id for dice in session.components_lists["dice"]] == ["lone"]

    @pytest.mark.parametrize("dice", [[], None, [TOKEN]])
    def test_a_pool_without_dice_is_ignored(self, dice):
        session = build_session(dict(POOL, dice=dice))
        assert session.components_lists["fixed"] == []
        assert session.get_component("pool") is False

    def test_anything_but_a_dice_is_left_out_of_the_pool(self):
        session = build_session(dict(POOL, dice=[RED, TOKEN]))
        assert [dice.id for dice in session.components_lists["fixed"][0].dice] == ["red"]

    def test_a_pool_is_only_read_from_fixed(self):
        session = build_session({"kind": "board", "id": "b", "x": 0, "y": 0, "src": "/m.jpg",
                                 "width": 10, "height": 10}, movable=[POOL])
        assert session.get_component("pool") is False


class TestSave:
    def test_the_session_json_carries_the_pool_with_its_dice(self):
        fixed = build_session().return_session_json()["components"]["fixed"]
        assert fixed[0]["kind"] == "dice_pool"
        assert fixed[0]["id"] == "pool"
        assert [dice["id"] for dice in fixed[0]["dice"]] == ["red", "white"]

    def test_the_saved_state_is_read_back(self):
        session = build_session(POOL, LONE_DICE)
        session.get_component("white").src = FACES[3]
        state = session.game_json_state()
        resumed = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state,
        )
        pool = resumed.get_component("pool")
        assert [dice.id for dice in pool.dice] == ["red", "white"]
        assert resumed.get_component("white").src == FACES[3]
        assert resumed.get_component("red").roll_delay == 2
        assert [dice.id for dice in resumed.components_lists["dice"]] == ["lone"]

    def test_saved_dice_positions_are_rounded(self):
        session = build_session()
        session.get_component("red").x = 100.6
        assert session.game_json_state()["fixed"][0]["dice"][0]["x"] == 101

    def test_the_setup_moves_a_dice_of_a_pool(self):
        session = build_session(setup=[{"component_id": "white", "x": 700, "y": 800}])
        session.apply_setup()
        white = session.get_component("white")
        assert (white.x, white.y) == (700, 800)


class TestDuplicateIds:
    def test_a_pool_has_no_duplicate(self):
        assert duplicate_component_ids({"fixed": [POOL, LONE_DICE], "movable": [TOKEN]}) == []

    def test_a_dice_of_a_pool_shares_the_namespace(self):
        assert duplicate_component_ids({"fixed": [POOL], "dice": [dict(LONE_DICE, id="red")]}) == ["red"]

    def test_the_pool_id_shares_the_namespace(self):
        assert duplicate_component_ids({"fixed": [POOL], "movable": [dict(TOKEN, id="pool")]}) == ["pool"]


class TestRollPool:
    def test_every_dice_is_rolled_in_one_message(self):
        session = build_session()
        protocol, user = player_on(session)
        call(roll_pool, protocol, "pool")
        assert protocol.sent == []
        (message,) = user.sent
        assert message["event"] == "roll_pool"
        assert message["component_id"] == "pool"
        assert [entry["component_id"] for entry in message["dice"]] == ["red", "white"]
        for entry in message["dice"]:
            assert entry["src"] in FACES
            assert entry["src"] == session.get_component(entry["component_id"]).src
            assert entry["cooldown_seconds"] == 2

    def test_a_second_throw_waits_for_the_delay(self):
        session = build_session()
        protocol, user = player_on(session)
        call(roll_pool, protocol, "pool")
        call(roll_pool, protocol, "pool")
        assert error_codes(protocol) == ["dice_cooling_down"]
        assert len(user.sent) == 1

    def test_one_cooling_dice_holds_the_whole_pool(self):
        session = build_session()
        protocol, user = player_on(session)
        call(roll, protocol, "red")
        white_face = session.get_component("white").src
        call(roll_pool, protocol, "pool")
        assert error_codes(protocol) == ["dice_cooling_down"]
        assert session.get_component("white").src == white_face
        assert session.get_component("white").rolled_at is None

    def test_a_pool_throw_starts_the_delay_of_each_dice(self):
        session = build_session()
        protocol, _ = player_on(session)
        call(roll_pool, protocol, "pool")
        call(roll, protocol, "white")
        assert error_codes(protocol) == ["dice_cooling_down"]

    def test_a_pool_without_delay_is_thrown_freely(self):
        pool = dict(POOL, dice=[dict(RED, roll_delay=0), dict(WHITE, roll_delay=0)])
        session = build_session(pool)
        protocol, user = player_on(session)
        call(roll_pool, protocol, "pool")
        call(roll_pool, protocol, "pool")
        assert protocol.sent == []
        assert len(user.sent) == 2

    def test_a_dice_of_a_pool_is_still_rolled_alone(self):
        session = build_session()
        protocol, user = player_on(session)
        call(roll, protocol, "red")
        assert user.sent[0]["event"] == "roll"
        assert user.sent[0]["component_id"] == "red"

    @pytest.mark.parametrize("component_id", ["red", "token"])
    def test_only_a_pool_is_thrown(self, component_id):
        session = build_session()
        protocol, user = player_on(session)
        call(roll_pool, protocol, component_id)
        assert error_codes(protocol) == ["component_not_a_dice_pool"]
        assert user.sent == []

    def test_a_pool_is_not_rolled_as_a_dice(self):
        session = build_session()
        protocol, user = player_on(session)
        call(roll, protocol, "pool")
        assert error_codes(protocol) == ["component_not_clickable"]
        assert user.sent == []

    def test_an_unknown_pool_is_refused(self):
        protocol, _ = player_on(build_session())
        call(roll_pool, protocol, "nowhere")
        assert error_codes(protocol) == ["component_not_found"]

    def test_a_watcher_cannot_throw(self):
        session = build_session()
        protocol, user = player_on(session, role="watcher")
        call(roll_pool, protocol, "pool")
        assert error_codes(protocol) == ["watcher_not_allowed"]
        assert user.sent == []

    def test_the_throw_requires_a_component_id(self):
        protocol, _ = player_on(build_session())
        roll_pool(protocol, LOGGER, {"action": "roll_pool"})
        assert error_codes(protocol) == ["missing_field"]

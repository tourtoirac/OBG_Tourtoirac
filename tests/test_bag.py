"""
Tests of the bag component: a fixed component holding tokens out of sight.
A token released over it joins its content, a click takes one out at random.

The actions are exercised through the message handlers with fakes rather than
the WebSocket layer, the way test_dice_pool.py does, so the bag is covered even
where autobahn is missing.
"""
import json
import logging

import pytest

from Components.bag import Bag
from Components.token import Token
from handle_message import acquire, pick, release
from session import Session, duplicate_component_ids
from user import User

LOGGER = logging.getLogger("tests")

CHIT_A = {"kind": "token", "id": "chit-a", "x": None, "y": None, "width": 40, "height": 40,
          "front_src": "/a.png", "back_src": "/back.png"}
CHIT_B = dict(CHIT_A, id="chit-b", front_src="/b.png")
BAG = {"kind": "bag", "id": "bag", "x": 100, "y": 100, "width": 120, "height": 120,
       "src": "/bag.png", "components": [CHIT_A, CHIT_B]}
ARMY = {"kind": "token", "id": "army", "x": 500, "y": 500, "width": 40, "height": 40,
        "front_src": "/army.png", "back_src": None}


def build_session(*fixed, movable=None, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": list(fixed) if fixed else [BAG],
        "movable": [ARMY] if movable is None else movable,
        **extra,
    }
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
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


def call(handler, protocol, component_id, **fields):
    handler(protocol, LOGGER, {"action": "x", "component_id": component_id, **fields})


def error_codes(protocol):
    return [message["error"]["code"] for message in protocol.sent]


def movable_ids(session):
    return [token.id for token in session.components_lists["movable"]]


class TestLoading:
    def test_the_bag_is_a_fixed_component_holding_its_tokens(self):
        session = build_session()
        (bag,) = session.components_lists["fixed"]
        assert isinstance(bag, Bag)
        assert [token.id for token in bag.components] == ["chit-a", "chit-b"]

    def test_the_tokens_of_a_bag_are_indexed_but_not_on_the_table(self):
        session = build_session()
        assert isinstance(session.get_component("bag"), Bag)
        assert isinstance(session.get_component("chit-a"), Token)
        assert movable_ids(session) == ["army"]

    def test_a_token_in_a_bag_has_no_position_and_no_green_rectangle(self):
        chit = build_session().get_component("chit-a")
        assert (chit.x, chit.y) == (None, None)
        assert chit.in_place is False

    @pytest.mark.parametrize("content", [[], None, "chits"])
    def test_a_bag_may_be_empty(self, content):
        session = build_session(dict(BAG, components=content))
        assert session.get_component("bag").components == []

    def test_a_bag_without_picture_is_accepted(self):
        bag = {key: value for key, value in BAG.items() if key != "src"}
        assert build_session(bag).get_component("bag").src is None

    def test_a_bag_only_holds_tokens(self):
        dice = {"kind": "dice", "id": "red", "x": 0, "y": 0, "width": 9, "height": 9, "src_list": ["/1.png"]}
        session = build_session(dict(BAG, components=[dice, CHIT_A]))
        assert [token.id for token in session.get_component("bag").components] == ["chit-a"]
        assert session.get_component("red") is False

    def test_a_bag_declared_outside_fixed_is_ignored(self):
        session = build_session({"kind": "counter", "id": "c", "x": 0, "y": 0, "width": 9, "height": 9},
                                movable=[BAG])
        assert session.get_component("bag") is False

    def test_the_tokens_of_a_bag_may_ask_for_copies(self):
        session = build_session(dict(BAG, components=[dict(CHIT_A, copies=3)]))
        bag = session.get_component("bag")
        assert [token.id for token in bag.components] == ["chit-a-01", "chit-a-02", "chit-a-03"]

    def test_an_id_shared_with_a_token_of_a_bag_is_a_duplicate(self):
        game_json = {"fixed": [BAG], "movable": [dict(ARMY, id="chit-b")]}
        assert duplicate_component_ids(game_json) == ["chit-b"]


class TestPick:
    def test_a_pick_puts_a_random_token_in_the_hand_of_the_player(self, monkeypatch):
        monkeypatch.setattr("Components.bag.random.choice", lambda tokens: tokens[1])
        session = build_session()
        protocol, user = player_on(session)

        call(pick, protocol, "bag", x=150, y=160, request_id="r1")

        chit = session.get_component("chit-b")
        assert [token.id for token in session.get_component("bag").components] == ["chit-a"]
        # back on the table, above the others, centered on the click
        assert movable_ids(session) == ["army", "chit-b"]
        assert (chit.x, chit.y) == (130, 140)
        assert chit.acquired_by is user
        assert user.acquired == ["chit-b"]
        (event,) = user.sent
        assert event["event"] == "pick"
        assert event["component_id"] == "bag"
        assert event["user"] == user.id
        # the client recognises its own request
        assert event["request_id"] == "r1"
        assert event["component_json"]["id"] == "chit-b"
        assert (event["component_json"]["x"], event["component_json"]["y"]) == (130, 140)

    @pytest.mark.parametrize("point", [{}, {"x": "12", "y": 5}, {"x": True, "y": 5}])
    def test_without_a_usable_point_the_token_comes_out_on_the_bag(self, point):
        session = build_session(dict(BAG, components=[CHIT_A]))
        protocol, _ = player_on(session)
        call(pick, protocol, "bag", **point)
        chit = session.get_component("chit-a")
        assert (chit.x, chit.y) == (140, 140)

    def test_every_token_comes_out_once_then_the_bag_is_empty(self):
        session = build_session()
        protocol, user = player_on(session)
        call(pick, protocol, "bag")
        call(pick, protocol, "bag")
        assert sorted(movable_ids(session)) == ["army", "chit-a", "chit-b"]
        call(pick, protocol, "bag")
        assert error_codes(protocol) == ["bag_empty"]
        assert len(user.sent) == 2

    def test_only_a_bag_is_picked_from(self):
        session = build_session()
        protocol, user = player_on(session)
        call(pick, protocol, "army")
        assert error_codes(protocol) == ["component_not_a_bag"]
        assert user.sent == []

    def test_a_watcher_does_not_pick(self):
        session = build_session()
        protocol, _ = player_on(session, role="watcher")
        call(pick, protocol, "bag")
        assert error_codes(protocol) == ["watcher_not_allowed"]
        assert len(session.get_component("bag").components) == 2

    def test_nothing_is_picked_before_the_game_starts(self):
        session = build_session()
        session.started = False
        protocol, _ = player_on(session)
        call(pick, protocol, "bag")
        assert error_codes(protocol) == ["session_not_started"]
        assert len(session.get_component("bag").components) == 2

    def test_a_token_inside_a_bag_cannot_be_acquired(self):
        session = build_session()
        protocol, user = player_on(session)
        call(acquire, protocol, "chit-a")
        assert error_codes(protocol) == ["component_in_bag"]
        assert session.get_component("chit-a").acquired_by is None
        assert user.sent == []

    def test_a_picked_token_is_released_like_any_other(self):
        session = build_session(dict(BAG, components=[CHIT_A]))
        protocol, user = player_on(session)
        call(pick, protocol, "bag")
        call(release, protocol, "chit-a", x=600, y=610)
        chit = session.get_component("chit-a")
        assert (chit.x, chit.y) == (600, 610)
        assert chit.acquired_by is None
        assert chit.in_place is False
        assert user.sent[-1]["bag_id"] is None


class TestDrop:
    def test_a_token_released_over_the_bag_joins_its_content(self):
        session = build_session()
        protocol, user = player_on(session)
        call(acquire, protocol, "army")
        # the center of the token (150, 150) lies on the bag
        call(release, protocol, "army", x=130, y=130)

        army = session.get_component("army")
        bag = session.get_component("bag")
        assert [token.id for token in bag.components] == ["chit-a", "chit-b", "army"]
        assert movable_ids(session) == []
        assert (army.x, army.y) == (None, None)
        assert (army.initial_x, army.initial_y) == (None, None)
        assert army.in_place is False
        assert army.acquired_by is None
        event = user.sent[-1]
        assert event["event"] == "release"
        assert event["success"] is True
        assert event["bag_id"] == "bag"
        assert event["component_json"]["x"] is None

    def test_a_token_released_beside_the_bag_stays_on_the_table(self):
        session = build_session()
        protocol, user = player_on(session)
        call(acquire, protocol, "army")
        # the token overlaps the bag but its center (90, 150) is outside
        call(release, protocol, "army", x=70, y=130)
        assert movable_ids(session) == ["army"]
        assert len(session.get_component("bag").components) == 2
        assert user.sent[-1]["bag_id"] is None

    def test_a_token_that_is_not_held_does_not_fall_into_the_bag(self):
        session = build_session()
        protocol, user = player_on(session)
        call(release, protocol, "army", x=130, y=130)
        assert movable_ids(session) == ["army"]
        assert user.sent[-1]["success"] is False
        assert user.sent[-1]["bag_id"] is None

    def test_a_dropped_token_can_be_picked_again(self):
        session = build_session(dict(BAG, components=[]))
        protocol, user = player_on(session)
        call(acquire, protocol, "army")
        call(release, protocol, "army", x=130, y=130)
        call(pick, protocol, "bag", x=300, y=300)
        army = session.get_component("army")
        assert movable_ids(session) == ["army"]
        assert (army.x, army.y) == (280, 280)
        assert army.acquired_by is user


class TestSetupAndSave:
    def test_the_setup_moves_the_bag(self):
        session = build_session(setup=[{"component_id": "bag", "x": 10, "y": 20}])
        (state,) = session.apply_setup()
        assert (state["id"], state["x"], state["y"]) == ("bag", 10, 20)

    def test_the_setup_does_not_place_a_token_inside_a_bag(self):
        session = build_session(setup=[{"component_id": "chit-a", "x": 10, "y": 20, "side": "back"}])
        session.apply_setup()
        chit = session.get_component("chit-a")
        assert (chit.x, chit.y) == (None, None)
        # but it chooses the face the token will show when it comes out
        assert chit.side == "back"

    def test_fix_positions_leaves_the_tokens_of_a_bag_alone(self):
        session = build_session()
        session.fix_positions()
        assert session.get_component("chit-a").in_place is False

    def test_the_saved_state_keeps_the_content_of_the_bag(self):
        session = build_session()
        protocol, _ = player_on(session)
        call(acquire, protocol, "army")
        call(release, protocol, "army", x=130, y=130)

        state = json.loads(json.dumps(session.game_json_state()))
        (bag,) = state["fixed"]
        assert [token["id"] for token in bag["components"]] == ["chit-a", "chit-b", "army"]
        assert all(token["x"] is None and token["y"] is None for token in bag["components"])
        assert state["movable"] == []

    def test_a_resumed_session_finds_its_bag_as_it_was(self, monkeypatch):
        monkeypatch.setattr("Components.bag.random.choice", lambda tokens: tokens[0])
        session = build_session()
        protocol, _ = player_on(session)
        call(pick, protocol, "bag", x=300, y=300)
        call(release, protocol, "chit-a", x=300.4, y=300.6)

        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert [token.id for token in resumed.get_component("bag").components] == ["chit-b"]
        assert movable_ids(resumed) == ["army", "chit-a"]
        chit = resumed.get_component("chit-a")
        assert (chit.x, chit.y) == (300, 301)
        # still no starting square: releasing it anywhere does not snap it back
        assert chit.near_initial_position(300, 301) is False
        assert chit.in_place is False

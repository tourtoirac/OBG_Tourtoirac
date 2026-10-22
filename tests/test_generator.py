"""
Tests of the generator component: a fixed component listing models, of which
a player drags new copies out. A copy released back over the generator is
destroyed.

The actions are exercised through the message handlers with fakes rather than
the WebSocket layer, the way test_bag.py does.
"""
import logging

import pytest

from Components.card import Card
from Components.generator import Generator
from Components.token import Token
from handle_message import generate, release
from session import InvalidComponentError, Session
from user import User

LOGGER = logging.getLogger("tests")

MARKER = {"kind": "token", "id": "marker", "width": 40, "height": 40,
          "front_src": "/marker.png", "back_src": "/marker-back.png", "side": "back"}
CARD = {"kind": "card", "id": "event", "width": 60, "height": 90,
        "front_src": "/event.png", "back_src": "/event-back.png"}
GENERATOR = {"kind": "generator", "id": "supply", "components": [MARKER, CARD]}
ARMY = {"kind": "token", "id": "army", "x": 500, "y": 500, "width": 40, "height": 40,
        "front_src": "/army.png", "back_src": None}


def build_session(*fixed, movable=None, game_json=None, **extra):
    if game_json is None:
        game_json = {
            "game": {"max_players": 2, "max_watchers": 2},
            "fixed": list(fixed) if fixed else [GENERATOR],
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
    def test_the_generator_is_a_fixed_component_listing_its_models(self):
        session = build_session()
        generator = session.get_component("supply")
        assert isinstance(generator, Generator)
        assert [model["id"] for model in generator.models] == ["marker", "event"]

    def test_the_models_are_not_components_of_the_table(self):
        session = build_session()
        assert session.get_component("marker") is False
        assert movable_ids(session) == ["army"]

    def test_only_tokens_and_cards_are_models(self):
        dice = {"kind": "dice", "id": "red", "width": 9, "height": 9, "src_list": ["/1.png"]}
        session = build_session(dict(GENERATOR, components=[dice, MARKER, {"kind": "token"}]))
        assert [model["id"] for model in session.get_component("supply").models] == ["marker"]

    @pytest.mark.parametrize("content", [[], None, "models"])
    def test_a_generator_may_list_nothing(self, content):
        session = build_session(dict(GENERATOR, components=content))
        assert session.get_component("supply").models == []

    def test_a_generator_declared_outside_fixed_is_ignored(self):
        session = build_session({"kind": "counter", "id": "c", "x": 0, "y": 0, "width": 9, "height": 9},
                                movable=[GENERATOR])
        assert session.get_component("supply") is False

    def test_a_model_missing_a_field_stops_the_build(self):
        broken = {key: value for key, value in MARKER.items() if key != "front_src"}
        with pytest.raises(InvalidComponentError) as raised:
            build_session(dict(GENERATOR, components=[broken]))
        assert raised.value.details()["parent_id"] == "supply"

    def test_the_clients_only_read_the_face_each_model_shows(self):
        (marker, card) = build_session().get_component("supply").return_json()["components"]
        # the marker shows its back, the card its front
        assert (marker["front_src"], marker["back_src"]) == (None, "/marker-back.png")
        assert (card["front_src"], card["back_src"]) == ("/event.png", None)

    def test_a_generator_of_cards_hides_faces(self):
        assert build_session().hides_faces is True
        assert build_session(dict(GENERATOR, components=[dict(MARKER, side="front")])).hides_faces is False


class TestGenerate:
    def test_a_copy_is_created_in_the_hand_of_the_player(self):
        session = build_session()
        protocol, user = player_on(session)

        call(generate, protocol, "supply", model_id="marker", x=150, y=160, request_id="r1")

        copy = session.get_component("marker-1")
        assert isinstance(copy, Token)
        # on the table, above the others, centered on the pointer
        assert movable_ids(session) == ["army", "marker-1"]
        assert (copy.x, copy.y) == (130, 140)
        assert copy.acquired_by is user
        # on the face of its model, without a starting square
        assert copy.side == "back"
        assert (copy.initial_x, copy.initial_y) == (None, None)
        (event,) = user.sent
        assert event["event"] == "generate"
        assert (event["component_id"], event["model_id"]) == ("supply", "marker")
        assert event["request_id"] == "r1"
        assert event["component_json"]["id"] == "marker-1"
        assert protocol.sent == []

    def test_the_ids_of_the_copies_count_up(self):
        session = build_session()
        protocol, user = player_on(session)
        for _ in range(3):
            call(generate, protocol, "supply", model_id="marker")
            call(release, protocol, user.acquired[0], x=0, y=0)
        call(generate, protocol, "supply", model_id="event")
        assert movable_ids(session) == ["army", "marker-1", "marker-2", "marker-3", "event-1"]
        assert isinstance(session.get_component("event-1"), Card)

    def test_an_id_already_taken_is_skipped(self):
        session = build_session(movable=[dict(ARMY, id="marker-1")])
        protocol, _ = player_on(session)
        call(generate, protocol, "supply", model_id="marker")
        assert movable_ids(session) == ["marker-1", "marker-2"]

    def test_an_unknown_model_is_refused(self):
        session = build_session()
        protocol, user = player_on(session)
        call(generate, protocol, "supply", model_id="nope")
        assert error_codes(protocol) == ["unknown_model"]
        assert user.sent == []

    def test_only_a_generator_generates(self):
        session = build_session()
        protocol, _ = player_on(session)
        call(generate, protocol, "army", model_id="marker")
        assert error_codes(protocol) == ["component_not_a_generator"]

    def test_the_model_id_is_required(self):
        protocol, _ = player_on(build_session())
        call(generate, protocol, "supply")
        assert error_codes(protocol) == ["missing_field"]

    def test_a_watcher_does_not_generate(self):
        session = build_session()
        protocol, _ = player_on(session, role="watcher")
        call(generate, protocol, "supply", model_id="marker")
        assert error_codes(protocol) == ["watcher_not_allowed"]
        assert movable_ids(session) == ["army"]

    def test_nothing_is_generated_before_the_game_starts(self):
        session = build_session()
        session.started = False
        protocol, _ = player_on(session)
        call(generate, protocol, "supply", model_id="marker")
        assert error_codes(protocol) == ["session_not_started"]

    def test_a_model_of_a_nationality_is_only_copied_by_its_players(self):
        session = build_session(dict(GENERATOR, components=[dict(MARKER, nationality="US")]))
        protocol, user = player_on(session)
        user.nationality = "NVA"
        call(generate, protocol, "supply", model_id="marker")
        assert error_codes(protocol) == ["wrong_nationality"]
        user.nationality = "US"
        call(generate, protocol, "supply", model_id="marker")
        assert movable_ids(session) == ["army", "marker-1"]


class TestReleaseOverTheGenerator:
    def test_a_copy_released_over_the_generator_is_destroyed(self):
        session = build_session()
        protocol, user = player_on(session)
        call(generate, protocol, "supply", model_id="marker")

        call(release, protocol, "marker-1", x=10, y=10, generator=True)

        assert movable_ids(session) == ["army"]
        assert session.get_component("marker-1") is False
        assert user.acquired == []
        event = user.sent[-1]
        assert event["event"] == "release"
        assert event["generator_id"] == "supply"
        assert event["success"] is True
        # gone from the saved state too
        assert [item["id"] for item in session.game_json_state()["movable"]] == ["army"]

    def test_a_destroyed_copy_does_not_give_its_id_back(self):
        session = build_session()
        protocol, _ = player_on(session)
        call(generate, protocol, "supply", model_id="marker")
        call(release, protocol, "marker-1", generator=True)
        call(generate, protocol, "supply", model_id="marker")
        assert movable_ids(session) == ["army", "marker-2"]

    def test_a_token_of_the_game_released_over_the_generator_is_put_down(self):
        session = build_session()
        protocol, user = player_on(session)
        call(release, protocol, "army")  # nothing held: refused quietly
        session.get_component("army").acquire(user)

        call(release, protocol, "army", x=300, y=320, generator=True)

        army = session.get_component("army")
        assert movable_ids(session) == ["army"]
        assert (army.x, army.y) == (300, 320)
        assert user.sent[-1]["generator_id"] is None

    def test_a_copy_released_elsewhere_stays_in_the_game(self):
        session = build_session()
        protocol, user = player_on(session)
        call(generate, protocol, "supply", model_id="marker")
        call(release, protocol, "marker-1", x=300, y=320)
        assert movable_ids(session) == ["army", "marker-1"]
        assert user.sent[-1]["generator_id"] is None


class TestSavedState:
    def test_the_copies_and_the_numbers_survive_a_resume(self):
        session = build_session()
        protocol, _ = player_on(session)
        call(generate, protocol, "supply", model_id="marker")
        call(release, protocol, "marker-1", x=300, y=320)
        call(generate, protocol, "supply", model_id="marker")
        call(release, protocol, "marker-2", generator=True)

        state = session.game_json_state()
        (saved,) = [item for item in state["fixed"] if item["id"] == "supply"]
        # the saved models keep both faces
        assert saved["components"][0]["front_src"] == "/marker.png"
        assert saved["generated"] == {"marker": 2}
        assert saved["created"] == ["marker-1"]

        resumed = build_session(game_json=state)
        assert movable_ids(resumed) == ["army", "marker-1"]
        protocol, _ = player_on(resumed)
        # a copy made before the save goes back to its generator
        resumed.get_component("marker-1").acquire(protocol.user)
        call(release, protocol, "marker-1", generator=True)
        assert movable_ids(resumed) == ["army"]
        # and the numbering goes on
        call(generate, protocol, "supply", model_id="marker")
        assert movable_ids(resumed) == ["army", "marker-3"]

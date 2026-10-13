"""
Tests of the private hand: the tokens a player keeps off the table. Only that
player knows what they are; the others know how many, and their backs.
"""
import json

from handle_message import acquire, flip, release, take
from session import Session
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes, movable_ids

ARMY = {"kind": "token", "id": "army", "x": 500, "y": 500, "width": 40, "height": 60,
        "front_src": "/army.png", "back_src": "/back.png"}
SCOUT = dict(ARMY, id="scout", x=10, y=10, front_src="/scout.png", back_src=None)


def build_session(**extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [],
        "movable": [ARMY, SCOUT],
        "options": {"private_hand": {}},
        **extra,
    }
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def two_players(session):
    alice = FakeUser("alice", session=session)
    bob = FakeUser("bob", session=session)
    session.players = [alice, bob]
    return FakeProtocol(alice), alice, FakeProtocol(bob), bob


def keep(protocol, component_id):
    """Takes a token from the table and puts it in the private hand."""
    call(acquire, protocol, component_id)
    call(release, protocol, component_id, x=0, y=0, hand=True)


class TestKeeping:
    def test_a_token_released_to_the_hand_leaves_the_table(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        keep(protocol, "army")
        army = session.get_component("army")
        assert movable_ids(session) == ["scout"]
        assert session.hands == {"alice": [army]}
        assert (army.x, army.y) == (None, None)
        assert army.acquired_by is None

    def test_everybody_learns_whose_hand_took_it_and_its_back(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        keep(protocol, "army")
        event = bob.sent[-1]
        assert event["event"] == "release"
        assert event["hand_of"] == "alice"
        assert event["hands"] == {"alice": [{"src": "/back.png", "width": 40, "height": 60}]}

    def test_a_token_without_a_back_is_shown_without_picture(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        keep(protocol, "scout")
        assert session.hands_json() == {"alice": [{"src": None, "width": 40, "height": 60}]}

    def test_a_token_of_a_nationality_never_shows_its_back(self):
        session = build_session(movable=[dict(ARMY, nationality="US")])
        session.hands["alice"] = [session.get_component("army")]
        assert session.hands_json()["alice"][0]["src"] is None

    def test_an_ordinary_release_goes_to_no_hand(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(acquire, protocol, "army")
        call(release, protocol, "army", x=5, y=5)
        assert alice.sent[-1]["hand_of"] is None
        assert session.hands == {}

    def test_a_token_that_is_not_held_does_not_go_to_the_hand(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(release, protocol, "army", x=0, y=0, hand=True)
        assert session.hands == {}
        assert alice.sent[-1]["hand_of"] is None
        assert movable_ids(session) == ["army", "scout"]

    def test_a_token_in_a_hand_is_out_of_reach(self):
        session = build_session()
        protocol, _, other, _ = two_players(session)
        keep(protocol, "army")
        call(acquire, other, "army")
        call(flip, other, "army")
        call(acquire, protocol, "army")
        assert error_codes(other) == ["component_in_hand", "component_in_hand"]
        assert error_codes(protocol) == ["component_in_hand"]

    def test_the_holder_turns_over_a_token_of_its_hand(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        keep(protocol, "army")
        told = len(bob.sent)
        call(flip, protocol, "army")
        assert error_codes(protocol) == []
        assert session.get_component("army").side == "back"
        assert alice.sent[-1]["event"] == "flip"
        assert alice.sent[-1]["side"] == "back"
        # nobody else is told: the hand is private
        assert len(bob.sent) == told

    def test_a_token_keeps_its_side_when_it_joins_a_hand(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        call(acquire, protocol, "army")
        call(flip, protocol, "army")
        call(release, protocol, "army", x=0, y=0, hand=True)
        assert session.get_component("army").side == "back"


class TestPlaying:
    def test_the_holder_plays_the_token_back_on_the_table(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        keep(protocol, "army")
        call(take, protocol, "army", x=120, y=130, request_id="r1")
        army = session.get_component("army")
        assert movable_ids(session) == ["scout", "army"]
        assert (army.x, army.y) == (100, 100)
        assert army.acquired_by is alice
        assert session.hands == {}
        event = bob.sent[-1]
        assert event["event"] == "take"
        assert event["hand_of"] == "alice"
        assert event["request_id"] == "r1"
        assert event["hands"] == {}
        assert event["component_json"]["id"] == "army"

    def test_nobody_plays_from_the_hand_of_another(self):
        session = build_session()
        protocol, _, other, _ = two_players(session)
        keep(protocol, "army")
        call(take, other, "army", x=0, y=0)
        assert error_codes(other) == ["component_not_in_hand"]
        assert list(session.hands) == ["alice"]

    def test_a_token_on_the_table_is_not_played_from_a_hand(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        call(take, protocol, "army", x=0, y=0)
        assert error_codes(protocol) == ["component_not_in_hand"]

    def test_a_game_not_started_refuses_it(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        keep(protocol, "army")
        session.started = False
        call(take, protocol, "army", x=0, y=0)
        assert error_codes(protocol) == ["session_not_started"]


class TestDescribing:
    def test_only_the_holder_reads_the_faces_of_its_hand(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        keep(protocol, "army")
        mine = session.return_session_json(alice)
        theirs = session.return_session_json(bob)
        assert [token["id"] for token in mine["hand"]] == ["army"]
        assert theirs["hand"] == []
        assert theirs["hands"] == mine["hands"] == {
            "alice": [{"src": "/back.png", "width": 40, "height": 60}],
        }
        assert "army" not in json.dumps(theirs["components"])

    def test_a_watcher_has_no_hand(self):
        session = build_session()
        session.hands["carol"] = [session.get_component("army")]
        carol = FakeUser("carol", role="watcher", session=session)
        assert session.return_session_json(carol)["hand"] == []

    def test_the_hand_survives_a_saved_session(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        keep(protocol, "army")
        state = json.loads(json.dumps(session.game_json_state()))
        assert [token["id"] for token in state["hands"]["alice"]] == ["army"]
        resumed = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert movable_ids(resumed) == ["scout"]
        assert [token.id for token in resumed.hands["alice"]] == ["army"]
        assert resumed.hand_holding(resumed.get_component("army")) == "alice"

    def test_a_session_without_hands_stores_none(self):
        assert "hands" not in build_session().game_json_state()

    def test_the_setup_does_not_place_a_token_kept_in_a_hand(self):
        session = build_session(setup=[{"component_id": "army", "x": 7, "y": 7}])
        protocol, _, _, _ = two_players(session)
        keep(protocol, "army")
        session.apply_setup()
        assert session.get_component("army").x is None


class TestOption:
    """The private hand only exists when options.private_hand is an object."""

    def test_without_the_option_a_token_released_to_the_hand_stays_on_the_table(self):
        for options in ({}, {"private_hand": None}, {"private_hand": False},
                        {"private_hand": True}, {"private_hand": "true"}):
            session = build_session(options=options)
            protocol, _, _, bob = two_players(session)
            call(acquire, protocol, "army")
            call(release, protocol, "army", x=120, y=130, hand=True)
            army = session.get_component("army")
            assert session.hands == {}
            assert (army.x, army.y) == (120, 130)
            assert "army" in movable_ids(session)
            assert bob.sent[-1]["hand_of"] is None

    def test_an_object_turns_the_private_hand_on(self):
        for value in ({}, {"anything": 1}):
            session = build_session(options={"private_hand": value})
            protocol, _, _, _ = two_players(session)
            keep(protocol, "army")
            assert list(session.hands) == ["alice"]

    def test_without_the_option_nothing_is_taken_from_a_hand(self):
        session = build_session(options={"private_hand": None})
        protocol, *_ = two_players(session)
        call(take, protocol, "army", x=0, y=0)
        assert error_codes(protocol) == ["private_hand_disabled"]

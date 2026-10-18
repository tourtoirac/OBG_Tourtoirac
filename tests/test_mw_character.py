"""
Tests of the mw_character: a card that never moves, with a column of boxes on each
side. A click on a box of the left column adds a slash, then a cross, then
empties it; on the right column it draws a circle, then empties it. The card
may be turned over; its boxes only take clicks on its front.
"""
import json

from handle_message import acquire, flip, mark, pick, release, take
from session import Session
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes

CHARACTER = {
    "kind": "mw_character", "id": "frodo", "x": 100, "y": 200, "width": 188, "height": 286,
    "front_src": "/frodo.jpg", "back_src": "/frodo_back.jpg",
    "left": {"icon": "/life.svg", "boxes": 4},
    "right": {"icon": "/ring.svg", "boxes": 3},
}


def card_json(name, **fields):
    return {"kind": "card", "id": name, "x": 900, "y": 900, "width": 60, "height": 90,
            "front_src": f"/{name}.png", "back_src": "/back.png", **fields}


def build_session(character=None, nationalities=None, movable=None, options=None):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "options": {"nationalities": nationalities} if nationalities else (options or {}),
        "fixed": [CHARACTER if character is None else character],
        "movable": movable if movable is not None else [],
    }
    return Session(
        user=None, name="Ring", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def two_players(session):
    alice = FakeUser("alice", session=session)
    bob = FakeUser("bob", session=session)
    session.players = [alice, bob]
    return FakeProtocol(alice), alice, FakeProtocol(bob), bob


def marks(session, column):
    return session.get_component("frodo").return_json()[column]["marks"]


class TestReading:
    def test_a_character_is_fixed_and_starts_empty(self):
        session = build_session()
        character = session.get_component("frodo")
        assert character in session.components_lists["fixed"]
        state = character.return_json()
        assert state["kind"] == "mw_character"
        assert state["side"] == "front"
        assert state["left"] == {"icon": "/life.svg", "boxes": 4, "marks": [0, 0, 0, 0]}
        assert state["right"] == {"icon": "/ring.svg", "boxes": 3, "marks": [0, 0, 0]}

    def test_the_boxes_are_kept_between_3_and_6(self):
        character = dict(CHARACTER, left={"boxes": 1}, right={"boxes": 9})
        state = build_session(character).get_component("frodo").return_json()
        assert (state["left"]["boxes"], state["right"]["boxes"]) == (3, 6)
        assert state["left"]["icon"] is None

    def test_a_badly_written_column_is_left_out(self):
        character = dict(CHARACTER, left={"boxes": "4"}, right=None)
        state = build_session(character).get_component("frodo").return_json()
        assert "left" not in state and "right" not in state

    def test_a_character_outside_fixed_is_ignored(self):
        game_json = {"game": {"max_players": 2, "max_watchers": 2}, "fixed": [], "movable": [CHARACTER]}
        session = Session(user=None, name="Ring", key="K", code="C", active=True, variant="std",
                          game_json=game_json, started=True)
        assert not session.get_component("frodo")


class TestMarking:
    def test_a_left_box_goes_slash_cross_empty(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        values = []
        for _ in range(3):
            call(mark, protocol, "frodo", column="left", index=1)
            values.append(marks(session, "left")[1])
        assert values == [1, 2, 0]
        call(mark, protocol, "frodo", column="left", index=1)
        expected = {"event": "mark", "component_id": "frodo", "column": "left", "index": 1, "value": 1,
                    "nickname": "alice"}
        assert alice.sent[-1] == expected and bob.sent[-1] == expected

    def test_a_right_box_goes_circle_empty(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(mark, protocol, "frodo", column="right", index=2)
        assert marks(session, "right") == [0, 0, 1]
        call(mark, protocol, "frodo", column="right", index=2)
        assert marks(session, "right") == [0, 0, 0]

    def test_an_unknown_box_is_refused(self):
        session = build_session(dict(CHARACTER, right=None))
        protocol, *_ = two_players(session)
        for column, index in (("left", 4), ("left", -1), ("left", "1"), ("left", True), ("middle", 0),
                              ("right", 0)):
            call(mark, protocol, "frodo", column=column, index=index)
        assert error_codes(protocol) == ["unknown_box"] * 6

    def test_the_back_takes_no_mark(self):
        session = build_session(dict(CHARACTER, side="back"))
        protocol, *_ = two_players(session)
        call(mark, protocol, "frodo", column="left", index=0)
        assert error_codes(protocol) == ["character_face_down"]

    def test_another_component_is_not_marked(self):
        session = build_session(dict(CHARACTER, kind="counter", color="#ffffff", value=0))
        protocol, *_ = two_players(session)
        call(mark, protocol, "frodo", column="left", index=0)
        assert error_codes(protocol) == ["component_not_markable"]

    def test_a_watcher_does_not_mark(self):
        session = build_session()
        two_players(session)
        watcher = FakeUser("carol", role="watcher", session=session)
        session.watchers = [watcher]
        protocol = FakeProtocol(watcher)
        call(mark, protocol, "frodo", column="left", index=0)
        assert error_codes(protocol) == ["watcher_not_allowed"]


class TestNationality:
    def test_only_the_players_of_its_nationality_mark_and_flip_it(self):
        session = build_session(dict(CHARACTER, nationality="Free"), nationalities=["Free", "Shadow"])
        alice_protocol, alice, bob_protocol, bob = two_players(session)
        alice.nationality, bob.nationality = "Free", "Shadow"
        call(mark, bob_protocol, "frodo", column="left", index=0)
        call(flip, bob_protocol, "frodo")
        assert error_codes(bob_protocol) == ["wrong_nationality", "wrong_nationality"]
        call(mark, alice_protocol, "frodo", column="left", index=0)
        assert marks(session, "left")[0] == 1

    def test_without_nationality_anybody_marks_it(self):
        session = build_session()
        _, _, protocol, bob = two_players(session)
        bob.nationality = "Shadow"
        call(mark, protocol, "frodo", column="right", index=0)
        assert marks(session, "right")[0] == 1


class TestFlipping:
    def test_a_double_click_turns_it_over_for_everybody(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(flip, protocol, "frodo")
        assert session.get_component("frodo").side == "back"
        assert bob.sent[-1]["event"] == "flip" and bob.sent[-1]["side"] == "back"
        call(flip, protocol, "frodo")
        assert session.get_component("frodo").side == "front"

    def test_a_character_without_back_does_not_turn(self):
        session = build_session(dict(CHARACTER, back_src=None))
        protocol, *_ = two_players(session)
        call(flip, protocol, "frodo")
        assert error_codes(protocol) == ["component_not_flippable"]

    def test_it_is_never_taken_in_hand(self):
        session = build_session()
        protocol, alice, *_ = two_players(session)
        call(acquire, protocol, "frodo")
        assert alice.sent[-1]["success"] is False
        assert (session.get_component("frodo").x, session.get_component("frodo").y) == (100, 200)


class TestSaving:
    def test_the_saved_state_keeps_the_marks_and_the_side(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(mark, protocol, "frodo", column="left", index=0)
        call(mark, protocol, "frodo", column="left", index=0)
        call(mark, protocol, "frodo", column="right", index=1)
        call(flip, protocol, "frodo")
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(user=None, name="Ring", key="KEY1", code="CODE1",
                          active=True, variant="std", game_json=state, started=True)
        character = resumed.get_component("frodo").return_json()
        assert character["left"]["marks"] == [2, 0, 0, 0]
        assert character["right"]["marks"] == [0, 1, 0]
        assert character["side"] == "back"

    def test_saved_marks_badly_written_start_empty(self):
        character = dict(CHARACTER, left={"boxes": 4, "marks": [2, 3, "x", True, 1]})
        assert build_session(character).get_component("frodo").return_json()["left"]["marks"] == [2, 0, 0, 0]


def drop_on_character(protocol, card_id, session, at=(150, 300)):
    """Takes a card from the table and releases it, centered on at."""
    card = session.get_component(card_id)
    call(acquire, protocol, card_id)
    call(release, protocol, card_id, x=at[0] - card.width / 2, y=at[1] - card.height / 2)


def under(session):
    return [card.id for card in session.get_component("frodo").cards]


class TestCardsUnder:
    CARDS = [card_json("sting", side="back"), card_json("mithril", orientation=90, orientable=True)]

    def test_a_card_released_on_it_goes_under_face_up(self):
        session = build_session(movable=self.CARDS)
        protocol, alice, _, bob = two_players(session)
        drop_on_character(protocol, "sting", session)
        drop_on_character(protocol, "mithril", session)
        assert under(session) == ["sting", "mithril"]
        assert not any(card.id in ("sting", "mithril") for card in session.components_lists["movable"])
        sting, mithril = session.get_component("sting"), session.get_component("mithril")
        assert (sting.side, mithril.orientation) == ("front", 0)
        event = bob.sent[-1]
        assert event["event"] == "release" and event["character_id"] == "frodo"
        # everybody reads the fronts of the cards under it
        assert [card["front_src"] for card in event["character_json"]["cards"]] == ["/sting.png", "/mithril.png"]

    def test_a_token_or_a_card_elsewhere_stays_on_the_table(self):
        token = {"kind": "token", "id": "army", "x": 900, "y": 900, "width": 40, "height": 40,
                 "front_src": "/army.png", "back_src": None}
        session = build_session(movable=[*self.CARDS, token])
        protocol, *_ = two_players(session)
        drop_on_character(protocol, "army", session)
        drop_on_character(protocol, "sting", session, at=(1500, 1500))
        assert under(session) == []
        assert session.get_component("army") in session.components_lists["movable"]

    def test_the_last_card_put_is_taken_first(self):
        session = build_session(movable=self.CARDS)
        protocol, alice, _, bob = two_players(session)
        drop_on_character(protocol, "sting", session)
        drop_on_character(protocol, "mithril", session)
        call(pick, protocol, "frodo", x=500, y=500, request_id="r1")
        card = session.get_component("mithril")
        assert under(session) == ["sting"]
        assert card in session.components_lists["movable"]
        assert card.acquired_by is alice
        assert (card.x + card.width / 2, card.y + card.height / 2) == (500, 500)
        event = bob.sent[-1]
        assert event["event"] == "pick" and event["request_id"] == "r1"
        assert event["component_json"]["id"] == "mithril"
        assert len(event["character_json"]["cards"]) == 1

    def test_an_empty_character_gives_nothing(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(pick, protocol, "frodo", x=500, y=500)
        assert error_codes(protocol) == ["character_empty"]

    def test_a_card_under_it_is_out_of_reach(self):
        session = build_session(movable=self.CARDS)
        protocol, *_ = two_players(session)
        drop_on_character(protocol, "sting", session)
        call(acquire, protocol, "sting")
        call(flip, protocol, "sting")
        assert error_codes(protocol) == ["component_under_character"] * 2

    def test_only_its_nationality_puts_and_takes_cards(self):
        session = build_session(dict(CHARACTER, nationality="Free"), nationalities=["Free", "Shadow"],
                                movable=self.CARDS)
        alice_protocol, alice, bob_protocol, bob = two_players(session)
        alice.nationality, bob.nationality = "Free", "Shadow"
        drop_on_character(bob_protocol, "sting", session)
        assert under(session) == []
        drop_on_character(alice_protocol, "mithril", session)
        call(pick, bob_protocol, "frodo", x=500, y=500)
        assert error_codes(bob_protocol) == ["wrong_nationality"]
        assert under(session) == ["mithril"]

    def test_the_saved_state_keeps_the_cards(self):
        session = build_session(movable=self.CARDS)
        protocol, *_ = two_players(session)
        drop_on_character(protocol, "sting", session)
        drop_on_character(protocol, "mithril", session)
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(user=None, name="Ring", key="KEY1", code="CODE1",
                          active=True, variant="std", game_json=state, started=True)
        assert under(resumed) == ["sting", "mithril"]
        assert resumed.get_component("sting").side == "front"
        assert resumed.get_component("sting") not in resumed.components_lists["movable"]

    def test_the_game_may_declare_cards_under_it(self):
        character = dict(CHARACTER, cards=[{"kind": "card", "id": "ring", "front_src": "/ring.png"}])
        session = build_session(character)
        ring = session.get_component("ring")
        assert under(session) == ["ring"]
        assert (ring.width, ring.height) == (188, 286)

    def test_a_card_id_under_it_shares_the_namespace(self):
        from session import duplicate_component_ids
        character = dict(CHARACTER, cards=[card_json("sting")])
        assert duplicate_component_ids({"fixed": [character], "movable": [card_json("sting")]}) == ["sting"]

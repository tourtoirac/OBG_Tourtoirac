"""
Tests of the area a player shows cards of its private hand in: the players it
chose read them as they are, the others and the watchers read nothing, and
the cards never leave the hand.
"""
import json

from handle_message import flip, give, show_add, show_close, show_open, show_remove, take
from session import Session
from tests.test_bag import LOGGER, FakeProtocol, FakeUser, call, error_codes
from tests.test_private_hand import ARMY, SCOUT, keep

SHOWING = {"private_hand": {"show_hands_to_other_players": True}}


def build_session(options=None):
    game_json = {
        "game": {"max_players": 3, "max_watchers": 2},
        "fixed": [],
        "movable": [ARMY, SCOUT],
        "options": SHOWING if options is None else options,
    }
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def table(session):
    """Three players and a watcher; alice keeps the army in her hand."""
    users = [FakeUser(name, session=session) for name in ("alice", "bob", "carol")]
    session.players = list(users)
    dave = FakeUser("dave", role="watcher", session=session)
    session.watchers = [dave]
    protocols = [FakeProtocol(user) for user in users]
    keep(protocols[0], "army")
    for user in users + [dave]:
        user.sent.clear()
    return protocols, users, dave


def ask(handler, protocol, **fields):
    handler(protocol, LOGGER, {"action": "x", **fields})


def shown(user):
    """The last thing user was told of an area."""
    return [message for message in user.sent if message["event"] == "show"][-1]


ARMY_FRONT = {"src": "/army.png", "width": 40, "height": 60}


class TestOption:
    def test_only_true_in_the_private_hand_turns_it_on(self):
        assert build_session().show_hands_enabled()
        for options in ({}, {"private_hand": {}}, {"private_hand": None},
                        {"private_hand": {"show_hands_to_other_players": "true"}},
                        {"show_hands_to_other_players": True}):
            assert not build_session(options).show_hands_enabled()

    def test_without_the_option_nothing_is_shown(self):
        session = build_session({"private_hand": {}})
        (alice, _, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        assert error_codes(alice) == ["show_hands_disabled"]
        assert session.shows == {}


class TestShowing:
    def test_a_card_shown_to_all_is_read_by_every_player(self):
        session = build_session()
        (alice, _, _), users, dave = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        assert error_codes(alice) == []
        for user in users:
            assert shown(user)["owner"] == "alice"
            assert shown(user)["to"] == "all"
            assert shown(user)["cards"] == [ARMY_FRONT]
        # the watchers are told nothing
        assert dave.sent == []

    def test_only_the_owner_gets_the_ids(self):
        session = build_session()
        (alice, _, _), (a, b, _), _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        assert shown(a)["component_ids"] == ["army"]
        assert shown(b)["component_ids"] == []
        assert "army" not in json.dumps(shown(b)).replace("/army.png", "")

    def test_a_card_shown_to_one_player_stays_unknown_to_the_others(self):
        session = build_session()
        (alice, _, _), (a, b, c), _ = table(session)
        ask(show_open, alice, to="bob")
        ask(show_add, alice, component_id="army")
        assert shown(a)["cards"] == shown(b)["cards"] == [ARMY_FRONT]
        assert shown(c) == {"event": "show", "owner": "alice", "to": None,
                            "cards": [], "ranks": [], "component_ids": []}

    def test_the_card_is_shown_as_it_is_and_turns_with_the_hand(self):
        session = build_session()
        (alice, _, _), (_, b, _), _ = table(session)
        call(flip, alice, "army")
        ask(show_open, alice, to="bob")
        ask(show_add, alice, component_id="army")
        assert shown(b)["cards"] == [{"src": "/back.png", "width": 40, "height": 60}]
        call(flip, alice, "army")
        assert shown(b)["cards"] == [ARMY_FRONT]

    def test_the_card_stays_in_the_hand(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        army = session.get_component("army")
        assert session.hands == {"alice": [army]}
        assert session.shows["alice"]["cards"] == [army]

    def test_a_card_is_shown_once(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        ask(show_add, alice, component_id="army")
        assert len(session.shows["alice"]["cards"]) == 1

    def test_changing_who_reads_keeps_the_cards(self):
        session = build_session()
        (alice, _, _), (_, b, c), _ = table(session)
        ask(show_open, alice, to="bob")
        ask(show_add, alice, component_id="army")
        ask(show_open, alice, to="carol")
        assert shown(b)["to"] is None
        assert shown(c)["cards"] == [ARMY_FRONT]


class TestRefusals:
    def test_the_hand_is_shown_to_all_or_to_another_player(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        for to in ("alice", "dave", "nobody", None, 3):
            ask(show_open, alice, to=to)
        assert error_codes(alice) == ["unknown_player"] * 5
        assert session.shows == {}

    def test_nothing_is_added_before_the_area_is_open(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_add, alice, component_id="army")
        assert error_codes(alice) == ["show_not_open"]

    def test_only_a_card_of_the_own_hand_is_shown(self):
        session = build_session()
        (alice, bob, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_open, bob, to="all")
        ask(show_add, alice, component_id="scout")
        ask(show_add, alice, component_id="unknown")
        ask(show_add, alice)
        ask(show_add, bob, component_id="army")
        assert error_codes(alice) == ["component_not_in_hand"] * 3
        assert error_codes(bob) == ["component_not_in_hand"]

    def test_a_watcher_shows_nothing(self):
        session = build_session()
        _, _, dave = table(session)
        protocol = FakeProtocol(dave)
        ask(show_open, protocol, to="all")
        assert error_codes(protocol) == ["watcher_not_allowed"]


class TestClosing:
    def test_closing_gives_the_cards_back_to_the_hand_alone(self):
        session = build_session()
        (alice, _, _), users, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        ask(show_close, alice)
        assert session.shows == {}
        assert session.hands == {"alice": [session.get_component("army")]}
        for user in users:
            assert shown(user)["to"] is None
            assert shown(user)["cards"] == []

    def test_closing_turns_the_cards_back_on_their_front(self):
        session = build_session()
        (alice, _, _), (a, b, _), _ = table(session)
        ask(show_open, alice, to="bob")
        ask(show_add, alice, component_id="army")
        call(flip, alice, "army")
        a.sent.clear()
        b.sent.clear()
        ask(show_close, alice)
        army = session.get_component("army")
        assert army.side == "front"
        flips = [message for message in a.sent if message["event"] == "flip"]
        assert [(m["component_id"], m["side"]) for m in flips] == [("army", "front")]
        assert not any(message["event"] == "flip" for message in b.sent)

    def test_closing_leaves_a_front_card_untouched(self):
        session = build_session()
        (alice, _, _), (a, _, _), _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        a.sent.clear()
        ask(show_close, alice)
        assert not any(message["event"] == "flip" for message in a.sent)

    def test_closing_what_is_not_open_tells_nobody(self):
        session = build_session()
        (alice, _, _), users, _ = table(session)
        ask(show_close, alice)
        assert error_codes(alice) == []
        assert all(user.sent == [] for user in users)

    def test_a_card_played_leaves_the_area(self):
        session = build_session()
        (alice, _, _), (_, b, _), _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        call(take, alice, "army", x=100, y=100)
        assert session.shows["alice"]["cards"] == []
        assert shown(b)["cards"] == []


    def test_a_card_given_leaves_the_area(self):
        session = build_session()
        (alice, _, _), (_, b, _), _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        call(give, alice, "army", to="bob")
        assert session.shows["alice"]["cards"] == []
        assert shown(b)["cards"] == []


class TestRanks:
    def showing_both(self, session):
        """alice shows the army, then the scout, to everyone."""
        (alice, _, _), users, dave = table(session)
        keep(alice, "scout")
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        ask(show_add, alice, component_id="scout")
        return alice, users, dave

    def test_cards_are_ranked_in_the_order_they_are_shown(self):
        session = build_session()
        _, (a, b, _), _ = self.showing_both(session)
        assert shown(a)["ranks"] == shown(b)["ranks"] == [1, 2]

    def test_a_card_leaving_the_area_keeps_the_others_ranks(self):
        session = build_session()
        alice, (_, b, _), _ = self.showing_both(session)
        call(take, alice, "army", x=100, y=100)
        assert shown(b)["ranks"] == [2]

    def test_a_rank_is_never_given_twice(self):
        session = build_session()
        alice, (a, _, _), _ = self.showing_both(session)
        ask(show_remove, alice, component_id="army")
        ask(show_add, alice, component_id="army")
        assert shown(a)["component_ids"] == ["scout", "army"]
        assert shown(a)["ranks"] == [2, 3]

    def test_closing_the_area_starts_the_ranks_again(self):
        session = build_session()
        alice, (a, _, _), _ = self.showing_both(session)
        ask(show_close, alice)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="scout")
        assert shown(a)["ranks"] == [1]

    def test_the_closed_area_has_no_rank(self):
        session = build_session()
        _, _, dave = self.showing_both(session)
        assert session.show_json("alice", dave)["ranks"] == []


class TestRemoving:
    def test_a_card_taken_back_stays_in_the_hand(self):
        session = build_session()
        (alice, _, _), (_, b, _), _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        ask(show_remove, alice, component_id="army")
        army = session.get_component("army")
        assert session.hands == {"alice": [army]}
        assert session.shows["alice"]["cards"] == []
        assert shown(b)["cards"] == []

    def test_a_card_not_shown_is_refused(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_remove, alice, component_id="army")
        assert error_codes(alice) == ["component_not_shown"]

    def test_nothing_is_removed_without_an_area(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_remove, alice, component_id="army")
        assert error_codes(alice) == ["component_not_shown"]


class TestDescribing:
    def test_a_player_joining_reads_the_areas_meant_for_it(self):
        session = build_session()
        (alice, _, _), (a, b, c), dave = table(session)
        ask(show_open, alice, to="bob")
        ask(show_add, alice, component_id="army")
        assert session.return_session_json(b)["shows"][0]["cards"] == [ARMY_FRONT]
        assert session.return_session_json(a)["shows"][0]["component_ids"] == ["army"]
        assert session.return_session_json(c)["shows"] == []
        assert session.return_session_json(dave)["shows"] == []

    def test_the_area_is_not_saved(self):
        session = build_session()
        (alice, _, _), _, _ = table(session)
        ask(show_open, alice, to="all")
        ask(show_add, alice, component_id="army")
        state = session.game_json_state()
        assert "shows" not in state
        assert [token["id"] for token in state["hands"]["alice"]] == ["army"]

"""
Tests of the deck and of its cards. A deck is a fixed component holding cards
only, in one pile ("single") or in a draw pile and a discard pile ("duo"). A
card is a token whose front stays on the server while it lies on its back.

The actions are exercised through the message handlers with the fakes of
test_bag.py, so the deck is covered even where autobahn is missing.
"""
import json

from Components.card import Card
from Components.deck import DECK_GAP, Deck
from handle_message import acquire, draw, flip, refill, release, shuffle, take
from session import Session, duplicate_component_ids
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes, movable_ids


def card_json(name, **fields):
    return {"kind": "card", "id": name, "front_src": f"/{name}.png", **fields}


ACE, KING, QUEEN = card_json("ace"), card_json("king"), card_json("queen")
DECK = {"kind": "deck", "id": "deck", "type": "duo", "x": 100, "y": 100, "width": 60, "height": 90,
        "back_src": "/back.png", "components": [ACE, KING, QUEEN]}
JOKER = card_json("joker", x=500, y=500, width=60, height=90, back_src="/back.png")
ARMY = {"kind": "token", "id": "army", "x": 700, "y": 700, "width": 40, "height": 40,
        "front_src": "/army.png", "back_src": None}


def build_session(deck=None, movable=None, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [DECK if deck is None else deck],
        "movable": [JOKER, ARMY] if movable is None else movable,
        "options": {"private_hand": True},
        **extra,
    }
    return Session(
        user=None, name="JudgeDredd", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def two_players(session):
    alice = FakeUser("alice", session=session)
    bob = FakeUser("bob", session=session)
    session.players = [alice, bob]
    return FakeProtocol(alice), alice, FakeProtocol(bob), bob


def pile_ids(session, pile="draw", deck_id="deck"):
    return [card.id for card in session.get_component(deck_id).piles[pile]]


def drop_on(protocol, card_id, pile, session, deck_id="deck"):
    """Takes a card from the table and releases it over a pile."""
    x, y = session.get_component(deck_id).pile_position(pile)
    call(acquire, protocol, card_id)
    call(release, protocol, card_id, x=x, y=y)


class TestCard:
    def test_a_card_is_a_token_of_its_own_kind(self):
        card = Card("c1", 0, 0, "/front.png", "/back.png", 30, 50)
        assert card.return_json()["kind"] == "card"
        assert card.flippable()
        # a card has no starting square: it never shows the green rectangle
        assert card.return_json()["in_place"] is False

    def test_a_card_declared_in_movable_lies_on_the_table(self):
        session = build_session()
        assert movable_ids(session) == ["joker", "army"]
        assert isinstance(session.get_component("joker"), Card)


class TestBuilding:
    def test_the_cards_wait_in_the_draw_pile_face_down(self):
        session = build_session()
        deck = session.get_component("deck")
        assert pile_ids(session) == ["ace", "king", "queen"]
        assert pile_ids(session, "discard") == []
        assert all(card.side == "back" and card.x is None for card in deck.piles["draw"])
        # they take the size and the back of their deck
        ace = session.get_component("ace")
        assert (ace.width, ace.height, ace.image_src["back"]) == (60, 90, "/back.png")

    def test_a_single_deck_has_one_pile(self):
        session = build_session(dict(DECK, type="single", discard=[card_json("two")]))
        deck = session.get_component("deck")
        assert list(deck.piles) == ["draw"]
        assert pile_ids(session) == ["ace", "king", "queen", "two"]

    def test_an_unknown_type_is_a_single_deck(self):
        assert Deck("d", 0, 0, 60, 90, "triple").type == "single"

    def test_the_discard_pile_lies_next_to_the_draw_pile(self):
        deck = build_session().get_component("deck")
        assert deck.pile_position("discard") == (100 + 60 + DECK_GAP, 100)
        placed = build_session(dict(DECK, discard_x=400, discard_y=300)).get_component("deck")
        assert placed.pile_position("discard") == (400, 300)

    def test_only_cards_go_in_and_copies_are_expanded(self):
        session = build_session(dict(DECK, components=[dict(ACE, copies=2), dict(ARMY, id="intruder")]))
        assert pile_ids(session) == ["ace-01", "ace-02"]

    def test_a_deck_outside_fixed_is_ignored(self):
        session = build_session(dict(DECK, id="other", components=[]), movable=[DECK])
        assert session.get_component("deck") is False

    def test_the_ids_of_the_cards_share_the_namespace(self):
        game_json = {"fixed": [DECK], "movable": [dict(JOKER, id="ace")]}
        assert duplicate_component_ids(game_json) == ["ace"]

    def test_anonymous_ids_rename_the_cards(self):
        session = build_session(options={"anonymous_id": True, "private_hand": True})
        assert session.get_component("ace") is False
        assert len(pile_ids(session)) == 3
        assert not {"ace", "king", "queen", "joker"} & set(session.components_dict)


class TestWhatTheClientsRead:
    def test_the_deck_is_sent_without_its_cards(self):
        session = build_session()
        described = session.get_component("deck").return_json()
        assert "components" not in described and "discard" not in described
        assert described["piles"]["draw"] == {
            "x": 100, "y": 100, "count": 3,
            "top": {"src": "/back.png", "width": 60, "height": 90},
        }
        assert described["piles"]["discard"]["top"] is None
        # no front of a card of the deck is anywhere in the session description
        assert ".png" in json.dumps(session.return_session_json(FakeUser("alice")))
        for name in ("ace", "king", "queen"):
            assert f"/{name}.png" not in json.dumps(session.return_session_json(FakeUser("alice")))

    def test_the_discard_pile_shows_the_front_of_its_top_card(self):
        session = build_session()
        protocol, *_ = two_players(session)
        drop_on(protocol, "joker", "discard", session)
        top = session.get_component("deck").return_json()["piles"]["discard"]["top"]
        assert top["src"] == "/joker.png"


class TestDraw:
    def test_the_top_card_comes_to_the_hand_of_the_player(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(draw, protocol, "deck", pile="draw", x=300, y=300, request_id="r1")
        queen = session.get_component("queen")
        assert pile_ids(session) == ["ace", "king"]
        assert movable_ids(session)[-1] == "queen"
        assert queen.acquired_by is alice
        assert (queen.x, queen.y) == (270, 255)
        event = bob.sent[-1]
        assert event["event"] == "draw" and event["request_id"] == "r1"
        assert event["deck_json"]["piles"]["draw"]["count"] == 2

    def test_nobody_learns_the_front_of_the_card_drawn(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(draw, protocol, "deck")
        for user in (alice, bob):
            described = user.sent[-1]["component_json"]
            assert described["side"] == "back"
            assert described["front_src"] is None
            assert described["back_src"] == "/back.png"

    def test_flipping_the_card_tells_everybody(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(draw, protocol, "deck")
        call(flip, protocol, "queen")
        assert bob.sent[-1]["front_src"] == "/queen.png"

    def test_an_empty_pile_gives_nothing(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", pile="discard")
        assert error_codes(protocol) == ["pile_empty"]

    def test_a_pile_the_deck_does_not_have(self):
        session = build_session(dict(DECK, type="single"))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", pile="discard")
        call(draw, protocol, "deck", pile=["draw"])
        assert error_codes(protocol) == ["unknown_pile", "unknown_pile"]

    def test_only_a_deck_is_drawn_from(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(draw, protocol, "army")
        assert error_codes(protocol) == ["component_not_a_deck"]

    def test_a_card_inside_a_deck_is_out_of_reach(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(acquire, protocol, "ace")
        call(flip, protocol, "ace")
        assert error_codes(protocol) == ["component_in_deck", "component_in_deck"]

    def test_a_game_not_started_refuses(self):
        session = build_session()
        session.started = False
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck")
        call(shuffle, protocol, "deck")
        call(refill, protocol, "deck")
        assert error_codes(protocol) == ["session_not_started"] * 3

    def test_a_watcher_does_not_touch_a_deck(self):
        session = build_session()
        watcher = FakeUser("carol", role="watcher", session=session)
        protocol = FakeProtocol(watcher)
        call(draw, protocol, "deck")
        assert error_codes(protocol) == ["watcher_not_allowed"]


class TestRelease:
    def test_a_card_released_over_the_discard_pile_joins_it_face_up(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        drop_on(protocol, "joker", "discard", session)
        joker = session.get_component("joker")
        assert pile_ids(session, "discard") == ["joker"]
        assert movable_ids(session) == ["army"]
        assert (joker.x, joker.side) == (None, "front")
        event = bob.sent[-1]
        assert (event["deck_id"], event["pile"]) == ("deck", "discard")
        assert event["deck_json"]["piles"]["discard"]["count"] == 1

    def test_a_card_released_over_the_draw_pile_goes_on_top_face_down(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(acquire, protocol, "joker")
        call(flip, protocol, "joker")
        drop_on(protocol, "joker", "draw", session)
        assert pile_ids(session) == ["ace", "king", "queen", "joker"]
        assert session.get_component("joker").side == "back"

    def test_a_token_does_not_go_into_a_deck(self):
        session = build_session()
        protocol, _, _, bob = two_players(session)
        drop_on(protocol, "army", "draw", session)
        assert pile_ids(session) == ["ace", "king", "queen"]
        assert "army" in movable_ids(session)
        assert bob.sent[-1]["deck_id"] is None

    def test_a_card_released_elsewhere_stays_on_the_table(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(acquire, protocol, "joker")
        call(release, protocol, "joker", x=900, y=900)
        assert (session.get_component("joker").x, session.get_component("joker").y) == (900, 900)


class TestShuffleAndRefill:
    def test_shuffle_keeps_every_card_and_tells_everybody(self):
        session = build_session()
        protocol, _, _, bob = two_players(session)
        call(shuffle, protocol, "deck", pile="draw")
        assert sorted(pile_ids(session)) == ["ace", "king", "queen"]
        assert bob.sent[-1]["event"] == "shuffle" and bob.sent[-1]["pile"] == "draw"

    def test_shuffle_changes_the_order(self):
        cards = [card_json(f"card-{number:02d}") for number in range(40)]
        session = build_session(dict(DECK, components=cards))
        protocol, *_ = two_players(session)
        before = pile_ids(session)
        call(shuffle, protocol, "deck")
        assert pile_ids(session) != before

    def test_refill_puts_the_discard_pile_under_the_draw_pile(self):
        session = build_session(dict(DECK, discard=[card_json("two"), card_json("three")]))
        protocol, _, _, bob = two_players(session)
        assert session.get_component("two").side == "front"
        call(refill, protocol, "deck")
        assert pile_ids(session) == ["two", "three", "ace", "king", "queen"]
        assert pile_ids(session, "discard") == []
        assert session.get_component("two").side == "back"
        piles = bob.sent[-1]["deck_json"]["piles"]
        assert bob.sent[-1]["event"] == "refill"
        assert (piles["draw"]["count"], piles["discard"]["count"]) == (5, 0)

    def test_a_single_deck_has_nothing_to_refill(self):
        session = build_session(dict(DECK, type="single"))
        protocol, *_ = two_players(session)
        call(refill, protocol, "deck")
        assert error_codes(protocol) == ["unknown_pile"]


class TestPrivateHand:
    def keep_top_card(self, session, protocol):
        call(draw, protocol, "deck")
        call(release, protocol, "queen", x=0, y=0, hand=True)

    def test_only_the_holder_reads_the_front_of_a_card_kept_in_hand(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        assert alice.sent[-1]["component_json"]["front_src"] == "/queen.png"
        assert bob.sent[-1]["component_json"]["front_src"] is None
        assert bob.sent[-1]["hands"] == {"alice": [{"src": "/back.png", "width": 60, "height": 90}]}
        assert session.return_session_json(alice)["hand"][0]["front_src"] == "/queen.png"
        assert "/queen.png" not in json.dumps(session.return_session_json(bob))

    def test_a_card_played_face_down_stays_unknown_to_the_others(self):
        session = build_session()
        protocol, _, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        call(take, protocol, "queen", x=300, y=300)
        assert bob.sent[-1]["event"] == "take"
        assert bob.sent[-1]["component_json"]["front_src"] is None


class TestSavedState:
    def test_the_state_keeps_the_cards_of_each_pile(self):
        session = build_session()
        protocol, *_ = two_players(session)
        drop_on(protocol, "joker", "discard", session)
        saved = session.game_json_state()["fixed"][0]
        assert "piles" not in saved
        assert [card["id"] for card in saved["components"]] == ["ace", "king", "queen"]
        assert [card["id"] for card in saved["discard"]] == ["joker"]
        assert saved["components"][0]["front_src"] == "/ace.png"

    def test_a_resumed_session_finds_its_piles_and_its_hands_again(self):
        session = build_session()
        protocol, *_ = two_players(session)
        drop_on(protocol, "joker", "discard", session)
        call(draw, protocol, "deck")
        call(release, protocol, "queen", x=0, y=0, hand=True)
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert pile_ids(resumed) == ["ace", "king"]
        assert pile_ids(resumed, "discard") == ["joker"]
        assert resumed.get_component("joker").side == "front"
        assert [card.id for card in resumed.hands["alice"]] == ["queen"]
        assert isinstance(resumed.get_component("queen"), Card)

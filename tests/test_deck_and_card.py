"""
Tests of the deck and of its cards. A deck is a fixed component holding cards
only, in one pile ("single") or in a draw pile and a discard pile ("duo"). A
card is a token whose front stays on the server while it lies on its back.

The actions are exercised through the message handlers with the fakes of
test_bag.py, so the deck is covered even where autobahn is missing.
"""
import json

from Components.card import Card
from Components.deck import DECK_GAP, DEFAULT_PICKUP_QUANTITIES, Deck
from handle_message import acquire, draw, flip, refill, release, rotate, shuffle, take
from session import DRAW_FAN, DRAW_MAX, Session, duplicate_component_ids
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
        "options": {"private_hand": {}},
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
        session = build_session(options={"anonymous_id": True, "private_hand": {}})
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

    def test_a_card_joins_the_hand_face_up(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        assert session.get_component("queen").side == "front"
        assert alice.sent[-1]["component_json"]["side"] == "front"
        assert session.return_session_json(alice)["hand"][0]["side"] == "front"

    def test_a_card_is_played_face_up_without_being_turned_over(self):
        session = build_session()
        protocol, _, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        call(take, protocol, "queen", x=300, y=300)
        assert bob.sent[-1]["event"] == "take"
        assert bob.sent[-1]["component_json"]["side"] == "front"
        assert bob.sent[-1]["component_json"]["front_src"] == "/queen.png"

    def test_the_holder_turns_a_card_over_in_its_hand(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        told = len(bob.sent)
        call(flip, protocol, "queen")
        assert session.get_component("queen").side == "back"
        # the holder still reads both faces, and nobody else is told
        assert alice.sent[-1] == {"event": "flip", "component_id": "queen", "side": "back",
                                  "front_src": "/queen.png", "back_src": "/back.png"}
        assert len(bob.sent) == told
        call(flip, protocol, "queen")
        assert session.get_component("queen").side == "front"

    def test_a_card_turned_over_in_the_hand_is_played_face_down(self):
        session = build_session()
        protocol, _, _, bob = two_players(session)
        self.keep_top_card(session, protocol)
        call(flip, protocol, "queen")
        call(take, protocol, "queen", x=300, y=300)
        assert bob.sent[-1]["event"] == "take"
        assert bob.sent[-1]["component_json"]["side"] == "back"
        assert bob.sent[-1]["component_json"]["front_src"] is None

    def test_another_player_does_not_turn_over_a_card_of_a_hand(self):
        session = build_session()
        protocol, _, other, _ = two_players(session)
        self.keep_top_card(session, protocol)
        call(flip, other, "queen")
        assert error_codes(other) == ["component_in_hand"]
        assert session.get_component("queen").side == "front"

    def test_a_resumed_hand_keeps_the_side_of_its_cards(self):
        session = build_session()
        protocol, *_ = two_players(session)
        self.keep_top_card(session, protocol)
        call(flip, protocol, "queen")
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=session.game_json_state(), started=True,
        )
        assert resumed.hands["alice"][0].side == "back"


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


class TestDrawingSeveralCards:
    def test_a_count_takes_that_many_cards_from_the_top(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(draw, protocol, "deck", count=2, x=300, y=300, request_id="r1")
        assert pile_ids(session) == ["ace"]
        events = [event for event in bob.sent if event["event"] == "draw"]
        assert [event["component_json"]["id"] for event in events] == ["queen", "king"]
        assert [(event["index"], event["count"], event["request_id"]) for event in events] == [
            (0, 2, "r1"), (1, 2, "r1"),
        ]
        assert all(event["deck_json"]["piles"]["draw"]["count"] == 1 for event in events)
        assert movable_ids(session)[-2:] == ["queen", "king"]
        assert all(session.get_component(name).acquired_by is alice for name in ("queen", "king"))

    def test_the_cards_come_out_side_by_side(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=2, x=300, y=300)
        queen, king = session.get_component("queen"), session.get_component("king")
        assert (queen.x, queen.y) == (270, 255)
        assert (king.x, king.y) == (270 + DRAW_FAN, 255)

    def test_without_a_point_they_fan_from_the_pile(self):
        session = build_session()
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=2)
        queen, king = session.get_component("queen"), session.get_component("king")
        assert (queen.x, queen.y) == (100, 100)
        assert (king.x, king.y) == (100 + DRAW_FAN, 100)

    def test_a_short_pile_gives_what_it_holds(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(draw, protocol, "deck", count=5)
        assert pile_ids(session) == []
        assert [event["count"] for event in alice.sent if event["event"] == "draw"] == [3, 3, 3]
        assert error_codes(protocol) == []

    def test_no_more_than_the_maximum_comes_out(self):
        cards = [card_json(f"card-{index}") for index in range(DRAW_MAX + 5)]
        session = build_session(dict(DECK, components=cards))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=500)
        assert len(pile_ids(session)) == 5

    def test_a_larger_pickup_button_raises_the_maximum(self):
        cards = [card_json(f"card-{index}") for index in range(20)]
        session = build_session(dict(DECK, components=cards, card_pickup_quantity=[3, 15]))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=500)
        assert len(pile_ids(session)) == 5

    def test_smaller_pickup_buttons_keep_the_maximum(self):
        cards = [card_json(f"card-{index}") for index in range(DRAW_MAX + 5)]
        session = build_session(dict(DECK, components=cards, card_pickup_quantity=False))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=500)
        assert len(pile_ids(session)) == 5

    def test_a_count_that_is_no_number_draws_one_card(self):
        for count in (0, -3, "5", 2.5, True, None):
            session = build_session()
            protocol, *_ = two_players(session)
            call(draw, protocol, "deck", count=count)
            assert pile_ids(session) == ["ace", "king"]

    def test_an_empty_pile_still_answers_pile_empty(self):
        session = build_session(dict(DECK, components=[]))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck", count=5)
        assert error_codes(protocol) == ["pile_empty"]

    def test_a_single_draw_carries_its_index_too(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(draw, protocol, "deck")
        assert (alice.sent[-1]["index"], alice.sent[-1]["count"]) == (0, 1)


class TestPickupQuantities:
    """card_pickup_quantity: the multi-draw buttons the client shows on a pile."""

    def quantities(self, **fields):
        return build_session(dict(DECK, **fields)).get_component("deck").return_json()["card_pickup_quantity"]

    def test_absent_gives_the_default_buttons(self):
        assert self.quantities() == list(DEFAULT_PICKUP_QUANTITIES) == [2, 5, 10]

    def test_false_gives_no_button(self):
        assert self.quantities(card_pickup_quantity=False) == []

    def test_a_list_gives_its_buttons_in_order(self):
        assert self.quantities(card_pickup_quantity=[4, 1, 7]) == [4, 1, 7]

    def test_only_whole_numbers_above_zero_are_kept_five_at_most(self):
        value = [3, "4", 0, -1, True, 2.5, None, 3, 7, 8, 9, 11, 12]
        assert self.quantities(card_pickup_quantity=value) == [3, 7, 8, 9, 11]

    def test_anything_else_gives_the_default_buttons(self):
        for value in (True, "5", 5, None, {"a": 1}):
            assert self.quantities(card_pickup_quantity=value) == [2, 5, 10]

    def test_the_saved_state_keeps_the_buttons(self):
        for value, expected in ((False, []), ([3, 6], [3, 6]), (None, [2, 5, 10])):
            session = build_session(dict(DECK, card_pickup_quantity=value))
            state = json.loads(json.dumps(session.game_json_state()))
            resumed = Session(
                user=None, name="JudgeDredd", key="KEY1", code="CODE1",
                active=True, variant="std", game_json=state, started=True,
            )
            assert resumed.get_component("deck").card_pickup_quantity == expected


class TestPickupPiles:
    """card_pickup_piles: the piles that show the multi-draw buttons."""

    def piles(self, deck=DECK, **fields):
        return build_session(dict(deck, **fields)).get_component("deck").return_json()["card_pickup_piles"]

    def test_absent_gives_every_pile(self):
        assert self.piles() == ["draw", "discard"]

    def test_a_list_keeps_the_piles_it_names_in_the_order_of_the_deck(self):
        assert self.piles(card_pickup_piles=["discard"]) == ["discard"]
        assert self.piles(card_pickup_piles=["discard", "draw"]) == ["draw", "discard"]
        assert self.piles(card_pickup_piles=[]) == []

    def test_unknown_piles_are_ignored(self):
        assert self.piles(card_pickup_piles=["discard", "graveyard", 3]) == ["discard"]
        # a single deck has no discard pile
        assert self.piles(dict(DECK, type="single"), card_pickup_piles=["discard"]) == []

    def test_anything_else_gives_every_pile(self):
        for value in ("discard", True, None, {"discard": 1}):
            assert self.piles(card_pickup_piles=value) == ["draw", "discard"]

    def test_the_saved_state_keeps_the_piles(self):
        session = build_session(dict(DECK, card_pickup_piles=["discard"]))
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert resumed.get_component("deck").card_pickup_piles == ["discard"]


class TestOrientation:
    """orientation: the piles of a deck or a stack are turned around their center."""

    def deck(self, **fields):
        return build_session(dict(DECK, **fields)).get_component("deck")

    def test_absent_or_wrong_is_upright(self):
        for value in (None, "90", True, 45, 100.5):
            assert self.deck(orientation=value).return_json()["orientation"] == 0
        assert self.deck().orientation == 0

    def test_a_quarter_turn_is_kept_modulo_360(self):
        assert [self.deck(orientation=value).orientation for value in (90, 180, 270, -90, 450)] == [
            90, 180, 270, 270, 90,
        ]

    def test_a_quarter_turn_swaps_the_footprint_around_the_center(self):
        # DECK: x 100, y 100, 60 x 90, center (130, 145)
        deck = self.deck(orientation=270)
        assert deck.footprint("draw") == (85, 115, 90, 60)
        assert deck.pile_at(86, 120) == "draw"
        assert deck.pile_at(105, 105) is None
        assert self.deck(orientation=180).footprint("draw") == (100, 100, 60, 90)

    def test_a_card_drawn_is_upright(self):
        session = build_session(dict(DECK, orientation=90))
        protocol, alice, *_ = two_players(session)
        call(draw, protocol, "deck", x=300, y=300)
        assert session.get_component("queen").orientation == 0
        assert alice.sent[-1]["component_json"]["orientation"] == 0
        call(draw, protocol, "deck", x=300, y=300, count=2)
        assert [session.get_component(name).orientation for name in ("king", "ace")] == [0, 0]

    def test_the_saved_state_keeps_it(self):
        session = build_session(dict(DECK, orientation=270))
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert resumed.get_component("deck").orientation == 270


class TestCardRotation:
    """A card turns by steps of 45 degrees only when the game declares it
    orientable; a hand sets it upright, a deck turns it like itself."""

    def orientable_session(self, **fields):
        return build_session(dict(DECK, **fields), movable=[dict(JOKER, orientable=True), ARMY])

    def test_an_orientable_card_on_the_table_turns_for_everybody(self):
        session = self.orientable_session()
        protocol, alice, _, bob = two_players(session)
        call(rotate, protocol, "joker", direction="right")
        assert session.get_component("joker").orientation == 45
        for user in (alice, bob):
            assert user.sent[-1] == {"event": "rotate", "component_id": "joker", "orientation": 45}
        call(rotate, protocol, "joker", direction="left")
        call(rotate, protocol, "joker", direction="left")
        assert session.get_component("joker").orientation == 315

    def test_a_card_absent_or_false_does_not_turn(self):
        for movable in ([JOKER], [dict(JOKER, orientable=False)]):
            session = build_session(movable=movable)
            protocol, *_ = two_players(session)
            assert session.get_component("joker").return_json()["orientable"] is False
            call(rotate, protocol, "joker", direction="right")
            assert error_codes(protocol) == ["component_not_rotatable"]
            assert session.get_component("joker").orientation == 0

    def test_a_card_inside_a_deck_does_not_turn(self):
        session = build_session(dict(DECK, components=[dict(QUEEN, orientable=True)]))
        protocol, *_ = two_players(session)
        call(rotate, protocol, "queen", direction="right")
        assert error_codes(protocol) == ["component_in_deck"]
        assert session.get_component("queen").orientation == 0

    def test_a_held_card_does_not_turn(self):
        session = self.orientable_session()
        protocol, _, other, _ = two_players(session)
        call(acquire, other, "joker")
        call(rotate, protocol, "joker", direction="right")
        assert error_codes(protocol) == ["component_held"]

    def test_a_card_kept_in_hand_stands_upright(self):
        session = self.orientable_session()
        protocol, alice, *_ = two_players(session)
        call(rotate, protocol, "joker", direction="right")
        call(acquire, protocol, "joker")
        call(release, protocol, "joker", x=0, y=0, hand=True)
        assert session.get_component("joker").orientation == 0
        assert alice.sent[-1]["component_json"]["orientation"] == 0
        call(take, protocol, "joker", x=300, y=300)
        assert session.get_component("joker").orientation == 0

    def test_a_card_drawn_from_a_turned_deck_is_upright_in_hand(self):
        session = build_session(dict(DECK, orientation=90))
        protocol, *_ = two_players(session)
        call(draw, protocol, "deck")
        call(release, protocol, "queen", x=0, y=0, hand=True)
        assert session.get_component("queen").orientation == 0

    def test_a_card_put_on_a_deck_takes_its_orientation(self):
        session = self.orientable_session(orientation=270)
        protocol, *_ = two_players(session)
        call(rotate, protocol, "joker", direction="right")
        drop_on(protocol, "joker", "draw", session)
        assert pile_ids(session)[-1] == "joker"
        assert session.get_component("joker").orientation == 270
        # taken back, it stands upright again
        call(draw, protocol, "deck", x=300, y=300)
        assert session.get_component("joker").orientation == 0

    def test_a_card_put_on_an_upright_deck_stands_upright(self):
        session = self.orientable_session()
        protocol, *_ = two_players(session)
        call(rotate, protocol, "joker", direction="right")
        drop_on(protocol, "joker", "discard", session)
        assert session.get_component("joker").orientation == 0

    def test_the_saved_state_keeps_the_angle_and_the_choice(self):
        session = self.orientable_session()
        protocol, *_ = two_players(session)
        call(rotate, protocol, "joker", direction="right")
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert resumed.get_component("joker").orientation == 45
        assert resumed.get_component("joker").orientable is True


class TestText:
    """text: the caption above a deck; absent, null, empty or not a text, none."""

    def test_no_caption_without_a_text(self):
        for fields in ({}, {"text": None}, {"text": ""}, {"text": 12}):
            assert build_session(dict(DECK, **fields)).get_component("deck").return_json()["text"] is None

    def test_a_text_is_carried_as_is(self):
        assert build_session(dict(DECK, text="Pioche")).get_component("deck").return_json()["text"] == "Pioche"


class TestDisplayShuffle:
    """display_shuffle: only false hides the Shuffle buttons of a deck."""

    def test_absent_or_true_shows_them(self):
        for fields in ({}, {"display_shuffle": True}, {"display_shuffle": None}, {"display_shuffle": 0}):
            assert build_session(dict(DECK, **fields)).get_component("deck").return_json()["display_shuffle"] is True

    def test_false_hides_them_and_refuses_the_shuffle(self):
        session = build_session(dict(DECK, display_shuffle=False))
        assert session.get_component("deck").return_json()["display_shuffle"] is False
        protocol, *_ = two_players(session)
        call(shuffle, protocol, "deck", pile="draw")
        assert error_codes(protocol) == ["shuffle_disabled"]
        assert pile_ids(session) == ["ace", "king", "queen"]

    def test_the_shuffle_at_startup_still_happens(self):
        orders = {
            tuple(pile_ids(build_session(dict(DECK, display_shuffle=False, shuffle_at_startup=True,
                                              components=[card_json(f"c{index}") for index in range(8)]))))
            for _ in range(30)
        }
        assert len(orders) > 1

    def test_the_saved_state_keeps_it(self):
        state = build_session(dict(DECK, display_shuffle=False)).game_json_state()
        assert state["fixed"][0]["display_shuffle"] is False

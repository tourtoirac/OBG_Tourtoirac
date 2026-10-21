"""
Tests of the slot: a pile of cards that only centers the cards put down on
it. The cards keep their side, and the pile is never shuffled nor dealt.

The actions are exercised through the message handlers with the fakes of
test_bag.py, so the slot is covered even where autobahn is missing.
"""
from Components.slot import Slot
from handle_message import acquire, deal, draw, flip, release, shuffle
from session import Session, duplicate_component_ids
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes, movable_ids


def card_json(name, **fields):
    return {"kind": "card", "id": name, "front_src": f"/{name}.png", "back_src": "/back.png",
            "width": 60, "height": 90, **fields}


ACE, KING = card_json("ace"), card_json("king", side="back")
SLOT = {"kind": "slot", "id": "slot", "x": 100, "y": 100, "width": 200, "height": 300,
        "components": [ACE, KING]}
JOKER = card_json("joker", x=500, y=500, orientable=True, orientation=45)
ARMY = {"kind": "token", "id": "army", "x": 700, "y": 700, "width": 40, "height": 40,
        "front_src": "/army.png", "back_src": None}


def build_session(slot=None, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [SLOT if slot is None else slot],
        "movable": [JOKER, ARMY],
        "options": {"private_hand": {}},
        **extra,
    }
    return Session(
        user=None, name="WarOfTheRing", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def two_players(session):
    alice = FakeUser("alice", session=session)
    bob = FakeUser("bob", session=session)
    session.players = [alice, bob]
    return FakeProtocol(alice), alice


def slot_cards(session):
    return [(card.id, card.side) for card in session.get_component("slot").piles["draw"]]


class TestBuilding:
    def test_a_slot_is_a_pile_keeping_the_sides_of_its_cards(self):
        session = build_session()
        slot = session.get_component("slot")
        assert isinstance(slot, Slot)
        assert list(slot.piles) == ["draw"]
        assert slot_cards(session) == [("ace", "front"), ("king", "back")]

    def test_the_clients_read_a_slot_without_buttons(self):
        described = build_session(dict(SLOT, card_pickup_quantity=[2, 3], display_shuffle=True)).get_component(
            "slot").return_json()
        assert described["kind"] == "slot"
        assert described["card_pickup_quantity"] == []
        assert described["display_shuffle"] is False
        assert "deal" not in described
        assert "components" not in described

    def test_the_top_card_shows_the_side_it_lies_on(self):
        session = build_session(dict(SLOT, components=[KING, ACE]))
        assert session.get_component("slot").return_json()["piles"]["draw"]["top"]["src"] == "/ace.png"
        session = build_session()
        assert session.get_component("slot").return_json()["piles"]["draw"]["top"]["src"] == "/back.png"

    def test_a_slot_is_never_shuffled_at_startup(self, monkeypatch):
        monkeypatch.setattr("random.shuffle", lambda cards: cards.reverse())
        session = build_session(dict(SLOT, shuffle_at_startup=True))
        assert slot_cards(session) == [("ace", "front"), ("king", "back")]

    def test_the_saved_state_builds_the_same_slot(self):
        state = build_session().game_json_state()
        assert state["fixed"][0]["kind"] == "slot"
        resumed = Session(
            user=None, name="WarOfTheRing", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert isinstance(resumed.get_component("slot"), Slot)
        assert slot_cards(resumed) == [("ace", "front"), ("king", "back")]

    def test_a_card_id_declared_twice_is_reported(self):
        game_json = {"fixed": [SLOT], "movable": [dict(JOKER, id="ace")]}
        assert duplicate_component_ids(game_json) == ["ace"]


class TestCards:
    def test_a_released_card_keeps_its_side(self):
        session = build_session()
        protocol, alice = two_players(session)
        call(acquire, protocol, "joker")
        # the center of the card lies inside the slot, not its corner
        call(release, protocol, "joker", x=150, y=150)
        assert slot_cards(session)[-1] == ("joker", "front")
        assert "joker" not in movable_ids(session)
        assert alice.sent[-1]["deck_id"] == "slot"
        assert alice.sent[-1]["deck_json"]["piles"]["draw"]["top"]["src"] == "/joker.png"

    def test_a_card_released_face_down_stays_face_down(self):
        session = build_session()
        protocol, _ = two_players(session)
        call(flip, protocol, "joker")
        call(acquire, protocol, "joker")
        call(release, protocol, "joker", x=150, y=150)
        assert slot_cards(session)[-1] == ("joker", "back")

    def test_a_token_does_not_enter_a_slot(self):
        session = build_session()
        protocol, _ = two_players(session)
        call(acquire, protocol, "army")
        call(release, protocol, "army", x=150, y=150)
        assert "army" in movable_ids(session)

    def test_a_drawn_card_comes_out_upright_on_its_side(self):
        session = build_session(dict(SLOT, orientation=90))
        protocol, alice = two_players(session)
        call(acquire, protocol, "joker")
        call(release, protocol, "joker", x=150, y=150)
        assert session.get_component("joker").orientation == 90
        call(draw, protocol, "slot", x=10, y=10)
        joker = session.get_component("joker")
        assert (joker.side, joker.orientation) == ("front", 0)
        assert alice.sent[-1]["component_json"]["id"] == "joker"

    def test_a_slot_is_not_shuffled(self):
        session = build_session()
        protocol, _ = two_players(session)
        call(shuffle, protocol, "slot")
        assert error_codes(protocol) == ["shuffle_disabled"]
        assert slot_cards(session) == [("ace", "front"), ("king", "back")]

    def test_a_slot_does_not_deal(self):
        session = build_session()
        protocol, _ = two_players(session)
        call(deal, protocol, "slot")
        assert error_codes(protocol) == ["component_not_a_stack"]


class TestRandomizerTarget:
    DECK = {"kind": "deck", "id": "deck", "x": 600, "y": 100, "width": 60, "height": 90,
            "components": [card_json("ring", side="front")]}

    def session(self, targets):
        setup = [{"kind": "randomizer", "source_kind": "id_list", "source_id": ["joker", "army"],
                  "targets": targets}]
        return build_session(dict(SLOT, components=[]), setup=setup)

    def test_a_slot_takes_a_card_like_a_release(self):
        session = self.session([{"container_id": "slot"}])
        assert [(entry["component_id"], entry["container_id"]) for entry in session.setup] == [("joker", "slot")]
        components = session.apply_setup()
        # the card keeps its side, and lies turned like the slot
        assert slot_cards(session) == [("joker", "front")]
        assert "joker" not in movable_ids(session)
        by_id = {state["id"]: state for state in components}
        assert by_id["slot"]["piles"]["draw"]["count"] == 1

    def test_applying_twice_changes_nothing(self):
        session = self.session([{"container_id": "slot"}])
        session.apply_setup()
        session.apply_setup()
        assert slot_cards(session) == [("joker", "front")]

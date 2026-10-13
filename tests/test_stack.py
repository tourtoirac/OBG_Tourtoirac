"""
Tests of the stack: a deck of one single pile that deals its cards to the
private hands of the players.

The actions are exercised through the message handlers with the fakes of
test_bag.py, so the stack is covered even where autobahn is missing.
"""
from Components.stack import Stack
from handle_message import acquire, deal, draw, refill, release, shuffle
from session import Session, duplicate_component_ids
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes, movable_ids


def card_json(name, **fields):
    return {"kind": "card", "id": name, "front_src": f"/{name}.png", "width": 60, "height": 90, **fields}


ACE, KING, QUEEN = card_json("ace"), card_json("king"), card_json("queen")
STACK = {"kind": "stack", "id": "stack", "x": 100, "y": 100, "width": 200, "height": 300,
         "back_src": "/back.png", "components": [ACE, KING, QUEEN]}
JOKER = card_json("joker", x=500, y=500, back_src="/back.png")
ARMY = {"kind": "token", "id": "army", "x": 700, "y": 700, "width": 40, "height": 40,
        "front_src": "/army.png", "back_src": None}


def build_session(stack=None, private_hand=(), **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [STACK if stack is None else stack],
        "movable": [JOKER, ARMY],
        "options": {"private_hand": {} if private_hand == () else private_hand},
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


def stack_ids(session):
    return [card.id for card in session.get_component("stack").piles["draw"]]


def hand_ids(session, nickname):
    return [card.id for card in session.hands.get(nickname, [])]


class TestBuilding:
    def test_a_stack_is_a_deck_of_one_pile(self):
        session = build_session(dict(STACK, type="duo", discard=[card_json("ten")]))
        stack = session.get_component("stack")
        assert isinstance(stack, Stack)
        assert list(stack.piles) == ["draw"]
        # a stack has no discard pile: every card it is given is in its pile
        assert stack_ids(session) == ["ace", "king", "queen", "ten"]
        assert all(card.side == "back" for card in stack.piles["draw"])

    def test_the_clients_read_the_pile_without_its_cards(self):
        described = build_session().get_component("stack").return_json()
        assert described["kind"] == "stack"
        assert "components" not in described
        assert described["piles"]["draw"]["count"] == 3
        # the size of the top card lets the client center it in the stack
        assert described["piles"]["draw"]["top"] == {"src": "/back.png", "width": 60, "height": 90}

    def test_a_stack_is_ignored_outside_fixed(self):
        session = build_session(movable=[STACK])
        assert movable_ids(session) == []

    def test_the_saved_state_builds_the_same_stack(self):
        session = build_session()
        state = session.game_json_state()
        assert state["fixed"][0]["kind"] == "stack"
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert isinstance(resumed.get_component("stack"), Stack)
        assert stack_ids(resumed) == ["ace", "king", "queen"]

    def test_a_card_id_declared_twice_is_reported(self):
        game_json = {"fixed": [STACK], "movable": [dict(JOKER, id="ace")]}
        assert duplicate_component_ids(game_json) == ["ace"]


class TestCards:
    def test_a_released_card_joins_the_top_face_down(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(acquire, protocol, "joker")
        # the center of the card lies inside the stack, not its corner
        call(release, protocol, "joker", x=150, y=150)
        assert stack_ids(session) == ["ace", "king", "queen", "joker"]
        assert session.get_component("joker").side == "back"
        assert "joker" not in movable_ids(session)
        assert alice.sent[-1]["deck_id"] == "stack"

    def test_a_token_does_not_enter_a_stack(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        call(acquire, protocol, "army")
        call(release, protocol, "army", x=150, y=150)
        assert "army" in movable_ids(session)
        assert stack_ids(session) == ["ace", "king", "queen"]

    def test_the_top_card_is_drawn_by_a_click(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(draw, protocol, "stack", x=10, y=10)
        assert stack_ids(session) == ["ace", "king"]
        assert alice.sent[-1]["component_json"]["id"] == "queen"

    def test_shuffle_keeps_the_cards(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(shuffle, protocol, "stack")
        assert sorted(stack_ids(session)) == ["ace", "king", "queen"]
        assert alice.sent[-1]["event"] == "shuffle"

    def test_a_stack_has_nothing_to_refill(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        call(refill, protocol, "stack")
        assert error_codes(protocol) == ["unknown_pile"]


class TestDeal:
    def test_each_player_gets_one_card_from_the_top(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        call(deal, protocol, "stack")
        assert hand_ids(session, "alice") == ["queen"]
        assert hand_ids(session, "bob") == ["king"]
        assert stack_ids(session) == ["ace"]
        event = alice.sent[-1]
        assert event["event"] == "deal"
        assert event["nickname"] == "alice"
        assert event["deck_json"]["piles"]["draw"]["count"] == 1
        assert {name: len(held) for name, held in event["hands"].items()} == {"alice": 1, "bob": 1}
        assert bob.sent[-1]["event"] == "deal"

    def test_the_dealt_cards_lie_face_up_in_the_hands(self):
        session = build_session()
        protocol, alice, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert session.get_component("queen").side == "front"
        assert alice.sent[-1]["cards"][0]["side"] == "front"

    def test_a_player_only_reads_its_own_card(self):
        session = build_session()
        protocol, alice, _, bob = two_players(session)
        watcher = FakeUser("carol", role="watcher", session=session)
        session.watchers = [watcher]
        call(deal, protocol, "stack")
        assert [(card["id"], card["front_src"]) for card in alice.sent[-1]["cards"]] == [("queen", "/queen.png")]
        assert [(card["id"], card["front_src"]) for card in bob.sent[-1]["cards"]] == [("king", "/king.png")]
        assert watcher.sent[-1]["cards"] == []
        assert "king" not in str(alice.sent[-1])

    def test_the_seats_are_dealt_in_order(self):
        session = build_session()
        session.player_names = ["bob", "alice"]
        protocol, _, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert hand_ids(session, "bob") == ["queen"]
        assert hand_ids(session, "alice") == ["king"]

    def test_nothing_is_dealt_without_a_card_for_everybody(self):
        session = build_session(dict(STACK, components=[ACE]))
        protocol, alice, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert error_codes(protocol) == ["not_enough_cards"]
        assert stack_ids(session) == ["ace"]
        assert session.hands == {}
        assert alice.sent == []

    def test_no_deal_without_private_hands(self):
        session = build_session(private_hand=None)
        protocol, _, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert error_codes(protocol) == ["private_hand_disabled"]
        assert stack_ids(session) == ["ace", "king", "queen"]

    def test_a_deck_does_not_deal(self):
        session = build_session(dict(STACK, kind="deck"))
        protocol, _, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert error_codes(protocol) == ["component_not_a_stack"]

    def test_no_deal_while_a_player_is_away(self):
        session = build_session()
        session.player_names = ["alice", "bob", "carol"]
        protocol, _, _, _ = two_players(session)
        call(deal, protocol, "stack")
        assert error_codes(protocol) == ["players_missing"]

    def test_a_watcher_does_not_deal(self):
        session = build_session()
        two_players(session)
        watcher = FakeUser("carol", role="watcher", session=session)
        session.watchers = [watcher]
        protocol = FakeProtocol(watcher)
        call(deal, protocol, "stack")
        assert error_codes(protocol) == ["watcher_not_allowed"]

    def test_the_dealt_cards_are_saved_with_the_hands(self):
        session = build_session()
        protocol, _, _, _ = two_players(session)
        call(deal, protocol, "stack")
        state = session.game_json_state()
        assert [card["id"] for card in state["hands"]["alice"]] == ["queen"]
        assert [card["id"] for card in state["fixed"][0]["components"]] == ["ace"]


class TestShuffleAtStartup:
    """shuffle_at_startup shuffles the draw pile when the game is launched."""

    @staticmethod
    def reversing(monkeypatch):
        # a shuffle that can be told from no shuffle at all
        monkeypatch.setattr("Components.deck.random.shuffle", lambda cards: cards.reverse())

    def test_a_stack_asking_for_it_is_shuffled(self, monkeypatch):
        self.reversing(monkeypatch)
        session = build_session(dict(STACK, shuffle_at_startup=True))
        assert stack_ids(session) == ["queen", "king", "ace"]

    def test_a_deck_asking_for_it_is_shuffled(self, monkeypatch):
        self.reversing(monkeypatch)
        deck = dict(STACK, kind="deck", type="duo", shuffle_at_startup=True, discard=[card_json("ten")])
        session = build_session(deck)
        assert stack_ids(session) == ["queen", "king", "ace"]
        # the discard pile is left as the game declares it
        assert [card.id for card in session.get_component("stack").piles["discard"]] == ["ten"]

    def test_only_true_shuffles(self, monkeypatch):
        self.reversing(monkeypatch)
        for value in (False, None, "true", 1):
            session = build_session(dict(STACK, shuffle_at_startup=value))
            assert stack_ids(session) == ["ace", "king", "queen"]
        assert stack_ids(build_session()) == ["ace", "king", "queen"]

    def test_a_resumed_session_is_not_shuffled_again(self, monkeypatch):
        self.reversing(monkeypatch)
        session = build_session(dict(STACK, shuffle_at_startup=True))
        state = session.game_json_state()
        assert "shuffle_at_startup" not in state["fixed"][0]
        resumed = Session(
            user=None, name="JudgeDredd", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state, started=True,
        )
        assert stack_ids(resumed) == ["queen", "king", "ace"]

    def test_the_clients_do_not_read_the_key(self):
        session = build_session(dict(STACK, shuffle_at_startup=True))
        assert "shuffle_at_startup" not in session.get_component("stack").return_json()

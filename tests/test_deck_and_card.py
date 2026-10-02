"""
Tests for Deck and Card behaviour.

These two classes are still unreachable from the WebSocket layer (no `add`
action is exposed, and Session.load_session_components only builds boards,
tokens and dice), so they are latent bugs guarded against regressions. The flip
action does reach the tokens, which are covered by test_token_flip.py.
"""
import pytest

from Components.card import Card
from Components.component import Component
from Components.deck import Deck
from Components.token import Token
from user import User


@pytest.fixture
def card():
    return Card("c1", 0, 0, "front.png", "back.png", 30, 50, 0)


@pytest.fixture
def deck():
    return Deck("d1", 0, 0, "deck.png", 30, 50, "deck_icon.png")


class TestDeckAdd:
    """
    Bug 10: Deck.add called list.insert(component, position) instead of
            list.insert(position, component), so every call raised
            TypeError: 'str' object cannot be interpreted as an integer.
    """

    def test_add_appends_by_default(self, deck):
        deck.add("draw", "card_a")

        assert deck.containers["draw"] == ["card_a"]

    def test_add_accepts_a_component_object(self, deck):
        component = Component("c1")
        deck.add("draw", component)

        assert deck.containers["draw"] == [component]

    def test_add_at_an_explicit_position(self, deck):
        for name in ("card_a", "card_b", "card_c"):
            deck.add("draw", name)

        deck.add("draw", "card_x", 1)

        assert deck.containers["draw"] == ["card_a", "card_x", "card_b", "card_c"]

    def test_add_at_the_very_beginning(self, deck):
        deck.add("draw", "first")
        deck.add("draw", "zeroth", 0)

        assert deck.containers["draw"] == ["zeroth", "first"]

    @pytest.mark.parametrize("position", [-5, -1, 99, 1000])
    def test_out_of_range_position_appends(self, deck, position):
        """
        The old code fell back to position = -1, and list.insert(-1, x) inserts
        *before the last element* rather than appending.
        """
        deck.add("draw", "card_a")
        deck.add("draw", "card_b")

        deck.add("draw", "card_z", position)

        assert deck.containers["draw"] == ["card_a", "card_b", "card_z"]

    def test_out_of_range_on_an_empty_stack(self, deck):
        deck.add("draw", "only", 42)

        assert deck.containers["draw"] == ["only"]

    def test_add_into_the_discard_stack(self, deck):
        deck.add("discard", "card_a")

        assert deck.containers["discard"] == ["card_a"]
        assert deck.containers["draw"] == []

    def test_add_to_an_unknown_stack_is_ignored(self, deck):
        deck.add("nowhere", "card_a")  # must not raise

        assert deck.containers["draw"] == []
        assert deck.containers["discard"] == []


class TestDeckStackOperations:
    def test_pick_removes_the_component(self, deck):
        deck.add("draw", "only_card")

        assert deck.pick("draw") == "only_card"
        assert deck.containers["draw"] == []

    def test_pick_on_an_empty_stack_returns_none(self, deck):
        assert deck.pick("draw") is None

    def test_pick_from_an_unknown_stack_returns_none(self, deck):
        assert deck.pick("nowhere") is None

    def test_shuffle_keeps_every_card(self, deck):
        for index in range(10):
            deck.add("draw", f"card_{index}")

        deck.shuffle("draw")

        assert sorted(deck.containers["draw"]) == sorted(
            f"card_{index}" for index in range(10)
        )

    def test_return_json_counts_the_stacks(self, deck):
        deck.add("draw", "a")
        deck.add("draw", "b")
        deck.add("discard", "c")

        assert deck.return_json()["stacks_count"] == {"draw": 2, "discard": 1}

    def test_return_json_includes_components_on_demand(self, deck):
        deck.add("draw", "a")

        assert "components" not in deck.return_json()
        detailed = deck.return_json(["components"])
        assert detailed["components"] == {"draw": ["a"], "discard": []}


class TestCardFlip:
    """
    Bug 11: Card.flip updated self.src when turning to the back but not when
            turning back to the front, so the card kept showing the wrong image.
    """

    def test_starts_on_the_back(self, card):
        assert card.side == "back"
        assert card.src == "back.png"

    def test_flip_to_the_front_updates_src(self, card):
        card.flip()

        assert card.side == "front"
        assert card.src == "front.png", "src doit suivre le flip vers la face"

    def test_flip_back_updates_src(self, card):
        card.flip()
        card.flip()

        assert card.side == "back"
        assert card.src == "back.png"

    def test_repeated_flips_stay_consistent(self, card):
        for _ in range(6):
            card.flip()
            assert card.src == card.image_src[card.side], (
                "src doit toujours correspondre a la face courante"
            )

    def test_flip_is_visible_in_return_json(self, card):
        card.flip()
        assert card.return_json()["src"] == "front.png"
        assert card.return_json()["side"] == "front"

    def test_tap_changes_the_orientation(self, card):
        card.tap(90)

        assert card.orientation == 90
        assert card.return_json()["orientation"] == 90


class TestCardFlipMatchesToken:
    def test_both_components_expose_the_current_face(self):
        card = Card("c1", 0, 0, "front.png", "back.png", 3, 4, 0)
        token = Token("t1", 0, 0, "front.png", "back.png", 3, 4)

        card.flip()
        token.flip()

        assert card.src == card.image_src[card.side]
        assert token.src == token.image_src[token.side]
        assert card.src == token.src


class TestCardMoveOwnership:
    def test_move_only_by_the_holder(self, card):
        alice = User(name="alice", protocol=None)
        bob = User(name="bob", protocol=None)

        card.acquire(alice)
        card.move(5, 6, bob)
        assert (card.x, card.y) == (0, 0)

        card.move(5, 6, alice)
        assert (card.x, card.y) == (5, 6)

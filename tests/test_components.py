import json

import pytest

from Components.board import Board
from Components.bag import Bag
from Components.card import Card
from Components.component import Component
from Components.deck import Deck
from Components.dice import Dice
from Components.token import Token
from user import User


@pytest.fixture
def user():
    return User(name="alice", protocol=None)


class TestComponentIdentifiers:
    """
    Bug 1: Board/Token did not accept a component_id while Session passed one,
           so every create_session/join_session raised TypeError.
    Bug 2: every component called super().__init__(id), where `id` resolved to
           the builtin function, making self.id a non JSON-serialisable object.
    """

    def test_board_accepts_the_arguments_session_passes(self):
        board = Board("b1", 10, 20, "board.png", 800, 600)
        assert board.id == "b1"
        assert (board.x, board.y) == (10, 20)
        assert board.src == "board.png"
        assert (board.width, board.height) == (800, 600)

    def test_token_accepts_the_arguments_session_passes(self):
        token = Token("t1", 1, 2, "front.png", "back.png", 32, 32)
        assert token.id == "t1"
        assert (token.x, token.y) == (1, 2)
        assert token.image_src == {"front": "front.png", "back": "back.png"}
        assert (token.width, token.height) == (32, 32)

    @pytest.mark.parametrize("factory,args", [
        (Board, ("b1", 0, 0, "s.png", 8, 6)),
        (Token, ("t1", 0, 0, "f.png", "b.png", 3, 4)),
        (Card, ("c1", 0, 0, "f.png", "b.png", 3, 4, 0)),
        (Deck, ("d1", 0, 0, "s.png", 3, 4, "i.png")),
        (Dice, ("x1", 0, 0, "s.png", 3, 4, ["a.png", "b.png"])),
        (Bag, ("g1", 0, 0, 3, 4, "i.png")),
    ])
    def test_id_is_a_real_string_not_the_builtin(self, factory, args):
        component = factory(*args)
        assert isinstance(component.id, str)
        assert component.id == args[0]

    @pytest.mark.parametrize("factory,args", [
        (Board, ("b1", 0, 0, "s.png", 8, 6)),
        (Token, ("t1", 0, 0, "f.png", "b.png", 3, 4)),
    ])
    def test_return_json_is_serialisable(self, factory, args):
        payload = json.dumps(factory(*args).return_json())
        assert json.loads(payload)["id"] == args[0]

    def test_every_component_module_imports(self):
        """bag.py imported `from component` instead of `from Components.component`."""
        for module in ("bag", "board", "card", "component", "deck", "dice", "token"):
            __import__(f"Components.{module}")


class TestTokenAcquisition:
    """
    Bug 9: Token.acquire() added the component to user.acquired even when the
           acquisition was refused, so the client was told success=True while
           another user kept the component.
    """

    def test_acquire_succeeds_and_is_reported(self, user):
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)
        assert token.acquire(user) is True
        assert token.acquired_by is user
        assert user.acquired == ["t1"]

    def test_acquire_by_a_second_user_is_refused(self, user):
        other = User(name="bob", protocol=None)
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)

        assert token.acquire(user) is True
        assert token.acquire(other) is False

        assert token.acquired_by is user, "le detenteur initial doit etre conserve"
        assert other.acquired == [], "un acquire refuse ne doit rien ajouter"

    def test_reacquiring_by_the_same_user_is_idempotent(self, user):
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.acquire(user)
        assert user.acquired == ["t1"], "pas de doublon"

    def test_release_by_the_holder_reports_success(self, user):
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)
        token.acquire(user)
        assert token.release(user) is True
        assert token.acquired_by is None
        assert user.acquired == []

    def test_release_by_another_user_is_refused(self, user):
        other = User(name="bob", protocol=None)
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.release(other) is False
        assert token.acquired_by is user
        assert user.acquired == ["t1"]

    def test_move_only_by_the_holder(self, user):
        other = User(name="bob", protocol=None)
        token = Token("t1", 0, 0, "f.png", "b.png", 3, 4)
        token.acquire(user)

        token.move(99, 99, other)
        assert (token.x, token.y) == (0, 0), "un non-détenteur ne peut pas bouger"

        token.move(5, 6, user)
        assert (token.x, token.y) == (5, 6)


class TestNonAcquirableComponents:
    def test_base_component_refuses_acquisition(self, user):
        component = Component("x")
        assert component.acquire(user) is False
        assert component.release(user) is False

    def test_board_refuses_acquisition(self, user):
        assert Board("b1", 0, 0, "s.png", 8, 6).acquire(user) is False

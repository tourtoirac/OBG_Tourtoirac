import json

import pytest

from Components.board import Board
from Components.bag import Bag
from Components.card import Card
from Components.component import Component
from Components.deck import Deck
from Components.dice import Dice
from Components.token import MOVE_THRESHOLD, Token
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


class TestGreenBorder:
    """
    Le rectangle vert d'un jeton est pilote par le serveur : il disparait quand le
    jeton est depose loin de sa case de depart, revient quand il est depose a
    moins de MOVE_THRESHOLD pixels d'elle ( auquel cas il est recadre dessus ), ou
    quand "fixe la position" est utilise. Un jeton non repositionnable
    (move_border=False) n'est jamais recadre et n'affiche jamais le rectangle.
    """

    def test_a_token_starts_on_its_initial_square(self, user):
        assert Token("t1", 10, 20, "f.png", "b.png", 3, 4).in_place is True

    def test_a_frozen_token_starts_without_border(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, move_border=False)
        assert token.in_place is False

    def test_moving_loses_the_border(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        token.move(200, 300, user)
        assert token.in_place is False

    def test_dropping_back_on_the_initial_spot_restores_the_border(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)

        assert token.place(10, 20, user) is True
        assert token.in_place is True
        assert (token.x, token.y) == (10, 20)

    def test_dropping_elsewhere_keeps_the_border_off(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(200, 300, user) is True
        assert token.in_place is False
        assert (token.x, token.y) == (200, 300), "un dépôt loin n'est pas recadré"

    def test_dropping_just_under_the_threshold_snaps_back(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(10 + 20, 20 + 10, user) is True
        assert (token.x, token.y) == (10, 20), "29 px : le jeton doit être recadré"
        assert token.in_place is True

    def test_dropping_exactly_on_the_threshold_snaps_back(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(10 + MOVE_THRESHOLD, 20, user) is True
        assert (token.x, token.y) == (10, 20), "la frontière est inclusive"
        assert token.in_place is True

    def test_dropping_just_over_the_threshold_stays_put(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(10 + MOVE_THRESHOLD + 1, 20, user) is True
        assert (token.x, token.y) == (10 + MOVE_THRESHOLD + 1, 20), "31 px : pas de recadrage"
        assert token.in_place is False

    def test_the_threshold_is_measured_on_the_diagonal(self, user):
        """La distance est euclidienne : 21 et 21 pixels font 29.7, pas 42."""
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(10 + 21, 20 + 21, user) is True
        assert (token.x, token.y) == (10, 20)

    def test_a_frozen_token_is_never_snapped(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, move_border=False)
        token.acquire(user)

        assert token.place(11, 21, user) is True
        assert (token.x, token.y) == (11, 21), "un jeton figé reste où on le pose"
        assert token.in_place is False

    def test_a_frozen_token_never_regains_its_border(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, move_border=False)
        token.acquire(user)
        token.move(200, 300, user)

        token.place(10, 20, user)
        assert token.in_place is False, "un jeton figé ne doit jamais avoir de rectangle"

    def test_place_requires_the_holder(self, user):
        other = User(name="bob", protocol=None)
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)

        assert token.place(10, 20, other) is False
        assert (token.x, token.y) == (10, 20)
        assert token.in_place is True, "un dépôt refusé ne doit rien changer"

    def test_fix_position_restores_the_border(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)

        token.fix_position()
        assert token.in_place is True
        assert (token.x, token.y) == (200, 300), "fixe la position ne bouge pas le jeton"

    def test_fix_position_makes_the_current_spot_the_new_start(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)

        token.fix_position()

        assert (token.initial_x, token.initial_y) == (200, 300)
        assert token.near_initial_position(200, 300) is True
        assert token.near_initial_position(10, 20) is False

    def test_after_fix_position_a_token_snaps_to_its_new_start(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)
        token.fix_position()

        assert token.place(215, 310, user) is True
        assert (token.x, token.y) == (200, 300), "la nouvelle case de départ fait foi"
        assert token.in_place is True

    def test_fix_position_does_not_enable_a_frozen_token(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, move_border=False)
        token.acquire(user)
        token.move(200, 300, user)

        token.fix_position()
        assert token.in_place is False

    def test_return_json_exposes_the_border_state(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)

        data = token.return_json()
        assert data["move_border"] is True
        assert data["in_place"] is False
        assert data["x"] == 200 and data["y"] == 300


class TestShadowBorder:
    """
    border est une demande d'ombre faite par le jeu, et rien d'autre : elle rend
    le pion plus réaliste. A la difference du rectangle vert, elle ne depend ni
    des deplacements ni de move_border, et survit a toute la partie.
    """

    def test_a_token_has_no_shadow_by_default(self, user):
        assert Token("t1", 10, 20, "f.png", "b.png", 3, 4).border is False

    def test_the_game_can_ask_for_a_shadow(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, border=True)
        assert token.border is True

    def test_the_shadow_survives_a_move(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, border=True)
        token.acquire(user)

        token.move(200, 300, user)
        assert token.border is True, "l'ombre est un rendu, pas un etat de partie"
        assert token.in_place is False, "le rectangle vert, lui, part avec le jeton"

    def test_a_frozen_token_can_still_have_a_shadow(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, border=True, move_border=False)
        assert token.border is True
        assert token.in_place is False

    def test_the_shadow_does_not_follow_fix_position(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4)
        token.acquire(user)
        token.move(200, 300, user)

        token.fix_position()
        assert token.border is False

    def test_return_json_exposes_the_shadow(self, user):
        data = Token("t1", 10, 20, "f.png", "b.png", 3, 4, border=True).return_json()
        assert data["border"] is True


class TestSavedInPlace:
    """
    in_place est l'etat du rectangle vert tel qu'une sauvegarde l'a laisse. Le
    serveur le relit pour retrouver la partie la ou elle en etait.
    """

    def test_a_saved_token_comes_back_without_the_rectangle(self, user):
        token = Token("t1", 200, 300, "f.png", "b.png", 3, 4, initial=(10, 20), in_place=False)
        assert token.in_place is False
        assert (token.x, token.y) == (200, 300), "la position sauvee fait foi"

    def test_a_saved_token_comes_back_with_the_rectangle(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, in_place=True)
        assert token.in_place is True

    def test_a_saved_rectangle_never_reaches_a_frozen_token(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, in_place=True, move_border=False)
        assert token.in_place is False

    def test_the_saved_rectangle_is_independent_from_the_shadow(self, user):
        token = Token("t1", 200, 300, "f.png", "b.png", 3, 4, border=True, in_place=False)
        assert token.border is True
        assert token.in_place is False


class TestTransparentOrigin:
    """
    Un pion dont le game_json porte origin="transparent" affiche son image en
    transparence sur sa case de depart. Lâché sur cette image, il y est recadre
    même loin du seuil habituel : le fantôme tout entier est une cible.
    """

    def test_origin_defaults_to_none(self):
        assert Token("t1", 10, 20, "f.png", "b.png", 3, 4).origin is None

    def test_return_json_exposes_the_origin(self):
        token = Token("t1", 10, 20, "f.png", "b.png", 3, 4, origin="transparent")
        assert token.return_json()["origin"] == "transparent"

    def test_dropping_on_the_ghost_snaps_back_beyond_the_threshold(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")
        token.acquire(user)
        token.move(200, 300, user)

        # 60 px du départ : au-delà des 30 px habituels, mais sur le fantôme
        assert token.place(70, 20, user) is True
        assert (token.x, token.y) == (10, 20)
        assert token.in_place is True

    def test_dropping_off_the_ghost_does_not_snap(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")
        token.acquire(user)

        # le rectangle du fantôme va de 10 à 110 : 115 tombe juste dehors
        assert token.place(115, 20, user) is True
        assert (token.x, token.y) == (115, 20), "hors du fantôme, pas de recadrage"

    def test_an_ordinary_token_does_not_get_the_ghost_snap(self, user):
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100)
        token.acquire(user)

        assert token.place(70, 20, user) is True
        assert (token.x, token.y) == (70, 20), "sans origin, le seuil de 30 px reste seul juge"

    def test_the_ghost_sits_on_the_position_the_game_gave(self):
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")

        assert (token.origin_x, token.origin_y) == (10, 20)
        assert (token.initial_x, token.initial_y) == (10, 20)

    def test_a_token_without_a_ghost_has_no_ghost_position(self):
        """
        Sans origin="transparent" le champ ne decrit rien : le garder a la valeur
        du x/y ferait croire a un fantome la ou le jeu n'en a pose aucun.
        """
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100)

        assert token.origin_x is None
        assert token.origin_y is None
        assert token.return_json()["origin_x"] is None

    def test_fixing_the_position_leaves_the_ghost_where_it_is(self):
        """
        "Fixe la position" dit ou revient le pion. Le fantome, lui, est un repere
        que le jeu a pose sur son plateau : il ne suit pas.
        """
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")
        token.x, token.y = 400, 500

        token.fix_position()

        assert (token.initial_x, token.initial_y) == (400, 500)
        assert (token.origin_x, token.origin_y) == (10, 20)

    def test_a_saved_ghost_is_read_back_where_it_was(self):
        """
        Une session reprise a deja son fantome pose : le serveur le rend tel quel,
        sans le reafficher sur la position du pion.
        """
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")
        token.x, token.y = 400, 500
        token.fix_position()

        saved = token.return_json()
        again = Token("t1", saved["x"], saved["y"], "f.png", "b.png", 100, 100,
                      origin=saved["origin"],
                      initial=(saved["initial_x"], saved["initial_y"]),
                      origin_x=saved["origin_x"], origin_y=saved["origin_y"])

        assert (again.x, again.y) == (400, 500)
        assert (again.initial_x, again.initial_y) == (400, 500)
        assert (again.origin_x, again.origin_y) == (10, 20)

    def test_a_ghost_alone_still_snaps_the_token_home(self, user):
        """
        Le fantome est sur sa case d'origine, la case de retour sur une autre. De、
        le pointeur vise le fantome : le pion revient chez lui, pas sur l'image.
        """
        token = Token("t1", 10, 20, "f.png", "b.png", 100, 100, origin="transparent")
        token.x, token.y = 400, 500
        token.fix_position()
        token.acquire(user)

        # 30 px du fantome, bien au-dela du seuil de 30 px autour de la case de retour
        assert token.place(40, 20, user) is True
        assert (token.x, token.y) == (400, 500)


class TestNonAcquirableComponents:
    def test_base_component_refuses_acquisition(self, user):
        component = Component("x")
        assert component.acquire(user) is False
        assert component.release(user) is False

    def test_board_refuses_acquisition(self, user):
        assert Board("b1", 0, 0, "s.png", 8, 6).acquire(user) is False

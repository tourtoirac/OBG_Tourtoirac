"""
Tests of the game setup: the modifications a game asks for once its components
are all in place, read from the game_json, applied by the server and sent to
every screen.

The positions come from the game, not from the client, so what a client can send
matters as much as what the setup does : the actions are exercised through the
message handlers with fakes rather than the WebSocket layer, the way
test_counter.py does, so the setup is covered even where autobahn is missing.
"""
import logging

import pytest

from Components.token import Token
from handle_message import apply_setup
from session import Session, read_coordinate, read_setup
from user import User

LOGGER = logging.getLogger("tests")

BOARD_JSON = {
    "x": 0,
    "y": 0,
    "id": "board",
    "kind": "board",
    "src": "/map.jpg",
    "width": 800,
    "height": 600,
}

COUNTER_JSON = {
    "x": 300,
    "y": 400,
    "id": "counter",
    "kind": "counter",
    "width": 60,
    "height": 40,
    "color": "#204080",
}

TOKEN_JSON = {
    "x": 100,
    "y": 100,
    "id": "token",
    "kind": "token",
    "front_src": "/counters/army.png",
    "back_src": "/counters/army_back.png",
    "width": 40,
    "height": 40,
}

# un pion sans dos : le serveur ne peut pas le retourner
PLAIN_TOKEN_JSON = dict(TOKEN_JSON, id="plain", back_src=None)

GAME_JSON = {
    "game": {"max_players": 2, "max_watchers": 2},
    "fixed": [BOARD_JSON, dict(COUNTER_JSON)],
    "movable": [TOKEN_JSON, PLAIN_TOKEN_JSON],
    "dice": [],
}

SETUP = [
    {"component_id": "token", "x": 500, "y": 510},
    {"component_id": "counter", "x": 20, "y": 30},
]


def build_session(setup=None, **token_fields):
    """Une session avec un plateau, un compteur et deux pions."""
    token = dict(TOKEN_JSON)
    token.update(token_fields)
    game_json = {**GAME_JSON, "movable": [token, PLAIN_TOKEN_JSON]}
    if setup is not None:
        game_json = {**game_json, "setup": setup}
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class FakeUser(User):
    """Un joueur dont on garde ce que la session lui envoie."""

    def __init__(self, name="alice", role="player", session=None):
        super().__init__(name=name, protocol=None)
        self.role = role
        self.session = session
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeProtocol:
    """Le minimum que le handler attend : un lobby et l'envoi des erreurs."""

    def __init__(self, user):
        self.user = user
        self.sent = []
        self.factory = type("Factory", (), {"lobby": self})()

    def get_user(self, protocol):
        return self.user

    def send_error(self, code, message):
        self.sent.append({"event": "error", "error": {"code": code, "message": message}})


def handler_for(setup=None, role="player", **token_fields):
    """Le handler apply_setup et le joueur qui l'a déclenché."""
    session = build_session(setup=setup, **token_fields)
    user = FakeUser(role=role, session=session)
    session.players = [user] if role == "player" else []
    session.watchers = [user] if role != "player" else []
    return apply_setup, FakeProtocol(user), user, session


def call(protocol):
    apply_setup(protocol, LOGGER, {"action": "apply_setup"})


class TestReadCoordinate:
    @pytest.mark.parametrize("value,expected", [
        (100, 100), (0, 0), (-5, -5), (12.7, 12),
    ])
    def test_a_number_is_kept(self, value, expected):
        assert read_coordinate(value) == expected

    @pytest.mark.parametrize("value", [None, True, False, "100", [], {}, ()])
    def test_anything_else_is_no_coordinate(self, value):
        """
        Un game_json s'ecrit a la main. Une coordonnee que le serveur ne sait pas
        lire ne doit pas deplacer le composant sur 0.
        """
        assert read_coordinate(value) is None


class TestReadSetup:
    def test_a_well_written_setup_is_kept(self):
        assert read_setup(SETUP) == SETUP

    def test_an_absent_setup_leaves_the_game_alone(self):
        for setup in (None, {}, "token", 42, []):
            assert read_setup(setup) == []

    def test_an_entry_without_component_id_is_ignored(self):
        """
        Sans component_id, l'entree ne dit pas quoi deplacer : mieux vaut une
        partie qui demarre qu'une partie qui ne demarre pas.
        """
        assert read_setup([{"x": 10, "y": 20}, {"component_id": "", "x": 1}]) == []

    def test_an_entry_that_is_not_an_object_is_ignored(self):
        assert read_setup(["token", 42, None, ["token"]]) == []

    def test_an_unreadable_coordinate_is_dropped(self):
        assert read_setup([
            {"component_id": "token", "x": "100", "y": 200},
        ]) == [{"component_id": "token", "y": 200}]

    def test_an_absent_coordinate_is_left_out(self):
        assert read_setup([
            {"component_id": "token", "x": 100},
        ]) == [{"component_id": "token", "x": 100}]

    def test_a_face_that_does_not_exist_is_dropped(self):
        assert read_setup([
            {"component_id": "token", "side": "edge"},
        ]) == [{"component_id": "token"}]

    @pytest.mark.parametrize("side", ["front", "back"])
    def test_both_faces_are_accepted(self, side):
        assert read_setup([
            {"component_id": "token", "side": side},
        ]) == [{"component_id": "token", "side": side}]


class TestTheSetupIsNotPartOfTheSession:
    def test_an_applied_setup_is_not_saved_with_the_session(self):
        """
        Le setup decrit la mise en place, pas la partie. Une fois applique, ses
        positions sont deja dans "fixed" et "movable" : le recopier ferait
        replacer les pions a chaque reprise de session.
        """
        session = build_session(setup=SETUP)
        session.apply_setup()

        assert "setup" not in session.game_json_state()

    def test_a_pending_setup_stays_in_the_saved_state(self):
        """
        L'etat est stocke des que le dernier joueur quitte la page du lobby, donc
        avant que la page de jeu ne soit chargee et que le setup n'ait ete
        demande. Le retirer de l'etat a ce stade reviendrait a interdire a la
        partie de s'installer du tout.
        """
        state = build_session(setup=SETUP).game_json_state()

        assert state["setup"] == SETUP

    def test_a_session_rebuilt_before_the_setup_still_installs_itself(self):
        """
        Le passage lobby -> jeu vide la session et la fait reconstruire depuis
        l'etat stocke. C'est le trajet ordinaire d'une partie : le setup doit
        survive a ce passage, sans quoi la partie demarre hors de sa mise en
        place.
        """
        first = build_session(setup=SETUP)

        again = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=first.game_json_state(),
        )

        assert again.pending_setup() == SETUP
        again.apply_setup()
        assert (again.get_component("token").x, again.get_component("token").y) == (500, 510)

    def test_a_resumed_session_does_not_replay_the_setup(self):
        """
        Une session reprise a deja les positions que le setup a produites : le
        redemander les replacerait au milieu du jeu.
        """
        first = build_session(setup=SETUP)
        first.apply_setup()

        again = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=first.game_json_state(),
        )

        assert again.setup == []
        assert again.get_component("token").x == 500
        assert again.pending_setup() == []

    def test_the_saved_state_keeps_the_moved_positions(self):
        first = build_session(setup=SETUP)
        first.apply_setup()

        state = first.game_json_state()

        assert {c["id"]: c for c in state["movable"]}["token"]["x"] == 500

    def test_a_resumed_ghost_does_not_follow_the_pawn(self):
        """
        Le fantome ne bouge jamais, pas meme a travers une sauvegarde : la reprise
        lit la place qu'il avait, pas celle du pion de l'etat sauvegarde.
        """
        first = build_session(setup=SETUP, origin="transparent")
        first.apply_setup()

        again = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=first.game_json_state(),
        )
        token = again.get_component("token")

        assert (token.x, token.y) == (500, 510)
        assert (token.origin_x, token.origin_y) == (100, 100)

    def test_the_game_json_of_the_game_is_untouched(self):
        """
        Le jeu est partage par toutes ses sessions : ecrire dedans les modifierait
        pour les parties suivantes.
        """
        game_json = {**GAME_JSON, "setup": SETUP}
        session = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=game_json,
        )

        session.apply_setup()

        assert game_json["setup"] == SETUP


class TestApplyingTheSetup:
    def test_a_component_is_moved(self):
        session = build_session(setup=SETUP)
        session.apply_setup()

        token = session.get_component("token")

        assert (token.x, token.y) == (500, 510)

    def test_the_transparent_ghost_stays_where_the_game_put_it(self):
        """
        Le setup installe le jeu, il ne deplace pas les reperes que le jeu a poses
        sur son plateau. Un pion "transparent" garde donc son fantome sur le x/y du
        game_json, meme apres avoir ete deplace : sa case de depart, elle, suit.
        """
        session = build_session(setup=SETUP, origin="transparent")
        session.apply_setup()

        token = session.get_component("token")

        assert (token.x, token.y) == (500, 510)
        assert (token.initial_x, token.initial_y) == (500, 510), "la case de retour suit le setup"
        assert (token.origin_x, token.origin_y) == (100, 100), "le fantome reste sur sa case"

    def test_a_fixed_component_is_moved_too(self):
        session = build_session(setup=SETUP)
        session.apply_setup()

        counter = session.get_component("counter")

        assert (counter.x, counter.y) == (20, 30)

    def test_the_position_is_also_the_new_home_of_the_token(self):
        """
        Le rectangle vert et le fantome d'un pion marquent sa case de depart : ils
        doivent suivre le setup, sinon ils resteraient sur la position du
        game_json que le jeu a justement remplacee.
        """
        session = build_session(setup=SETUP)
        session.apply_setup()

        token = session.get_component("token")

        assert (token.initial_x, token.initial_y) == (500, 510)
        assert token.in_place is True

    def test_the_coordinates_follow_the_move(self):
        session = build_session(setup=SETUP)
        session.apply_setup()

        assert session.get_component("token").coordinates == (500, 510)

    def test_a_partial_entry_moves_only_what_it_names(self):
        session = build_session(setup=[{"component_id": "token", "x": 500}])
        session.apply_setup()

        token = session.get_component("token")

        assert token.x == 500
        assert token.y == 100

    def test_a_component_the_game_does_not_have_is_skipped(self):
        """
        Un setup qui parle d'un composant absent est une faute de saisie du jeu :
        le reste de la mise en place doit quand même s'appliquer.
        """
        session = build_session(setup=[
            {"component_id": "nope", "x": 1, "y": 2},
            {"component_id": "token", "x": 500, "y": 510},
        ])

        applied = session.apply_setup()

        assert session.get_component("token").x == 500
        assert [c["id"] for c in applied] == ["token"]

    def test_the_components_are_announced_to_the_client(self):
        applied = build_session(setup=SETUP).apply_setup()

        assert {c["id"] for c in applied} == {"token", "counter"}
        token = next(c for c in applied if c["id"] == "token")
        assert (token["x"], token["y"]) == (500, 510)

    def test_applying_twice_moves_nothing(self):
        """
        Les positions sont absolues : un second joueur qui demande le setup ne
        doit pas deplacer les pions une deuxieme fois.
        """
        session = build_session(setup=SETUP)
        session.apply_setup()
        # un joueur a bouge le pion entre les deux demandes
        session.get_component("token").x = 900

        session.apply_setup()

        assert session.get_component("token").x == 500

    def test_a_component_is_announced_once(self):
        """
        Un composant peut figurer dans deux entrees, une qui le deplace et une
        qui le retourne : on ne l'annonce qu'une fois, dans son etat final.
        """
        applied = build_session(setup=[
            {"component_id": "token", "x": 500, "y": 510},
            {"component_id": "token", "side": "back"},
        ]).apply_setup()

        assert len(applied) == 1
        assert (applied[0]["x"], applied[0]["y"], applied[0]["side"]) == (500, 510, "back")


class TestTheSetupAndTheFace:
    def test_a_flippable_token_is_turned_over(self):
        session = build_session(setup=[{"component_id": "token", "side": "back"}])
        session.apply_setup()

        token = session.get_component("token")

        assert token.side == "back"
        assert token.src == token.image_src["back"]

    def test_a_token_can_be_put_back_on_its_front(self):
        session = build_session(setup=[
            {"component_id": "token", "side": "back"},
            {"component_id": "token", "side": "front"},
        ])

        session.apply_setup()

        assert session.get_component("token").side == "front"

    def test_a_token_without_a_back_keeps_its_face(self):
        """
        Le jeu a demande un retournement que ce pion ne peut pas faire : il reste
        sur sa face, plutot que de pointer vers une image absente.
        """
        session = build_session(setup=[{"component_id": "plain", "side": "back"}])
        session.apply_setup()

        assert session.get_component("plain").side == "front"

    def test_a_position_and_a_face_can_be_asked_at_once(self):
        session = build_session(setup=[
            {"component_id": "token", "x": 500, "y": 510, "side": "back"},
        ])

        session.apply_setup()

        token = session.get_component("token")
        assert (token.x, token.y, token.side) == (500, 510, "back")

    def test_a_face_does_not_move_the_token(self):
        session = build_session(setup=[{"component_id": "token", "side": "back"}])
        session.apply_setup()

        token = session.get_component("token")

        assert (token.x, token.y) == (100, 100)
        assert token.in_place is True

    def test_a_counter_has_no_second_face(self):
        """
        Un compteur ne se retourne pas : le setup le laisse tel quel, il ne peut
        ni le deplacer ni lui faire changer de face.
        """
        session = build_session(setup=[
            {"component_id": "counter", "side": "back"},
        ])

        applied = session.apply_setup()

        assert session.get_component("counter").set_side("back") is False
        assert (applied[0]["x"], applied[0]["y"]) == (300, 400)

    def test_the_face_is_announced_to_the_client(self):
        applied = build_session(setup=[
            {"component_id": "token", "side": "back"},
        ]).apply_setup()

        assert applied[0]["side"] == "back"
        assert applied[0]["front_src"] == TOKEN_JSON["front_src"]
        assert applied[0]["back_src"] == TOKEN_JSON["back_src"]


class TestSetSide:
    def test_a_token_without_a_back_refuses_the_face(self):
        assert Token("t", 0, 0, "front.png", None, 10, 10).set_side("back") is False

    @pytest.mark.parametrize("side", ["front", "back"])
    def test_a_token_with_a_back_accepts_the_face(self, side):
        token = Token("t", 0, 0, "front.png", "back.png", 10, 10)

        assert token.set_side(side) is True
        assert token.side == side
        assert token.src == token.image_src[side]

    def test_a_face_that_does_not_exist_lands_on_the_front(self):
        token = Token("t", 0, 0, "front.png", "back.png", 10, 10)

        token.set_side("edge")

        assert token.side == "front"


class TestTheSetupIsSentToTheClient:
    def test_the_setup_is_announced_while_it_is_pending(self):
        assert build_session(setup=SETUP).return_session_json()["setup"] == SETUP

    def test_a_game_without_setup_announces_nothing(self):
        assert build_session().return_session_json()["setup"] == []

    def test_the_setup_is_announced_once(self):
        session = build_session(setup=SETUP)
        session.apply_setup()

        assert session.return_session_json()["setup"] == []
        assert session.pending_setup() == []


class TestApplySetupHandler:
    def test_the_setup_is_applied_and_broadcast(self):
        _, protocol, user, session = handler_for(setup=SETUP)

        call(protocol)

        assert user.sent[-1]["event"] == "setup"
        assert {c["id"] for c in user.sent[-1]["components"]} == {"token", "counter"}
        assert session.get_component("token").x == 500

    def test_every_screen_receives_the_new_positions(self):
        _, protocol, _, session = handler_for(setup=SETUP)
        watcher = FakeUser(name="carol", role="watcher", session=session)
        session.watchers.append(watcher)

        call(protocol)

        token = next(c for c in watcher.sent[-1]["components"] if c["id"] == "token")
        assert (token["x"], token["y"]) == (500, 510)

    def test_the_client_chooses_nothing(self):
        """
        Le client dit seulement que tout est charge. Des positions envoyees en
        plus doivent etre ignorees : la mise en place est celle du jeu.
        """
        _, protocol, _, session = handler_for(setup=SETUP)

        apply_setup(protocol, LOGGER, {
            "action": "apply_setup",
            "components": [{"component_id": "token", "x": 1, "y": 2}],
        })

        assert session.get_component("token").x == 500

    def test_a_watcher_does_not_install_the_game(self):
        """
        Un spectateur regarde la partie : il ne peut pas la mettre en place. Le
        client n'envoie deja rien dans ce cas, mais il n'est pas une autorisation.
        """
        _, protocol, _, session = handler_for(setup=SETUP, role="watcher")

        call(protocol)

        assert protocol.sent[-1]["error"]["code"] == "watcher_not_allowed"
        assert session.get_component("token").x == 100

    def test_a_game_without_setup_is_refused(self):
        _, protocol, _, _ = handler_for()

        call(protocol)

        assert protocol.sent[-1]["error"]["code"] == "no_setup"

    def test_a_second_request_moves_nothing(self):
        _, protocol, user, session = handler_for(setup=SETUP)

        call(protocol)
        session.get_component("token").x = 900
        call(protocol)

        assert user.sent[-1]["components"][0]["x"] == 500

    def test_the_setup_is_installed_even_if_nothing_moves(self):
        """
        Un setup qui ne parle que d'une face a quand meme ete applique : la
        session ne doit pas le redemander indefiniment.
        """
        _, protocol, _, session = handler_for(setup=[
            {"component_id": "plain", "side": "back"},
        ])

        call(protocol)

        assert session.setup_done is True
"""
Tests of the counter component: reading it from the game_json, the increment and
decrement actions, and what reaches the other players.

The value is computed by the server, so the actions are exercised through the
message handlers rather than by calling Counter.increase_value() alone. Those
handlers only need a lobby, a user and a session, so they are driven here with
fakes instead of the WebSocket layer : the counter is then covered even where
autobahn is not installed.
"""
import logging

import pytest

from Components.counter import (
    DEFAULT_COUNTER_COLOR,
    Counter,
    read_limit,
    read_value,
)
from handle_message import decrement, increment
from session import Session
from user import User

LOGGER = logging.getLogger("tests")

COUNTER_JSON = {
    "x": 300,
    "y": 400,
    "id": "counter",
    "kind": "counter",
    "width": 60,
    "height": 40,
    "color": "#204080",
    "font_color": "#ffcc00",
    "value": 3,
}

TOKEN_JSON = {
    "x": 100,
    "y": 100,
    "id": "token",
    "kind": "token",
    "front_src": "/counters/army.png",
    "back_src": None,
    "width": 40,
    "height": 40,
}

BOARD_JSON = {
    "x": 0,
    "y": 0,
    "id": "board",
    "kind": "board",
    "src": "/map.jpg",
    "width": 800,
    "height": 600,
}

GAME_JSON = {
    "game": {"max_players": 2, "max_watchers": 2},
    "fixed": [BOARD_JSON, dict(COUNTER_JSON)],
    "movable": [TOKEN_JSON],
    "dice": [],
}


def build_session(**counter_fields):
    """Une session avec un plateau, un pion et un compteur."""
    counter = dict(COUNTER_JSON)
    counter.update(counter_fields)
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std",
        game_json={**GAME_JSON, "fixed": [BOARD_JSON, counter]},
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
    """Le minimum que les handlers attendent : un lobby et l'envoi des erreurs."""

    def __init__(self, user):
        self.user = user
        self.sent = []
        self.factory = type("Factory", (), {"lobby": self})()

    def get_user(self, protocol):
        return self.user

    def send_error(self, code, message):
        self.sent.append({"event": "error", "error": {"code": code, "message": message}})


def handler_for(role="player", **counter_fields):
    """Un handler et le joueur qui l'a déclenché, sur une session neuve."""
    session = build_session(**counter_fields)
    user = FakeUser(role=role, session=session)
    session.players = [user] if role == "player" else []
    session.watchers = [user] if role != "player" else []
    protocol = FakeProtocol(user)
    return increment, decrement, protocol, user, session


def call(handler, protocol, component_id):
    handler(protocol, LOGGER, {"action": "x", "component_id": component_id})


class TestReadValue:
    @pytest.mark.parametrize("value,expected", [
        (3, 3), (-2, -2), (0, 0), (2.7, 2),
    ])
    def test_a_number_is_kept(self, value, expected):
        assert read_value(value) == expected

    @pytest.mark.parametrize("value", ["3", None, True, False, [], {}, (1,)])
    def test_anything_else_starts_at_zero(self, value):
        """
        Un game_json s'ecrit a la main. Une valeur que le serveur ne sait pas
        lire ne doit pas casser la session : le compteur affiche zero.
        """
        assert read_value(value) == 0

    def test_a_missing_value_starts_at_zero(self):
        assert Counter("c1", 0, 0, 10, 10, "#000", None).value == 0


class TestReadLimit:
    @pytest.mark.parametrize("limit,expected", [
        (5, 5), (0, 0), (-3, -3), (2.7, 2),
    ])
    def test_a_number_is_kept(self, limit, expected):
        assert read_limit(limit) == expected

    @pytest.mark.parametrize("limit", [None, True, False, "3", [], {}, ()])
    def test_anything_else_means_no_bound(self, limit):
        """
        Une borne absente vaut "aucune borne", et surtout pas zero : un jeu
        qui n'interdit rien verrait son compteur bloque a zero.
        """
        assert read_limit(limit) is None

    def test_zero_is_a_bound_not_an_absence_of_bound(self):
        """
        Un jeu dont le score ne descend pas sous zero l'ecrit min a 0. Le
        serveur doit le lire comme la borne qu'il est.
        """
        assert read_limit(0) == 0


class TestLoadingCounter:
    def test_a_counter_is_loaded_with_its_value(self):
        assert build_session().get_component("counter").value == 3

    def test_a_counter_keeps_the_color_the_game_chose(self):
        assert build_session().get_component("counter").color == "#204080"

    def test_a_counter_keeps_the_font_color_the_game_chose(self):
        """
        Un jeu peut vouloir un cadre discret et un score qui se voit : la couleur
        du chiffre se choisit donc separement de celle du cadre.
        """
        assert build_session().get_component("counter").font_color == "#ffcc00"

    def test_without_font_color_the_value_stays_readable_on_a_dark_background(self):
        """
        Un jeu qui ne dit rien n'obtient pas un chiffre invisible : le serveur lui
        choisit une couleur lisible sur le fond qu'il a declare.
        """
        counter = dict(COUNTER_JSON)
        counter.pop("font_color")
        session = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std",
            game_json={**GAME_JSON, "fixed": [BOARD_JSON, counter]},
        )

        loaded = session.get_component("counter")

        assert loaded.font_color != loaded.color
        assert loaded.font_color == "#ffffff"

    def test_the_value_is_never_painted_in_its_own_background(self):
        """
        Le cas du json d'arvn : fond jaune vif, aucune font_color. Le chiffre ne
        doit pas disparaitre dans son pave.
        """
        counter = Counter("c1", 0, 0, 110, 110, "#f9d307", 1)

        assert counter.font_color != "#f9d307"
        assert counter.font_color == "#000000"

    def test_a_light_background_gets_a_dark_value(self):
        assert Counter("c1", 0, 0, 10, 10, "#ffffff", 0).font_color == "#000000"

    def test_a_dark_background_gets_a_light_value(self):
        assert Counter("c1", 0, 0, 10, 10, "#000000", 0).font_color == "#ffffff"

    def test_an_empty_font_color_falls_back_on_the_readable_one(self):
        counter = Counter("c1", 0, 0, 10, 10, "#000000", 0, font_color="")

        assert counter.font_color == "#ffffff"

    def test_an_unreadable_background_color_does_not_break_the_counter(self):
        """
        Une couleur que le serveur ne sait pas lire ne doit pas faire echouer le
        chargement d'un jeu : le chiffre part clair, le choix le plus sur.
        """
        for fond in ("rgb(249, 211, 7)", "yellow", "#fff", None, 42):
            counter = Counter("c1", 0, 0, 10, 10, fond, 1)

            assert counter.font_color == "#ffffff", f"fond {fond!r}"

    def test_a_counter_without_color_falls_back_on_the_default(self):
        counter = dict(COUNTER_JSON)
        counter.pop("color")
        session = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std",
            game_json={**GAME_JSON, "fixed": [BOARD_JSON, counter]},
        )

        assert session.get_component("counter").color == DEFAULT_COUNTER_COLOR

    def test_a_counter_is_a_fixed_component(self):
        """
        Le client le dessine avec le plateau, pas avec les pions : un compteur
        declare parmi les pions bougerait avec eux.
        """
        session = build_session()

        fixed = [c.id for c in session.components_lists["fixed"]]
        movable = [c.id for c in session.components_lists["movable"]]

        assert "counter" in fixed
        assert "counter" not in movable

    def test_the_counter_is_announced_to_the_client(self):
        sent = {c["id"]: c for c in build_session().return_session_json()["components"]["fixed"]}

        assert sent["counter"] == {
            "kind": "counter",
            "id": "counter",
            "x": 300,
            "y": 400,
            "width": 60,
            "height": 40,
            "color": "#204080",
            "font_color": "#ffcc00",
            "value": 3,
            "min": None,
            "max": None,
        }

    def test_the_value_is_saved_and_restored(self):
        """
        Une session enregistree emporte la valeur du compteur : une partie
        reprise montre le score atteint par les joueurs.
        """
        first = build_session()
        first.get_component("counter").increase_value(4)

        again = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=first.game_json_state(),
        )

        assert again.get_component("counter").value == 7

    def test_fix_positions_leaves_the_counter_alone(self):
        """
        "Fixe la position" deplace les cases de depart des pions. Un compteur
        n'a pas de pion a recadrer, et sa valeur ne doit pas repartir a zero.
        """
        session = build_session()
        session.get_component("counter").increase_value(5)

        session.fix_positions()

        assert session.get_component("counter").value == 8


class TestIncrement:
    def test_an_increment_is_accepted(self):
        up, _, protocol, user, _ = handler_for()

        call(up, protocol, "counter")

        assert user.sent[-1] == {
            "event": "counter_value", "component_id": "counter", "value": 4,
        }

    def test_the_server_keeps_the_new_value(self):
        up, _, protocol, _, session = handler_for()

        call(up, protocol, "counter")

        assert session.get_component("counter").value == 4

    def test_two_increments_stack_up(self):
        up, _, protocol, user, _ = handler_for()

        call(up, protocol, "counter")
        call(up, protocol, "counter")

        assert user.sent[-1]["value"] == 5

    def test_the_new_value_reaches_the_spectators(self):
        up, _, protocol, _, session = handler_for()
        watcher = FakeUser(name="carol", role="watcher", session=session)
        session.watchers.append(watcher)

        call(up, protocol, "counter")

        assert watcher.sent[-1]["value"] == 4
        assert watcher.sent[-1]["component_id"] == "counter"

    def test_the_client_cannot_choose_the_value(self):
        """
        Le client dit seulement quel bouton il a vise. Une valeur envoyee en plus
        doit etre ignoree, sinon le score serait celui qu'un joueur a decide.
        """
        up, _, protocol, _, session = handler_for()

        up(protocol, LOGGER, {
            "action": "increment", "component_id": "counter", "value": 999,
        })

        assert session.get_component("counter").value == 4

    def test_a_missing_component_id_is_refused(self):
        up, _, protocol, _, session = handler_for()

        up(protocol, LOGGER, {"action": "increment"})

        assert protocol.sent[-1]["error"]["code"] == "missing_field"
        assert session.get_component("counter").value == 3

    def test_an_unknown_counter_is_refused(self):
        up, _, protocol, _, _ = handler_for()

        call(up, protocol, "nope")

        assert protocol.sent[-1]["error"]["code"] == "component_not_found"


class TestDecrement:
    def test_a_decrement_is_accepted(self):
        _, down, protocol, user, _ = handler_for()

        call(down, protocol, "counter")

        assert user.sent[-1] == {
            "event": "counter_value", "component_id": "counter", "value": 2,
        }

    def test_the_value_may_go_below_zero(self):
        """
        Rien ne l'interdit ici : un jeu dont le score ne descend pas sous zero
        le dit dans ses regles, le composant ne fait pas de police a cote.
        """
        _, down, protocol, user, _ = handler_for()

        for _ in range(4):
            call(down, protocol, "counter")

        assert user.sent[-1]["value"] == -1


class TestReadingTheBounds:
    def test_the_bounds_are_read_from_the_game(self):
        counter = build_session(min=1, max=10).get_component("counter")

        assert counter.min == 1
        assert counter.max == 10

    def test_an_absent_bound_leaves_the_value_free(self):
        """
        Un jeu qui ne dit rien n'a pas de plage : le compteur monte et descend
        sans fin, des deux cotes.
        """
        counter = build_session().get_component("counter")

        assert counter.min is None
        assert counter.max is None

    def test_a_bound_on_one_side_only_leaves_the_other_free(self):
        counter = build_session(max=10).get_component("counter")

        assert counter.min is None
        assert counter.max == 10

    def test_an_unreadable_bound_leaves_the_value_free(self):
        """
        Une borne que le serveur ne sait pas lire ne doit pas casser le
        chargement du jeu, ni surtout rester une borne a zero par accident.
        """
        counter = build_session(min="1", max=True).get_component("counter")

        assert counter.min is None
        assert counter.max is None

    def test_the_value_starts_inside_the_announced_range(self):
        """
        Un jeu qui ouvre a 5 alors que son max vaut 3 afficherait d'emblee un
        etat que ses propres regles interdisent.
        """
        assert build_session(min=1, max=3, value=5).get_component("counter").value == 3
        assert build_session(min=1, max=3, value=-4).get_component("counter").value == 1

    def test_inverted_bounds_are_put_back_in_order(self):
        """
        Un jeu qui ecrit min 10 et max 0 veut une plage, pas un compteur bloque
        : les deux bornes sont intervertiees plutot que de ne plus jamais bouger.
        """
        counter = build_session(min=10, max=0).get_component("counter")

        assert (counter.min, counter.max) == (0, 10)
        assert counter.incrementable() is True
        assert counter.decrementable() is True

    def test_the_bounds_are_announced_to_the_client(self):
        """
        Le client grise un signe devenu impossible : il lui faut donc les bornes,
        pas seulement la valeur.
        """
        sent = {c["id"]: c for c in
                build_session(min=1, max=10).return_session_json()["components"]["fixed"]}

        assert sent["counter"]["min"] == 1
        assert sent["counter"]["max"] == 10

    def test_the_bounds_survive_a_restored_session(self):
        """
        Une session reprise doit rester bornee : sinon le compteur redecoivre
        les limites que le jeu lui avait donnees.
        """
        again = Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=build_session(min=1, max=10).game_json_state(),
        )
        counter = again.get_component("counter")

        assert (counter.min, counter.max) == (1, 10)
        assert counter.increase_value(99) == 10


class TestAtTheBounds:
    def test_the_value_stops_at_max(self):
        counter = build_session(min=1, max=4).get_component("counter")

        for _ in range(5):
            counter.increase_value()

        assert counter.value == 4

    def test_the_value_stops_at_min(self):
        counter = build_session(min=1, max=4).get_component("counter")

        for _ in range(5):
            counter.decrease_value()

        assert counter.value == 1

    def test_a_big_step_cannot_jump_over_a_bound(self):
        """
        Meme un cran enorme reste dans la plage : c'est la borne qui arrete la
        valeur, pas la taille du pas.
        """
        counter = build_session(min=1, max=4, value=3).get_component("counter")

        assert counter.increase_value(100) == 4
        assert counter.decrease_value(100) == 1

    def test_the_sign_at_the_bound_is_disabled(self):
        """
        C'est ce que le client dessine en gris : le + meurt a max, le - a min.
        """
        counter = build_session(min=1, max=4).get_component("counter")

        assert counter.incrementable() is True
        assert counter.decrementable() is True

        counter.value = 4
        assert counter.incrementable() is False
        assert counter.decrementable() is True

        counter.value = 1
        assert counter.incrementable() is True
        assert counter.decrementable() is False

    def test_both_signs_are_disabled_on_a_fixed_counter(self):
        """
        Une plage d'un seul point : min vaut max, le compteur ne bouge plus et
        les deux signes sont morts.
        """
        counter = build_session(min=2, max=2).get_component("counter")

        assert counter.incrementable() is False
        assert counter.decrementable() is False

    def test_a_free_counter_never_disables_a_sign(self):
        counter = build_session().get_component("counter")

        counter.value = 9999
        assert counter.incrementable() is True

        counter.value = -9999
        assert counter.decrementable() is True


class TestHandlersAtTheBounds:
    def test_an_increment_on_max_is_refused(self):
        up, _, protocol, user, session = handler_for(min=1, max=4, value=4)

        call(up, protocol, "counter")

        assert protocol.sent[-1]["error"]["code"] == "component_not_incrementable"
        assert session.get_component("counter").value == 4
        # rien n'est diffuse : personne ne doit voir une valeur qui n'a pas bouge
        assert user.sent == []

    def test_a_decrement_on_min_is_refused(self):
        _, down, protocol, user, session = handler_for(min=1, max=4, value=1)

        call(down, protocol, "counter")

        assert protocol.sent[-1]["error"]["code"] == "component_not_decrementable"
        assert session.get_component("counter").value == 1
        assert user.sent == []

    def test_the_other_sign_still_works_at_a_bound(self):
        """
        Atteindre max ne doit pas bloquer la baisse : seule la borne atteinte
        est morte.
        """
        up, down, protocol, user, session = handler_for(min=1, max=4, value=4)

        call(up, protocol, "counter")
        assert protocol.sent[-1]["error"]["code"] == "component_not_incrementable"

        call(down, protocol, "counter")

        assert user.sent[-1]["value"] == 3
        assert session.get_component("counter").value == 3

    def test_the_last_step_before_a_bound_is_still_accepted(self):
        """
        Le refus ne doit pas commencer trop tot : la valeur juste sous la borne
        se refuse pas encore, elle monte.
        """
        up, _, protocol, user, session = handler_for(min=1, max=4, value=3)

        call(up, protocol, "counter")

        assert user.sent[-1]["value"] == 4
        assert session.get_component("counter").value == 4

    def test_a_one_point_range_refuses_both_signs(self):
        up, down, protocol, _, _ = handler_for(min=2, max=2, value=2)

        call(up, protocol, "counter")
        call(down, protocol, "counter")

        assert protocol.sent[-1]["error"]["code"] == "component_not_decrementable"
        assert protocol.sent[-2]["error"]["code"] == "component_not_incrementable"


class TestOnlyCountersCount:
    """Rien d'autre n'a de valeur a monter ou descendre."""

    @pytest.mark.parametrize("component_id", ["token", "board"])
    @pytest.mark.parametrize("action", ["increment", "decrement"])
    def test_another_component_refuses_to_count(self, action, component_id):
        up, down, protocol, _, _ = handler_for()
        handler = up if action == "increment" else down

        call(handler, protocol, component_id)

        assert protocol.sent[-1]["error"]["code"] == f"component_not_{action}able"

    def test_an_ordinary_component_cannot_count(self):
        """
        Le drapeau est sur la classe de base : un composant qui l'oublierait ne
        ferait pas bouger sa valeur, meme sur un message bricole.
        """
        token = build_session().get_component("token")

        assert token.incrementable() is False
        assert token.decrementable() is False


class TestOnlyPlayersCount:
    def test_a_watcher_cannot_count(self):
        up, _, protocol, _, session = handler_for(role="watcher")

        call(up, protocol, "counter")

        assert protocol.sent[-1]["error"]["code"] == "watcher_not_allowed"
        assert session.get_component("counter").value == 3
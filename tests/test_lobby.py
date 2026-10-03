import json
import logging

import pytest
from twisted.internet import defer

from chabanas import Chabanas
from lobby import Lobby
from session import Session
from user import User

LOGGER = logging.getLogger("tests")


class FakeProtocol:
    """Stands in for an autobahn connection."""

    def __init__(self, raise_on_send=False):
        self.sent = []
        self.closed = False
        self._raise_on_send = raise_on_send

    def sendMessage(self, payload, isBinary=False):
        if self._raise_on_send:
            raise Exception("connection already closed")
        self.sent.append(payload)

    def is_connection_open(self):
        return not self.closed

    def sendClose(self):
        self.closed = True

    @property
    def events(self):
        return [msg["event"] for msg in self.sent if isinstance(msg, dict) and "event" in msg]


class FakeChabanas(Chabanas):
    """Back-end stub. Every method returns an already-fired Deferred."""

    def __init__(self, info):
        super().__init__(LOGGER)
        self.info = info
        self.available = True
        self.calls = []
        self.payloads = []
        self._agent = object()  # prevents building a real Agent

    # statut renvoye pour /session/join, surcharge par les tests de refus
    join_status = 200
    # reponse de /session/join quand join_status n'est pas un succes
    join_body = b"Unauthorized"

    def _post_json(self, path, payload, with_status=False):
        self.calls.append(path)
        self.payloads.append((path, payload))
        if not self.available:
            return self._fired(False, with_status)
        if path == "/session/create":
            return self._fired({"session_code": self.info["code"]}, with_status)
        if path in ("/session/get", "/session/join"):
            if path == "/session/join" and self.join_status != 200:
                return defer.succeed((self.join_status, False))
            return self._fired({"session": self.info}, with_status)
        if path == "/session/list":
            return self._fired({"sessions": {}}, with_status)
        if path == "/game/list":
            return self._fired({"game_list": [{"name": self.info["name"]}]}, with_status)
        return self._fired({}, with_status)

    @staticmethod
    def _fired(value, with_status):
        if with_status:
            value = (200, value)
        return defer.succeed(value)


@pytest.fixture
def lobby(session_info):
    return Lobby(LOGGER, FakeChabanas(session_info))


def connect(lobby, name):
    protocol = FakeProtocol()
    user = User(name=name, protocol=protocol)
    lobby.add_user(user)
    return user


class TestCreateSession:
    def test_creates_and_registers_the_session(self, lobby, sync):
        user = connect(lobby, "alice")

        success, error = sync(lobby.create_session("Waterloo", user, "key"))

        assert success is True
        assert error is None
        assert user.session is not None
        assert lobby.sessions[user.session.key] is user.session
        assert [u.name for u in user.session.players] == ["alice"]

    def test_components_keep_their_ids(self, lobby, sync):
        """Regression guard for bugs 1 and 2, through the full session build."""
        user = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", user, "key"))

        described = user.session.return_session_json()
        assert described["components"]["fixed"][0]["id"] == "b1"
        assert described["components"]["movable"][0]["id"] == "t1"

    def test_back_end_failure_is_reported_without_creating_a_session(self, lobby, sync):
        lobby.chabanas.available = False
        user = connect(lobby, "alice")

        success, error = sync(lobby.create_session("Waterloo", user, "key"))

        assert success is False
        assert error == "Unable to create session"
        assert user.session is None
        assert lobby.sessions == {}


class TestJoinSession:
    def test_finds_a_session_already_in_the_lobby(self, lobby, sync, session_info):
        """
        Bug 3: join_session iterated self.sessions, which is a dict keyed by
               session.key, so it iterated str keys and raised
               AttributeError: 'str' object has no attribute 'code'.
        """
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")

        success, error = sync(lobby.join_session(session_info["code"], guest, "", "player"))

        assert success is True
        assert error is None
        assert guest.session is host.session, "la session du lobby doit etre reutilisee"
        assert lobby.chabanas.calls == ["/session/create", "/session/get", "/session/join"], (
            "Chabanas doit toujours valider l'adhesion, meme pour une session en cache"
        )

    def test_queries_the_back_end_for_an_unknown_code(self, lobby, sync):
        user = connect(lobby, "alice")
        success, _ = sync(lobby.join_session("UNKNOWN", user, "", "player"))
        assert success is True
        assert "/session/join" in lobby.chabanas.calls

    def test_back_end_failure_is_reported(self, lobby, sync):
        # code absent du lobby : le back-end est sollicite et echoue
        lobby.chabanas.available = False
        guest = connect(lobby, "bob")

        success, error = sync(lobby.join_session("UNKNOWN", guest, "", "player"))

        assert success is False
        assert error == "Unable to join session"
        assert guest.session is None

    def test_a_cached_session_still_asks_the_back_end(self, lobby, sync, session_info):
        """
        Regression: joining a code already loaded here used to skip Chabanas
        entirely, so anyone guessing the code walked in, with any key and
        without the access_key. Chabanas is the only holder of those secrets, so
        it must be consulted even for a cached session.
        """
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.chabanas.calls.clear()
        lobby.chabanas.available = False
        guest = connect(lobby, "bob")

        success, error = sync(lobby.join_session(session_info["code"], guest, "", "player"))

        assert success is False
        assert "/session/join" in lobby.chabanas.calls

    def test_wrong_access_key_is_refused_for_a_cached_session(self, lobby, sync, session_info):
        """The access_key of a cached session must be verified, not ignored."""
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.chabanas.join_status = 401
        intruder = connect(lobby, "mallory")

        success, error = sync(
            lobby.join_session(session_info["code"], intruder, "k", "player", "MAUVAISE")
        )

        assert success is False
        assert error == "access_key_incorrect"
        assert intruder.session is None
        assert [u.name for u in host.session.players] == ["alice"], (
            "un intrus ne doit pas etre ajoute a la session en cache"
        )

    def test_watchers_not_allowed_is_refused_for_a_cached_session(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.chabanas.join_status = 403
        watcher = connect(lobby, "watcher")

        success, error = sync(
            lobby.join_session(session_info["code"], watcher, "k", "watcher")
        )

        assert success is False
        assert error == "watchers_not_allowed"
        assert watcher.session is None

    def test_cached_session_keeps_its_state_when_validated(self, lobby, sync, session_info):
        """
        Chabanas now answers every join, but its game_json is the stored one: it
        must not overwrite the board state held in memory here.
        """
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        # le plateau de test est trop petit pour qu'un coupage change les
        # coordonnees : on pose directement un etat vivant different de celui
        # du game_json renvoie par Chabanas
        token = host.session.components_lists["movable"][0]
        token.x, token.y = 7, 9
        moved = (token.x, token.y)
        guest = connect(lobby, "bob")

        success, _ = sync(lobby.join_session(session_info["code"], guest, "", "player"))

        assert success is True
        assert guest.session is host.session
        assert (token.x, token.y) == moved, (
            "l'etat vivant de la session ne doit pas etre remplace par le game_json de Chabanas"
        )


class TestCapacityAndRoles:
    """Bug 4: the return value of add_user was discarded, so a client was told
    it had joined a full session, and the `role` field was ignored."""

    @pytest.fixture
    def full_lobby(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        return lobby, host, session_info

    def test_max_players_is_enforced(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        second = connect(lobby, "bob")
        assert sync(lobby.join_session(session_info["code"], second, "", "player"))[0] is True

        third = connect(lobby, "carol")
        success, error = sync(lobby.join_session(session_info["code"], third, "", "player"))

        assert success is False
        assert error == "Session is full (2 players)"
        assert third.session is None, "un utilisateur refuse ne doit pas avoir de session"
        assert len(host.session.players) == 2

    def test_watchers_are_accepted_up_to_max_watchers(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        watcher = connect(lobby, "watcher")

        success, error = sync(lobby.join_session(session_info["code"], watcher, "", "watcher"))

        assert success is True
        assert error is None
        assert [u.name for u in host.session.watchers] == ["watcher"]
        assert host.session.return_session_json()["watchers"] == "1/1"

    def test_max_watchers_is_enforced(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        sync(lobby.join_session(session_info["code"], connect(lobby, "w1"), "", "watcher"))
        second = connect(lobby, "w2")

        success, error = sync(lobby.join_session(session_info["code"], second, "", "watcher"))

        assert success is False
        assert error == "watchers_full"
        assert second.session is None, "un spectateur refuse ne doit pas avoir de session"
        assert [u.name for u in host.session.watchers] == ["w1"]

    def test_watchers_do_not_consume_player_seats(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        sync(lobby.join_session(session_info["code"], connect(lobby, "w1"), "", "watcher"))

        player = connect(lobby, "bob")
        success, _ = sync(lobby.join_session(session_info["code"], player, "", "player"))

        assert success is True, "un watcher ne doit pas occuper un siege de joueur"

    def test_unknown_role_is_refused(self, full_lobby, sync, session_info):
        lobby, _, _ = full_lobby
        intruder = connect(lobby, "mallory")

        success, error = sync(lobby.join_session(session_info["code"], intruder, "", "admin"))

        assert success is False
        assert error == "Invalid role 'admin'"

    def test_joining_twice_is_refused(self, full_lobby, sync, session_info):
        lobby, host, _ = full_lobby
        success, error = sync(lobby.join_session(session_info["code"], host, "", "player"))

        assert success is False
        assert error == "User is already in this session"


class TestSessionBasics:
    def test_add_user_returns_a_reason(self):
        session = Session(None, "n", "k", "c", True, "std", {
            "game": {"max_players": 1, "max_watchers": 1},
            "fixed": [], "movable": [],
        })
        first, second = User(name="a", protocol=None), User(name="b", protocol=None)

        assert session.add_user(first, "player") == (True, None)
        ok, reason = session.add_user(second, "player")
        assert ok is False and "full" in reason

    def test_remove_user_works_for_players_and_watchers(self):
        session = Session(None, "n", "k", "c", True, "std", {
            "game": {"max_players": 2, "max_watchers": 2},
            "fixed": [], "movable": [],
        })
        player = User(name="a", protocol=None)
        watcher = User(name="w", protocol=None)
        session.add_user(player, "player")
        session.add_user(watcher, "watcher")

        session.remove_user(player)
        session.remove_user(watcher)

        assert session.players == []
        assert session.watchers == []


class TestKeepAlive:
    """
    Bug 6: send_keep_alive deleted users while iterating self.users.values(),
           raising RuntimeError: dictionary changed size during iteration and
           stopping the reactor.
    """

    def test_keeps_sending_to_live_users(self, lobby):
        alive = connect(lobby, "alive")

        lobby.send_keep_alive()

        assert len(alive.protocol.sent) == 1
        assert b"keep_alive" in alive.protocol.sent[0]

    def test_removes_dead_users_without_raising(self, lobby):
        alive = connect(lobby, "alive")
        dead = connect(lobby, "dead")
        dead.protocol._raise_on_send = True

        lobby.send_keep_alive()  # must not raise

        assert dead.protocol not in lobby.users
        assert alive.protocol in lobby.users
        assert len(alive.protocol.sent) == 1, "les autres clients recoivent le message"

    def test_handles_several_dead_users(self, lobby):
        alive = connect(lobby, "alive")
        for index in range(5):
            zombie = connect(lobby, f"zombie{index}")
            zombie.protocol._raise_on_send = True

        lobby.send_keep_alive()

        assert len(lobby.users) == 1
        assert alive.protocol in lobby.users


class TestDeleteUser:
    """Bug 7: shutdown() clears the user registry before the close handshakes
    finish, so onClose() then called delete_user(None)."""

    def test_delete_user_accepts_none(self, lobby):
        lobby.delete_user(None)  # must not raise

    def test_delete_user_removes_from_session_and_registry(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")
        sync(lobby.join_session(session_info["code"], guest, "", "player"))

        lobby.delete_user(guest)

        assert guest.protocol not in lobby.users
        assert [u.name for u in host.session.players] == ["alice"]

    def test_delete_user_twice_is_harmless(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))

        lobby.delete_user(host)
        lobby.delete_user(host)


class TestStoreStateWhenEmpty:
    """When the last user leaves a session, its current situation must be stored
    in the session game_json so the game can be resumed later on."""

    def updates(self, lobby):
        return [p for p, _ in lobby.chabanas.payloads if p == "/session/update"]

    def test_state_is_stored_when_the_last_player_leaves(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")
        sync(lobby.join_session("CODE1", guest, "", "player"))

        lobby.delete_user(host)
        assert self.updates(lobby) == [], "someone is still playing"

        lobby.delete_user(guest)
        assert self.updates(lobby) == ["/session/update"]

    def test_a_watcher_keeps_the_session_alive(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        watcher = connect(lobby, "carol")
        sync(lobby.join_session("CODE1", watcher, "", "watcher"))

        lobby.delete_user(host)
        assert self.updates(lobby) == [], "a spectator is still watching"

        lobby.delete_user(watcher)
        assert self.updates(lobby) == ["/session/update"]

    def test_stored_state_holds_the_current_position(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        token = host.session.components_dict["t1"]
        token.acquire(host)
        token.place(400, 500, host)

        lobby.delete_user(host)

        _, payload = lobby.chabanas.payloads[-1]
        stored = {c["id"]: c for c in payload["game_json"]["movable"]}
        assert payload["key"] == host.session.key
        assert stored["t1"]["x"] == 400 and stored["t1"]["y"] == 500
        assert stored["t1"]["border"] is False

    def test_state_is_not_stored_twice_while_nobody_comes_back(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))

        lobby.delete_user(host)
        lobby.delete_user(host)

        assert len(self.updates(lobby)) == 1

    def test_state_is_stored_again_after_somebody_came_back(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.delete_user(host)

        back = connect(lobby, "alice")
        assert sync(lobby.join_session("CODE1", back, "", "player", "")) == (True, None)
        lobby.delete_user(back)

        assert len(self.updates(lobby)) == 2

    def test_the_session_stays_in_the_lobby(self, lobby, sync):
        """Storing the state must not make the session disappear: players
        coming back have to find it."""
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))

        lobby.delete_user(host)

        assert host.session.key not in lobby.sessions

    def test_a_back_end_failure_does_not_break_disconnection(self, lobby, sync):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        lobby.chabanas.available = False

        lobby.delete_user(host)  # must not raise

        assert host.protocol not in lobby.users


class TestSessionStateReload:
    """game_json_state() must produce a game_json that load_session_components()
    reads back identically, otherwise a stored session cannot be resumed."""

    def build(self, game_json):
        return Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=game_json,
        )

    def test_a_stored_session_reloads_with_the_current_position(self, session_info):
        first = self.build(session_info["game_json"])
        token = first.components_dict["t1"]
        user = User(name="alice", protocol=None)
        token.acquire(user)
        token.place(400, 500, user)
        assert (token.x, token.y) == (400, 500)

        again = self.build(first.game_json_state())

        reloaded = again.components_dict["t1"]
        assert (reloaded.x, reloaded.y) == (400, 500)
        assert reloaded.border is False

    def test_the_initial_position_survives_a_reload(self, session_info):
        """A moved token must keep the board spot as its initial position,
        otherwise 'drop it back where it started' would drift on every reload."""
        first = self.build(session_info["game_json"])
        token = first.components_dict["t1"]
        user = User(name="alice", protocol=None)
        token.acquire(user)
        assert token.place(400, 500, user) is True, "le jeton doit avoir bouge"
        assert (token.x, token.y) == (400, 500)

        again = self.build(first.game_json_state())
        reloaded = again.components_dict["t1"]

        assert (reloaded.initial_x, reloaded.initial_y) == (1, 2)
        assert reloaded.near_initial_position(1, 2) is True
        assert reloaded.near_initial_position(400, 500) is False

    def test_a_token_dropped_back_before_reloading_keeps_its_border(self, session_info):
        first = self.build(session_info["game_json"])
        token = first.components_dict["t1"]
        user = User(name="alice", protocol=None)
        token.acquire(user)
        assert token.place(400, 500, user) is True
        assert token.border is False
        assert token.place(1, 2, user) is True
        assert token.border is True

        again = self.build(first.game_json_state())

        assert again.components_dict["t1"].border is True

    def test_a_frozen_token_never_gets_a_border_from_a_reload(self, session_info):
        info = session_info["game_json"]
        info["movable"][0]["move_border"] = False
        info["movable"][0]["border"] = True

        reloaded = self.build(self.build(info).game_json_state())

        assert reloaded.components_dict["t1"].border is False

    def test_the_game_limits_are_preserved(self, session_info):
        """Session.__init__ reads game_json['game'], so a stored state that lost
        it would make the session impossible to rebuild."""
        state = self.build(session_info["game_json"]).game_json_state()

        assert state["game"] == {"max_players": 2, "max_watchers": 1}

    def test_the_source_game_json_is_left_untouched(self, session_info):
        session = self.build(session_info["game_json"])
        token = session.components_dict["t1"]
        user = User(name="alice", protocol=None)
        token.acquire(user)
        assert token.place(400, 500, user) is True

        session.game_json_state()

        assert session_info["game_json"]["movable"][0]["x"] == 1


class TestFixPositionsMovesEveryStart:
    """"fixe la position" makes the current spot the new start of every token."""

    def build(self, game_json):
        return Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=game_json,
        )

    def two_tokens(self, session_info):
        info = session_info["game_json"]
        info["movable"].append({
            "kind": "token", "id": "t2", "x": 50, "y": 60,
            "front_src": "front.png", "back_src": "back.png",
            "width": 32, "height": 32,
        })
        return self.build(info)

    def test_every_token_gets_the_new_start(self, session_info):
        session = self.two_tokens(session_info)
        first = session.components_dict["t1"]
        second = session.components_dict["t2"]
        user = User(name="alice", protocol=None)
        first.acquire(user)
        second.acquire(user)
        assert first.place(400, 500, user) is True
        assert second.place(410, 510, user) is True

        session.fix_positions()

        assert (first.initial_x, first.initial_y) == (400, 500)
        assert (second.initial_x, second.initial_y) == (410, 510)
        assert first.border is True
        assert second.border is True
        assert "t2" in session.components_dict, "les deux jetons sont bien présents"

    def test_the_stored_state_carries_the_new_start(self, session_info):
        """The new start must be persisted, otherwise a reload would restore
        the old case de depart."""
        session = self.build(session_info["game_json"])
        first = session.components_dict["t1"]
        user = User(name="alice", protocol=None)
        first.acquire(user)
        first.place(400, 500, user)

        session.fix_positions()

        stored = {c["id"]: c for c in session.game_json_state()["movable"]}
        assert (stored["t1"]["initial_x"], stored["t1"]["initial_y"]) == (400, 500)
        assert stored["t1"]["border"] is True

    def test_the_returned_components_describe_the_new_start(self, session_info):
        session = self.build(session_info["game_json"])
        first = session.components_dict["t1"]
        user = User(name="alice", protocol=None)
        first.acquire(user)
        first.place(400, 500, user)

        returned = session.fix_positions()

        assert returned[0]["initial_x"] == 400
        assert returned[0]["initial_y"] == 500
        assert returned[0]["border"] is True


class TestSavedPositionsAreIntegers:
    """The client divides by the zoom, so positions are floats. Only the saved
    state is rounded: the board keeps sub-pixel precision while dragging."""

    def build(self, game_json):
        return Session(
            user=None, name="Waterloo", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=game_json,
        )

    def move_to(self, session, x, y):
        # prendre puis poser, puis relâcher : le jeton doit être libre avant
        # le déplacement suivant
        token = session.components_dict["t1"]
        user = User(name="alice", protocol=None)
        assert token.acquire(user) is not False
        assert token.place(x, y, user) is True
        assert token.release(user) is True
        return token

    def movable(self, state):
        return {c["id"]: c for c in state["movable"]}

    def test_a_float_position_is_saved_as_an_integer(self, session_info):
        session = self.build(session_info["game_json"])
        self.move_to(session, 561.8153874060956, 586.5681792971355)

        saved = self.movable(session.game_json_state())["t1"]

        assert saved["x"] == 562
        assert saved["y"] == 587
        assert isinstance(saved["x"], int)
        assert isinstance(saved["y"], int)

    def test_the_rounding_is_to_the_nearest_pixel(self, session_info):
        session = self.build(session_info["game_json"])
        self.move_to(session, 561.81, 586.19)

        saved = self.movable(session.game_json_state())["t1"]

        assert saved["x"] == 562
        assert saved["y"] == 586

    def test_an_untouched_token_stays_an_integer(self, session_info):
        session = self.build(session_info["game_json"])

        saved = self.movable(session.game_json_state())["t1"]

        assert (saved["x"], saved["y"]) == (1, 2)
        assert isinstance(saved["initial_x"], int)

    def test_the_start_position_is_rounded_too(self, session_info):
        """fix_position copies a float position into initial_x/initial_y."""
        session = self.build(session_info["game_json"])
        self.move_to(session, 400.6, 500.2)
        session.components_dict["t1"].fix_position()

        saved = self.movable(session.game_json_state())["t1"]

        assert (saved["initial_x"], saved["initial_y"]) == (401, 500)
        assert isinstance(saved["initial_x"], int)
        assert isinstance(saved["initial_y"], int)

    def test_the_position_in_memory_stays_a_float(self, session_info):
        """Rounding at save time only: the drag must not jump a pixel under the
        cursor, so the live value keeps its sub-pixel precision."""
        token = self.move_to(self.build(session_info["game_json"]), 561.8, 586.5)

        assert token.x == 561.8
        assert token.y == 586.5

    def test_the_live_broadcast_is_not_rounded(self, session_info):
        session = self.build(session_info["game_json"])
        self.move_to(session, 561.8, 586.5)

        live = session.components_dict["t1"].return_json()

        assert live["x"] == 561.8
        assert live["y"] == 586.5

    def test_a_saved_state_can_be_reloaded_and_saved_again_unchanged(self, session_info):
        session = self.build(session_info["game_json"])
        self.move_to(session, 561.8, 586.5)

        first = session.game_json_state()
        second = self.build(first).game_json_state()

        assert self.movable(first)["t1"]["x"] == self.movable(second)["t1"]["x"] == 562

    def test_the_source_game_json_keeps_its_own_values(self, session_info):
        """Rounding must not write back into the game's definitions."""
        info = session_info["game_json"]
        session = self.build(info)
        self.move_to(session, 561.8, 586.5)

        session.game_json_state()

        assert info["movable"][0]["x"] == 1
        assert info["movable"][0]["y"] == 2

    def test_every_saved_position_is_an_integer(self, session_info):
        info = session_info["game_json"]
        info["movable"].append({
            "kind": "token", "id": "t2", "x": 50.4, "y": 60.6,
            "front_src": "front.png", "back_src": "back.png",
            "width": 32, "height": 32,
        })
        session = self.build(info)
        self.move_to(session, 561.8, 586.5)
        session.components_dict["t2"].fix_position()

        for component in session.game_json_state()["movable"]:
            for key in ("x", "y", "initial_x", "initial_y"):
                assert isinstance(component[key], int), f"{component['id']}.{key}"

    def test_a_token_dropped_back_on_its_spot_stays_exact(self, session_info):
        """Rounding must not shift the snap: a token put back on its case de
        depart is stored on the very same integers."""
        session = self.build(session_info["game_json"])
        token = self.move_to(session, 400, 500)
        session.fix_positions()
        self.move_to(session, 401, 500)

        saved = self.movable(session.game_json_state())["t1"]

        assert (saved["x"], saved["y"], saved["initial_x"], saved["initial_y"]) \
            == (400, 500, 400, 500)


class TestLobbyNotifications:
    """A lobby client must learn about arrivals and departures without
    polling, so it can hide a Join button once a session is full."""

    def test_join_is_broadcast_to_every_connected_user(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        watcher = connect(lobby, "eve")

        sync(lobby.join_session(session_info["code"], connect(lobby, "bob"), "", "player"))

        assert json.loads(watcher.protocol.sent[-1])["event"] == "session_players_changed"
        assert json.loads(host.protocol.sent[-1])["event"] == "session_players_changed"

    def test_broadcast_reports_the_seat_count(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        eve = connect(lobby, "eve")

        sync(lobby.join_session(session_info["code"], connect(lobby, "bob"), "", "player"))

        payload = json.loads(eve.protocol.sent[-1])
        assert payload["event"] == "session_players_changed"
        assert payload["action"] == "join"
        assert payload["nickname"] == "bob"
        assert payload["code"] == session_info["code"]
        assert payload["players"] == 2
        assert payload["max_players"] == host.session.max_players

    def test_leave_is_broadcast(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        guest = connect(lobby, "bob")
        sync(lobby.join_session(session_info["code"], guest, "", "player"))
        eve = connect(lobby, "eve")
        eve.protocol.sent.clear()

        lobby.delete_user(guest)

        payload = json.loads(eve.protocol.sent[-1])
        assert payload["action"] == "leave"
        assert payload["nickname"] == "bob"
        assert payload["players"] == 1

    def test_no_broadcast_when_the_join_is_refused(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", host, ""))
        eve = connect(lobby, "eve")
        eve.protocol.sent.clear()

        success, _ = sync(lobby.join_session(session_info["code"], host, "", "player"))

        assert success is False
        assert eve.protocol.sent == []


class TestShutdown:
    def test_clears_registry_and_notifies_everyone(self, lobby, sync):
        first = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", first, ""))
        second = connect(lobby, "bob")

        lobby.shutdown()

        assert lobby.users == {}
        assert lobby.sessions == {}
        assert b"server_shutdown" in first.protocol.sent[0]
        assert b"server_shutdown" in second.protocol.sent[0]
        assert first.protocol.closed and second.protocol.closed

    def test_keep_alive_after_shutdown_is_harmless(self, lobby, sync):
        first = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", first, ""))
        lobby.shutdown()

        lobby.send_keep_alive()  # must not raise

"""
Tests of the optional nationalities of a game: the options of the game_json declare a list,
each player chooses one when taking a seat, and tokens may belong to one.
Chabanas enforces the choice and stores it on the seat; Tourtoirac carries it
to the clients. No rule depends on it yet.
"""
import json

from handle_message import acquire, pick
from session import Session, read_nationalities
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes
from tests.test_lobby import FakeChabanas, LOGGER, connect
from lobby import Lobby

TOKEN = {"kind": "token", "id": "army", "x": 0, "y": 0, "width": 40, "height": 40,
         "front_src": "/army.png", "back_src": None}


def build_session(game_json=None, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "options": {"nationalities": ["US", "NVA"]},
        "fixed": [],
        "movable": [dict(TOKEN, nationality="US"), dict(TOKEN, id="neutral")],
        **(game_json or {}),
    }
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, **extra,
    )


def seated(session_info, **nationalities):
    session_info["game_json"]["options"] = {"nationalities": ["US", "NVA"]}
    session_info["players"] = [
        {"nickname": nickname, "owner": index == 0, "nationality": nationality}
        for index, (nickname, nationality) in enumerate(nationalities.items())
    ]
    return session_info


class TestReading:
    def test_the_nationalities_of_the_game_are_read_in_order(self):
        assert build_session().nationalities == ["US", "NVA"]

    def test_a_game_without_nationalities_has_none(self):
        assert read_nationalities({"fixed": []}) == []
        assert read_nationalities(None) == []

    def test_a_badly_written_list_is_cleaned(self):
        options = {"nationalities": ["US", "", 3, "US", None, "NVA"]}
        assert read_nationalities({"options": options}) == ["US", "NVA"]
        assert read_nationalities({"options": {"nationalities": "US"}}) == []
        assert read_nationalities({"options": "US"}) == []

    def test_the_key_is_only_read_in_the_options(self):
        assert read_nationalities({"nationalities": ["US", "NVA"]}) == []

    def test_the_session_json_carries_the_nationalities_and_the_seats(self):
        session = build_session(player_nationalities={"alice": "US"})
        session_json = session.return_session_json()
        assert session_json["nationalities"] == ["US", "NVA"]
        assert session_json["player_nationalities"] == {"alice": "US"}

    def test_the_saved_state_keeps_the_nationalities(self):
        assert build_session().game_json_state()["options"]["nationalities"] == ["US", "NVA"]


class TestTokens:
    def test_a_token_carries_its_nationality(self):
        session = build_session()
        assert session.get_component("army").nationality == "US"
        assert session.get_component("neutral").nationality is None

    def test_the_nationality_of_a_token_survives_a_save(self):
        state = json.loads(json.dumps(build_session().game_json_state()))
        resumed = build_session(state)
        assert resumed.get_component("army").nationality == "US"
        assert resumed.get_component("army").return_json()["nationality"] == "US"

    def test_a_nationality_that_is_not_a_name_is_ignored(self):
        session = build_session({"movable": [dict(TOKEN, nationality=3)]})
        assert session.get_component("army").nationality is None


class TestSeats:
    def test_the_creator_gets_the_nationality_chabanas_stored(self, sync, session_info):
        lobby = Lobby(LOGGER, FakeChabanas(seated(session_info, alice="US")))
        alice = connect(lobby, "alice")

        assert sync(lobby.create_session("Waterloo", alice, "", nationality="US")) == (True, None)

        path, payload = lobby.chabanas.payloads[0]
        assert (path, payload["nationality"]) == ("/session/create", "US")
        assert alice.nationality == "US"

    def test_a_joining_player_gets_theirs_and_may_share_it(self, sync, session_info):
        lobby = Lobby(LOGGER, FakeChabanas(seated(session_info, alice="US")))
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        # Chabanas now knows the second seat too
        seated(lobby.chabanas.info, alice="US", bob="US")
        bob = connect(lobby, "bob")

        assert sync(lobby.join_session(session_info["code"], bob, "", "player", "", "US")) == (True, None)

        assert lobby.chabanas.payloads[-1][1]["nationality"] == "US"
        assert (alice.nationality, bob.nationality) == ("US", "US")
        assert alice.session.player_nationalities == {"alice": "US", "bob": "US"}

    def test_a_watcher_has_no_nationality(self, sync, session_info):
        lobby = Lobby(LOGGER, FakeChabanas(seated(session_info, alice="US")))
        sync(lobby.create_session("Waterloo", connect(lobby, "alice"), ""))
        watcher = connect(lobby, "alice2")
        sync(lobby.join_session(session_info["code"], watcher, "", "watcher"))
        assert watcher.role == "watcher"
        assert watcher.nationality is None

    def test_a_resumed_player_finds_the_nationality_of_their_seat(self, sync, session_info):
        lobby = Lobby(LOGGER, FakeChabanas(seated(session_info, alice="NVA")))
        sync(lobby.create_session("Waterloo", connect(lobby, "alice"), ""))
        # the game page opens its own connection
        again = connect(lobby, "alice")
        assert sync(lobby.resume_session(session_info["key"], again, "player", session_info["code"])) == (True, None)
        assert again.nationality == "NVA"

    def test_a_join_without_a_valid_nationality_is_refused(self, sync, session_info):
        lobby = Lobby(LOGGER, FakeChabanas(seated(session_info, alice="US")))
        sync(lobby.create_session("Waterloo", connect(lobby, "alice"), ""))
        lobby.chabanas.join_status = 422
        bob = connect(lobby, "bob")

        assert sync(lobby.join_session(session_info["code"], bob, "", "player", "", "France")) == (
            False, "invalid_nationality")
        assert bob.session is None


def player(session, nationality):
    """A connected player of that nationality, None for a player without one."""
    user = FakeUser(session=session)
    user.nationality = nationality
    session.players = [user]
    return FakeProtocol(user), user


class TestAcquireRule:
    """A token that belongs to a nationality is only taken by its players."""

    def test_a_player_takes_a_token_of_their_nationality(self):
        session = build_session(started=True)
        protocol, user = player(session, "US")
        call(acquire, protocol, "army")
        assert error_codes(protocol) == []
        assert session.get_component("army").acquired_by is user

    def test_a_player_does_not_take_a_token_of_another_nationality(self):
        session = build_session(started=True)
        protocol, user = player(session, "NVA")
        call(acquire, protocol, "army")
        assert error_codes(protocol) == ["wrong_nationality"]
        assert session.get_component("army").acquired_by is None
        # nothing is broadcast: the token did not move
        assert user.sent == []

    def test_a_player_without_nationality_does_not_take_it_either(self):
        session = build_session(started=True)
        protocol, _ = player(session, None)
        call(acquire, protocol, "army")
        assert error_codes(protocol) == ["wrong_nationality"]

    def test_a_token_without_nationality_is_taken_by_anybody(self):
        session = build_session(started=True)
        for nationality in ("US", "NVA", None):
            protocol, user = player(session, nationality)
            call(acquire, protocol, "neutral")
            assert error_codes(protocol) == []
            session.get_component("neutral").release(user)


class TestPickRule:
    """A pick only draws among the tokens the player may take."""

    BAG = {"kind": "bag", "id": "bag", "x": 0, "y": 0, "width": 100, "height": 100, "components": [
        dict(TOKEN, id="us-chit", x=None, y=None, nationality="US"),
        dict(TOKEN, id="nva-chit", x=None, y=None, nationality="NVA"),
    ]}

    def test_a_pick_never_draws_a_token_of_another_nationality(self):
        for _ in range(20):
            session = build_session({"fixed": [self.BAG]}, started=True)
            protocol, user = player(session, "NVA")
            call(pick, protocol, "bag")
            assert user.sent[-1]["component_json"]["id"] == "nva-chit"

    def test_a_bag_holding_only_tokens_of_others_gives_nothing(self):
        session = build_session({"fixed": [self.BAG]}, started=True)
        protocol, user = player(session, "NVA")
        call(pick, protocol, "bag")
        call(pick, protocol, "bag")
        assert error_codes(protocol) == ["wrong_nationality"]
        assert [token.id for token in session.get_component("bag").components] == ["us-chit"]
        assert len(user.sent) == 1

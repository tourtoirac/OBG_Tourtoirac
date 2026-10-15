"""
Tests of the optional nationalities of a game: the options of the game_json declare a list,
each player chooses one when taking a seat, and tokens may belong to one.
Chabanas enforces the choice and stores it on the seat; Tourtoirac carries it
to the clients. No rule depends on it yet.
"""
import json

from handle_message import acquire, apply_setup, flip, pick, release, rotate
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

    def test_a_token_carries_block_sight_to_the_clients(self):
        session = build_session({"movable": [dict(TOKEN, nationality="US", block_sight=True)]})
        assert session.get_component("army").return_json()["block_sight"] is True

    def test_block_sight_is_off_unless_set_to_true(self):
        session = build_session({"movable": [TOKEN, dict(TOKEN, id="other", block_sight="true")]})
        assert session.get_component("army").block_sight is False
        assert session.get_component("other").block_sight is False

    def test_block_sight_survives_a_save(self):
        session = build_session({"movable": [dict(TOKEN, block_sight=True)]})
        state = json.loads(json.dumps(session.game_json_state()))
        assert build_session(state).get_component("army").block_sight is True


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


class TestModificationRules:
    """Whatever forbids taking a token forbids turning and flipping it too."""

    FLIPPABLE = dict(TOKEN, id="unit", nationality="US", back_src="/back.png", orientable=True)

    def session(self, **extra):
        return build_session({"movable": [self.FLIPPABLE]}, started=True, **extra)

    def test_a_player_of_the_nationality_turns_and_flips_the_token(self):
        session = self.session()
        protocol, user = player(session, "US")
        call(rotate, protocol, "unit", direction="right")
        call(flip, protocol, "unit")
        assert error_codes(protocol) == []
        unit = session.get_component("unit")
        assert (unit.orientation, unit.side) == (45, "back")
        assert [message["event"] for message in user.sent] == ["rotate", "flip"]

    def test_a_player_of_another_nationality_does_neither(self):
        session = self.session()
        protocol, user = player(session, "NVA")
        call(rotate, protocol, "unit", direction="right")
        call(flip, protocol, "unit")
        assert error_codes(protocol) == ["wrong_nationality", "wrong_nationality"]
        unit = session.get_component("unit")
        assert (unit.orientation, unit.side) == (0, "front")
        assert user.sent == []

    def test_nothing_is_turned_or_flipped_before_the_game_starts(self):
        session = self.session()
        session.started = False
        protocol, _ = player(session, "US")
        call(rotate, protocol, "unit", direction="right")
        call(flip, protocol, "unit")
        assert error_codes(protocol) == ["session_not_started", "session_not_started"]
        unit = session.get_component("unit")
        assert (unit.orientation, unit.side) == (0, "front")

    def test_nothing_is_turned_or_flipped_while_a_player_is_away(self):
        session = self.session(player_names=["alice", "bob"])
        protocol, _ = player(session, "US")
        call(flip, protocol, "unit")
        assert error_codes(protocol) == ["players_missing"]

    def test_a_token_inside_a_bag_is_neither_turned_nor_flipped(self):
        bag = {"kind": "bag", "id": "bag", "x": 0, "y": 0, "width": 100, "height": 100,
               "components": [dict(self.FLIPPABLE, x=None, y=None)]}
        session = build_session({"fixed": [bag], "movable": []}, started=True)
        protocol, _ = player(session, "US")
        call(rotate, protocol, "unit", direction="right")
        call(flip, protocol, "unit")
        assert error_codes(protocol) == ["component_in_bag", "component_in_bag"]


class TestHiddenFaces:
    """
    The face a token of a nationality does not show never leaves the server
    for anybody but the players of that nationality.
    """

    UNIT = dict(TOKEN, id="unit", nationality="US", front_src="/secret.png", back_src="/back.png", side="back")
    NEUTRAL = dict(TOKEN, id="neutral", front_src="/open.png", back_src="/open-back.png", side="back")

    def table(self, *movable, **game):
        """A session with a US player, an NVA player, one without nationality and a watcher."""
        session = build_session(
            {"movable": list(movable or [self.UNIT, self.NEUTRAL]), **game}, started=True)
        users = {}
        for name, nationality in (("us", "US"), ("nva", "NVA"), ("none", None)):
            users[name] = FakeUser(name=name, session=session)
            users[name].nationality = nationality
        users["watcher"] = FakeUser(name="watcher", role="watcher", session=session)
        # a watcher never reads a hidden face, whatever it claims to be
        users["watcher"].nationality = "US"
        session.players = [users["us"], users["nva"], users["none"]]
        session.watchers = [users["watcher"]]
        return session, users

    @staticmethod
    def token_json(session, user, component_id="unit"):
        movable = session.return_session_json(user)["components"]["movable"]
        return next(token for token in movable if token["id"] == component_id)

    def test_only_the_players_of_the_nationality_read_the_hidden_front(self):
        session, users = self.table()
        assert self.token_json(session, users["us"])["front_src"] == "/secret.png"
        for name in ("nva", "none", "watcher"):
            token = self.token_json(session, users[name])
            assert token["front_src"] is None, name
            # the face shown on the table is known to everybody
            assert token["back_src"] == "/back.png"
            assert token["side"] == "back"

    def test_the_hidden_back_of_a_token_showing_its_front_is_kept_back_too(self):
        session, users = self.table(dict(self.UNIT, side="front"))
        assert self.token_json(session, users["us"])["back_src"] == "/back.png"
        token = self.token_json(session, users["nva"])
        assert (token["front_src"], token["back_src"]) == ("/secret.png", None)

    def test_a_token_without_nationality_hides_nothing(self):
        session, users = self.table()
        for user in users.values():
            assert self.token_json(session, user, "neutral")["front_src"] == "/open.png"

    def test_the_stored_state_keeps_both_faces(self):
        session, _ = self.table()
        stored = next(token for token in session.game_json_state()["movable"] if token["id"] == "unit")
        assert (stored["front_src"], stored["back_src"]) == ("/secret.png", "/back.png")
        # and reading the session for a user did not damage the token itself
        assert session.get_component("unit").image_src["front"] == "/secret.png"

    def test_a_flip_shows_the_new_face_and_keeps_back_the_other(self):
        session, users = self.table()
        call(flip, FakeProtocol(users["us"]), "unit")
        own, other, watcher = (users[name].sent[-1] for name in ("us", "nva", "watcher"))
        assert (own["side"], own["front_src"], own["back_src"]) == ("front", "/secret.png", "/back.png")
        for event in (other, watcher):
            assert (event["side"], event["front_src"], event["back_src"]) == ("front", "/secret.png", None)

        call(flip, FakeProtocol(users["us"]), "unit")
        other = users["nva"].sent[-1]
        assert (other["side"], other["front_src"], other["back_src"]) == ("back", None, "/back.png")

    def test_moving_and_releasing_a_token_does_not_leak_its_front(self):
        session, users = self.table()
        protocol = FakeProtocol(users["us"])
        call(acquire, protocol, "unit")
        call(release, protocol, "unit", x=300, y=300)
        release_event = users["nva"].sent[-1]
        assert release_event["event"] == "release"
        assert release_event["component_json"]["front_src"] is None
        assert users["us"].sent[-1]["component_json"]["front_src"] == "/secret.png"
        assert "/secret.png" not in json.dumps(users["nva"].sent + users["watcher"].sent + users["none"].sent)

    def test_fix_positions_does_not_leak_it_either(self):
        session, users = self.table()
        session.send({"event": "fix_positions", "components": session.fix_positions()})
        assert "/secret.png" not in json.dumps(users["nva"].sent)
        assert "/secret.png" in json.dumps(users["us"].sent)

    def test_a_token_picked_from_a_bag_keeps_its_secret(self):
        bag = {"kind": "bag", "id": "bag", "x": 0, "y": 0, "width": 100, "height": 100,
               "components": [dict(self.UNIT, x=None, y=None)]}
        session, users = self.table(self.NEUTRAL, fixed=[bag])
        # inside the bag already, the description hides it
        inside = session.return_session_json(users["nva"])["components"]["fixed"][0]["components"][0]
        assert inside["front_src"] is None
        call(pick, FakeProtocol(users["us"]), "bag")
        assert users["us"].sent[-1]["component_json"]["front_src"] == "/secret.png"
        assert users["nva"].sent[-1]["component_json"]["front_src"] is None
        assert users["watcher"].sent[-1]["component_json"]["front_src"] is None

    def test_a_face_the_setup_is_about_to_hide_is_kept_back_from_the_start(self):
        unit = dict(self.UNIT, side="front")
        session, users = self.table(unit, setup=[{"component_id": "unit", "side": "back"}])
        # before the setup: still described on its front, which the setup will hide
        before = self.token_json(session, users["nva"])
        assert (before["front_src"], before["back_src"]) == (None, "/back.png")
        assert self.token_json(session, users["us"])["front_src"] == "/secret.png"

        call(apply_setup, FakeProtocol(users["us"]), "unit")
        event = users["nva"].sent[-1]["components"][0]
        assert (event["side"], event["front_src"], event["back_src"]) == ("back", None, "/back.png")
        assert "/secret.png" not in json.dumps(users["nva"].sent)

    def test_a_game_without_nationality_tokens_sends_its_messages_untouched(self):
        session, users = self.table(self.NEUTRAL)
        assert session.hides_faces is False
        message = {"event": "x", "component_json": session.get_component("neutral").return_json()}
        assert session.for_user(message, users["nva"]) is message

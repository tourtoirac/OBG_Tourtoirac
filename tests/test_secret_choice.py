"""
Tests of the secret_choice: the players of one nationality choose a value in
secret (the Shadow Points letter of War of the Ring), validate it, then reveal
it. Until then the value never reaches the other players nor the watchers, and
the state survives the saving and the resumption of the session.
"""
import json

from handle_message import choose, reveal_choice, validate_choice
from session import Session
from tests.test_bag import FakeProtocol, FakeUser, call, error_codes

SECRET = {
    "kind": "secret_choice", "id": "shadow_letter", "x": 78, "y": 3683, "width": 63, "height": 63,
    "choices": ["A", "B", "C", "D", "E", "F"], "nationality": "sauron",
}


def build_session(secret=None):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "options": {"nationalities": ["fellowship", "sauron"]},
        "fixed": [SECRET if secret is None else secret],
        "movable": [],
    }
    return Session(
        user=None, name="Ring", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def seated(session):
    """The Dark Power player (sauron), the Fellowship player and a watcher."""
    dark = FakeUser("dark", session=session)
    free = FakeUser("free", session=session)
    watcher = FakeUser("carol", role="watcher", session=session)
    dark.nationality, free.nationality = "sauron", "fellowship"
    session.players = [dark, free]
    session.watchers = [watcher]
    return FakeProtocol(dark), dark, FakeProtocol(free), free, watcher


def last_choice(user):
    return user.sent[-1]["component_json"]["choice"]


class TestReading:
    def test_it_is_a_fixed_component_without_choice(self):
        secret = build_session().get_component("shadow_letter")
        state = secret.return_json()
        assert state["choices"] == ["A", "B", "C", "D", "E", "F"]
        assert (state["choice"], state["validated"], state["revealed"]) == (None, False, False)

    def test_a_saved_state_is_read_back(self):
        secret = build_session(dict(SECRET, choice="C", validated=True)).get_component("shadow_letter")
        assert (secret.choice, secret.validated, secret.revealed) == ("C", True, False)

    def test_an_inconsistent_saved_state_is_cleaned(self):
        secret = build_session(dict(SECRET, choice="Z", validated=True, revealed=True)).get_component("shadow_letter")
        assert (secret.choice, secret.validated, secret.revealed) == (None, False, False)


class TestRound:
    def test_choose_validate_reveal(self):
        session = build_session()
        dark_protocol, dark, _, free, watcher = seated(session)

        call(choose, dark_protocol, "shadow_letter", choice="B")
        assert last_choice(dark) == "B"
        assert last_choice(free) is None and last_choice(watcher) is None

        call(choose, dark_protocol, "shadow_letter", choice="D")
        call(validate_choice, dark_protocol, "shadow_letter")
        assert free.sent[-1]["component_json"]["validated"] is True
        assert last_choice(free) is None

        call(reveal_choice, dark_protocol, "shadow_letter")
        assert last_choice(free) == "D" and last_choice(watcher) == "D"
        assert error_codes(dark_protocol) == []

    def test_a_validated_choice_is_not_changed_before_reveal(self):
        session = build_session()
        dark_protocol, *_ = seated(session)
        call(choose, dark_protocol, "shadow_letter", choice="A")
        call(validate_choice, dark_protocol, "shadow_letter")
        call(choose, dark_protocol, "shadow_letter", choice="B")
        call(validate_choice, dark_protocol, "shadow_letter")
        assert error_codes(dark_protocol) == ["choose_refused", "validate_choice_refused"]
        assert session.get_component("shadow_letter").choice == "A"

    def test_reveal_needs_a_validated_choice(self):
        session = build_session()
        dark_protocol, *_ = seated(session)
        call(validate_choice, dark_protocol, "shadow_letter")
        call(choose, dark_protocol, "shadow_letter", choice="A")
        call(reveal_choice, dark_protocol, "shadow_letter")
        assert error_codes(dark_protocol) == ["validate_choice_refused", "reveal_choice_refused"]

    def test_a_revealed_choice_is_final(self):
        # one component per game turn: the letter of a past turn never changes
        session = build_session()
        dark_protocol, *_ = seated(session)
        call(choose, dark_protocol, "shadow_letter", choice="A")
        call(validate_choice, dark_protocol, "shadow_letter")
        call(reveal_choice, dark_protocol, "shadow_letter")
        call(choose, dark_protocol, "shadow_letter", choice="F")
        call(reveal_choice, dark_protocol, "shadow_letter")
        assert error_codes(dark_protocol) == ["choose_refused", "reveal_choice_refused"]
        assert session.get_component("shadow_letter").choice == "A"

    def test_an_unknown_choice_is_refused(self):
        session = build_session()
        dark_protocol, *_ = seated(session)
        call(choose, dark_protocol, "shadow_letter", choice="G")
        assert error_codes(dark_protocol) == ["choose_refused"]


class TestRights:
    def test_only_the_players_of_its_nationality_play_it(self):
        session = build_session()
        _, _, free_protocol, *_ = seated(session)
        call(choose, free_protocol, "shadow_letter", choice="A")
        assert error_codes(free_protocol) == ["wrong_nationality"]

    def test_a_watcher_does_not_play_it(self):
        session = build_session()
        *_, watcher = seated(session)
        protocol = FakeProtocol(watcher)
        call(choose, protocol, "shadow_letter", choice="A")
        assert error_codes(protocol) == ["watcher_not_allowed"]

    def test_another_component_is_refused(self):
        session = build_session(dict(SECRET, kind="counter", color="#ffffff", value=0))
        dark_protocol, *_ = seated(session)
        call(validate_choice, dark_protocol, "shadow_letter")
        assert error_codes(dark_protocol) == ["component_not_secret_choice"]


class TestHiding:
    def test_the_session_hides_the_value_until_it_is_revealed(self):
        session = build_session(dict(SECRET, choice="E", validated=True))
        _, dark, _, free, watcher = seated(session)

        def described(user):
            (secret,) = session.return_session_json(user)["components"]["fixed"]
            return secret["choice"]

        assert described(dark) == "E"
        assert described(free) is None and described(watcher) is None
        session.get_component("shadow_letter").reveal()
        assert described(free) == "E" and described(watcher) == "E"

    def test_the_saved_state_keeps_the_round_for_the_resumption(self):
        session = build_session()
        dark_protocol, *_ = seated(session)
        call(choose, dark_protocol, "shadow_letter", choice="C")
        call(validate_choice, dark_protocol, "shadow_letter")

        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(user=None, name="Ring", key="KEY1", code="CODE1",
                          active=True, variant="std", game_json=state, started=True)
        secret = resumed.get_component("shadow_letter")
        assert (secret.choice, secret.validated, secret.revealed) == ("C", True, False)

"""
Tests of the voice channel: the server tells who is in it and relays the
WebRTC messages between its players; the audio itself never reaches it.
"""
import base64
import hashlib
import hmac

from handle_message import voice_allow, voice_join, voice_leave, voice_signal
from session import Session
from tests.test_bag import LOGGER, FakeProtocol, FakeUser, error_codes
from voice import ice_servers, turn_credentials


def build_session():
    game_json = {
        "game": {"max_players": 3, "max_watchers": 2},
        "fixed": [],
        "movable": [],
        "options": {},
    }
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def table(session):
    """Two players and a watcher."""
    users = [FakeUser(name, session=session) for name in ("alice", "bob")]
    session.players = list(users)
    dave = FakeUser("dave", role="watcher", session=session)
    session.watchers = [dave]
    return [FakeProtocol(user) for user in users], users, dave


def ask(handler, protocol, **fields):
    handler(protocol, LOGGER, {"action": "x", **fields})


def events(user, name):
    return [message for message in user.sent if message["event"] == name]


class TestJoining:
    def test_the_newcomer_is_told_who_to_call(self):
        session = build_session()
        (alice, bob), (a, b), _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        assert events(a, "voice_joined")[-1]["peers"] == []
        assert events(b, "voice_joined")[-1]["peers"] == ["alice"]

    def test_everybody_is_told_the_members(self):
        session = build_session()
        (alice, bob), (a, b), dave = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        for user in (a, b, dave):
            assert events(user, "voice_members")[-1]["members"] == ["alice", "bob"]

    def test_joining_twice_keeps_one_entry(self):
        session = build_session()
        (alice, _), (a, _), _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, alice)
        assert session.voice_members() == ["alice"]
        assert events(a, "voice_joined")[-1]["peers"] == []

    def test_a_watcher_does_not_talk(self):
        session = build_session()
        _, _, dave = table(session)
        watcher = FakeProtocol(dave)
        ask(voice_join, watcher)
        assert error_codes(watcher) == ["watcher_not_allowed"]
        assert session.voice == []

    def test_the_session_says_who_is_in_the_channel(self):
        session = build_session()
        (alice, _), _, dave = table(session)
        ask(voice_join, alice)
        assert session.return_session_json(dave)["voice"] == ["alice"]



class TestWatcherAllowedToTalk:
    """The owner lets a watcher talk, and takes it back (voice_allow)."""

    def owned(self):
        session = build_session()
        session.owner_nickname = "alice"
        (alice, bob), (a, b), dave = table(session)
        return session, alice, bob, dave

    def test_only_the_owner_allows(self):
        session, _, bob, dave = self.owned()
        ask(voice_allow, bob, nickname="dave", allowed=True)
        assert error_codes(bob) == ["not_session_owner"]
        assert session.voice_speakers == []

    def test_only_a_watcher_is_allowed(self):
        session, alice, _, _ = self.owned()
        ask(voice_allow, alice, nickname="bob", allowed=True)
        ask(voice_allow, alice, nickname="dave", allowed="yes")
        assert error_codes(alice) == ["unknown_watcher", "invalid_message"]

    def test_an_allowed_watcher_talks(self):
        session, alice, _, dave = self.owned()
        ask(voice_allow, alice, nickname="dave", allowed=True)
        # every screen is told, the watcher included
        status = events(dave, "session_status")[-1]
        assert [p for p in status["participants"] if p["nickname"] == "dave"][0]["voice"] is True
        watcher = FakeProtocol(dave)
        ask(voice_join, alice)
        ask(voice_join, watcher)
        assert error_codes(watcher) == []
        assert events(dave, "voice_joined")[-1]["peers"] == ["alice"]
        ask(voice_signal, watcher, to="alice", data={"candidate": {}})
        assert error_codes(watcher) == []

    def test_taking_it_back_hangs_up(self):
        session, alice, _, dave = self.owned()
        watcher = FakeProtocol(dave)
        ask(voice_allow, alice, nickname="dave", allowed=True)
        ask(voice_join, watcher)
        ask(voice_allow, alice, nickname="dave", allowed=False)
        assert session.voice_members() == []
        assert events(alice.user, "voice_members")[-1]["members"] == []
        assert [p for p in events(dave, "session_status")[-1]["participants"]
                if p["nickname"] == "dave"][0]["voice"] is False
        # it can no longer emit nor come back
        ask(voice_signal, watcher, to="alice", data={"candidate": {}})
        ask(voice_join, watcher)
        assert error_codes(watcher) == ["watcher_not_allowed", "watcher_not_allowed"]

    def test_the_permission_is_saved(self):
        session, alice, _, _ = self.owned()
        ask(voice_allow, alice, nickname="dave", allowed=True)
        game_json = {"game": {"max_players": 3, "max_watchers": 2}, "fixed": [], "movable": [], "options": {},
                     "voice_speakers": session.voice_speakers}
        again = Session(user=None, name="Waterloo", key="KEY1", code="CODE1", active=True, variant="std",
                        game_json=game_json, started=True)
        assert again.voice_speakers == ["dave"]


class TestLeaving:
    def test_a_player_hangs_up(self):
        session = build_session()
        (alice, bob), (_, b), _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        ask(voice_leave, alice)
        assert events(b, "voice_members")[-1]["members"] == ["bob"]

    def test_a_player_leaving_the_session_leaves_the_channel(self):
        session = build_session()
        (alice, bob), (a, b), _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        session.remove_user(a)
        assert session.voice_members() == ["bob"]
        assert events(b, "voice_members")[-1]["members"] == ["bob"]

    def test_leaving_without_being_in_says_nothing(self):
        session = build_session()
        (alice, _), (a, b), _ = table(session)
        ask(voice_leave, alice)
        assert error_codes(alice) == []
        assert events(b, "voice_members") == []


class TestSignals:
    OFFER = {"description": {"type": "offer", "sdp": "v=0"}}

    def test_a_signal_reaches_its_target_only(self):
        session = build_session()
        (alice, bob), (a, b), dave = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        ask(voice_signal, bob, to="alice", data=self.OFFER)
        assert events(a, "voice_signal") == [{"event": "voice_signal", "from": "bob", "data": self.OFFER}]
        assert events(b, "voice_signal") == []
        assert events(dave, "voice_signal") == []

    def test_only_a_member_sends_signals(self):
        session = build_session()
        (alice, bob), _, _ = table(session)
        ask(voice_join, alice)
        ask(voice_signal, bob, to="alice", data=self.OFFER)
        assert error_codes(bob) == ["voice_not_joined"]

    def test_a_signal_goes_to_another_member(self):
        session = build_session()
        (alice, bob), _, _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        for target in ("bob", "carol", None):
            ask(voice_signal, bob, to=target, data=self.OFFER)
        assert error_codes(bob) == ["unknown_player"] * 3

    def test_a_signal_is_a_small_object(self):
        session = build_session()
        (alice, bob), _, _ = table(session)
        ask(voice_join, alice)
        ask(voice_join, bob)
        ask(voice_signal, bob, to="alice", data="offer")
        ask(voice_signal, bob, to="alice", data={"sdp": "x" * 70000})
        assert error_codes(bob) == ["invalid_voice_signal"] * 2


class TestIceServers:
    def test_a_public_stun_server_by_default(self, monkeypatch):
        for name in ("OBG_STUN_URLS", "OBG_TURN_URLS", "OBG_TURN_SECRET"):
            monkeypatch.delenv(name, raising=False)
        assert ice_servers("alice") == [{"urls": ["stun:stun.l.google.com:19302"]}]

    def test_a_turn_server_gets_credentials_that_expire(self, monkeypatch):
        monkeypatch.setenv("OBG_STUN_URLS", "")
        monkeypatch.setenv("OBG_TURN_URLS", "turn:example.org:3478, turns:example.org:5349")
        monkeypatch.setenv("OBG_TURN_SECRET", "s3cret")
        (turn,) = ice_servers("alice")
        assert turn["urls"] == ["turn:example.org:3478", "turns:example.org:5349"]
        assert turn["username"].endswith(":alice")

    def test_the_credentials_are_the_ones_coturn_checks(self):
        username, credential = turn_credentials("alice", "s3cret", 3600, now=1000)
        assert username == "4600:alice"
        expected = hmac.new(b"s3cret", b"4600:alice", hashlib.sha1).digest()
        assert credential == base64.b64encode(expected).decode("ascii")

    def test_no_turn_server_without_its_secret(self, monkeypatch):
        monkeypatch.setenv("OBG_STUN_URLS", "")
        monkeypatch.setenv("OBG_TURN_URLS", "turn:example.org:3478")
        monkeypatch.delenv("OBG_TURN_SECRET", raising=False)
        assert ice_servers("alice") == []

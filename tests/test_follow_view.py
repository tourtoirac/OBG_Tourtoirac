"""
Tests of the following of a view: a player tells the center of its view, and
whoever follows that player is sent it, at once and at each change.
"""
from handle_message import follow, view
from tests.test_bag import FakeProtocol, error_codes
from tests.test_chat import build_session, table
from tests.test_voice import ask, events


class TestFollow:
    def test_a_follower_is_sent_each_new_view(self):
        session = build_session()
        (alice, bob), (a, b), _ = table(session)
        ask(follow, bob, nickname="alice")
        ask(view, alice, x=120, y=45.5)
        assert events(b, "view") == [{"event": "view", "nickname": "alice", "x": 120.0, "y": 45.5}]
        # alice does not follow herself, bob's view goes nowhere
        ask(view, bob, x=1, y=2)
        assert events(a, "view") == []

    def test_the_last_view_is_sent_on_follow(self):
        session = build_session()
        (alice, _), _, dave = table(session)
        ask(view, alice, x=10, y=20)
        ask(follow, FakeProtocol(dave), nickname="alice")
        assert events(dave, "view")[-1]["x"] == 10.0

    def test_nobody_is_sent_anything_after_stopping(self):
        session = build_session()
        (alice, bob), (_, b), _ = table(session)
        ask(follow, bob, nickname="alice")
        ask(follow, bob, nickname=None)
        ask(view, alice, x=10, y=20)
        assert events(b, "view") == []

    def test_only_another_player_of_the_game_is_followed(self):
        session = build_session()
        (alice, _), _, _ = table(session)
        for nickname in ("alice", "dave", "nobody", 3):
            ask(follow, alice, nickname=nickname)
        assert error_codes(alice) == ["unknown_player"] * 4

    def test_a_player_away_is_followed(self):
        session = build_session()
        (alice, _), _, _ = table(session)
        ask(follow, alice, nickname="carol")
        assert error_codes(alice) == []


class TestView:
    def test_a_view_needs_finite_coordinates(self):
        session = build_session()
        (alice, _), _, _ = table(session)
        for x in (None, "1", True, float("nan"), float("inf")):
            ask(view, alice, x=x, y=0)
        assert error_codes(alice) == ["invalid_view"] * 5
        assert session.views == {}

    def test_the_view_of_a_watcher_is_ignored(self):
        session = build_session()
        _, _, dave = table(session)
        ask(view, FakeProtocol(dave), x=1, y=2)
        assert session.views == {}


class TestFollowed:
    def test_a_player_is_told_when_it_starts_and_stops_being_followed(self):
        session = build_session()
        (_, bob), (a, _), dave = table(session)
        ask(follow, bob, nickname="alice")
        ask(follow, FakeProtocol(dave), nickname="alice")
        ask(follow, bob, nickname=None)
        assert [event["followers"] for event in events(a, "followed")] == [["bob"], ["bob", "dave"], ["dave"]]
        ask(follow, FakeProtocol(dave), nickname=None)
        assert events(a, "followed")[-1] == {"event": "followed", "followed": False, "followers": []}

    def test_switching_tells_both_players(self):
        session = build_session()
        (alice, bob), (a, b), dave = table(session)
        ask(follow, FakeProtocol(dave), nickname="alice")
        ask(follow, FakeProtocol(dave), nickname="bob")
        assert events(a, "followed")[-1]["followed"] is False
        assert events(b, "followed")[-1]["followed"] is True

    def test_a_follower_leaving_stops_the_following(self):
        session = build_session()
        _, (a, _), dave = table(session)
        ask(follow, FakeProtocol(dave), nickname="alice")
        session.remove_user(dave)
        assert events(a, "followed")[-1]["followed"] is False

    def test_the_session_tells_the_reader_whether_it_is_followed(self):
        session = build_session()
        _, (a, b), dave = table(session)
        ask(follow, FakeProtocol(dave), nickname="alice")
        assert session.return_session_json(a)["followers"] == ["dave"]
        assert session.return_session_json(b)["followers"] == []

    def test_nobody_follows_a_player_who_follows_it(self):
        session = build_session()
        (alice, bob), (a, b), _ = table(session)
        ask(follow, alice, nickname="bob")
        ask(follow, bob, nickname="alice")
        assert error_codes(bob) == ["follows_you"]
        assert b.following is None
        # once alice stops, bob may follow her
        ask(follow, alice, nickname=None)
        ask(follow, bob, nickname="alice")
        assert b.following == "alice"

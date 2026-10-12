"""
Tests of the list of names each game page shows: the seats, connected or not,
then the watchers.
"""
from session import Session
from tests.test_bag import FakeUser


def build_session(**extra):
    game_json = {"game": {"max_players": 3, "max_watchers": 2}, "fixed": [], "movable": []}
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, **extra,
    )


def line(nickname, role, connected=True):
    return {"nickname": nickname, "role": role, "connected": connected}


class TestParticipants:
    def test_a_seat_is_listed_connected_or_not(self):
        session = build_session(player_names=["alice", "bob"])
        session.add_user(FakeUser("bob"), "player")
        assert session.participants() == [line("alice", "player", False), line("bob", "player")]

    def test_the_watchers_come_after_the_seats(self):
        session = build_session(player_names=["alice"])
        session.add_user(FakeUser("carol"), "watcher")
        session.add_user(FakeUser("alice"), "player")
        assert session.participants() == [line("alice", "player"), line("carol", "watcher")]

    def test_a_player_without_a_known_seat_is_listed(self):
        session = build_session()
        session.add_user(FakeUser("alice"), "player")
        assert session.participants() == [line("alice", "player")]

    def test_a_closed_game_keeps_its_players_as_players(self):
        session = build_session(player_names=["alice", "bob"])
        session.add_user(FakeUser("alice"), "player")
        session.add_user(FakeUser("carol"), "watcher")
        session.close()
        assert session.participants() == [
            line("alice", "player"), line("bob", "player", False), line("carol", "watcher"),
        ]

    def test_the_status_and_the_session_json_carry_the_list(self):
        session = build_session(player_names=["alice"])
        assert session.return_status_json()["participants"] == [line("alice", "player", False)]
        assert session.return_session_json()["participants"] == [line("alice", "player", False)]

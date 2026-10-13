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


class TestOrder:
    """The players are listed in the order they joined, unless the owner changed it."""

    @staticmethod
    def names(session):
        return [entry["nickname"] for entry in session.participants() if entry["role"] == "player"]

    def test_the_players_are_listed_in_the_order_they_joined(self):
        session = build_session(player_names=["bob"])
        session.refresh_seats(False, ["alice", "bob", "carol"])
        # bob was known first: a later list of seats does not move him
        assert self.names(session) == ["bob", "alice", "carol"]

    def test_the_owner_changes_the_order(self):
        session = build_session(player_names=["alice", "bob", "carol"])
        assert session.order_players(["carol", "alice", "bob"])
        assert self.names(session) == ["carol", "alice", "bob"]
        assert session.dealt_players() == ["carol", "alice", "bob"]

    def test_the_players_left_out_keep_their_order_after_the_others(self):
        session = build_session(player_names=["alice", "bob", "carol"])
        assert session.order_players(["carol"])
        assert self.names(session) == ["carol", "alice", "bob"]

    def test_an_order_naming_a_stranger_or_a_player_twice_is_refused(self):
        session = build_session(player_names=["alice", "bob"])
        for nicknames in (["alice", "dave"], ["alice", "alice"], "alice", None, [1]):
            assert not session.order_players(nicknames)
        assert self.names(session) == ["alice", "bob"]

    def test_the_order_survives_a_saved_session(self):
        session = build_session(player_names=["alice", "bob", "carol"])
        session.order_players(["bob", "carol", "alice"])
        state = session.game_json_state()
        assert state["player_order"] == ["bob", "carol", "alice"]
        resumed = Session(
            user=None, name="Vietnam", key="KEY1", code="CODE1", active=True,
            variant="std", game_json=state, player_names=["alice", "bob", "carol"],
        )
        assert self.names(resumed) == ["bob", "carol", "alice"]

    def test_only_the_owner_orders_the_players(self):
        from handle_message import order_players
        from tests.test_bag import LOGGER, FakeProtocol, error_codes
        session = build_session(player_names=["alice", "bob"], owner_nickname="alice")
        alice, bob = FakeUser("alice", session=session), FakeUser("bob", session=session)
        session.players = [alice, bob]
        refused = FakeProtocol(bob)
        order_players(refused, LOGGER, {"nicknames": ["bob", "alice"]})
        assert error_codes(refused) == ["not_session_owner"]
        assert self.names(session) == ["alice", "bob"]
        order_players(FakeProtocol(alice), LOGGER, {"nicknames": ["bob", "alice"]})
        assert self.names(session) == ["bob", "alice"]
        assert bob.sent[-1]["event"] == "session_status"
        assert [entry["nickname"] for entry in bob.sent[-1]["participants"]] == ["bob", "alice"]

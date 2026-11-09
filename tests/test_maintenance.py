"""
Tests of the maintenance of the site (maintenance.py): announced to every
screen, no game created meanwhile, every session stored and every connection
closed when it starts, then connections accepted again once it is over.
Also the shutdown of the server, which stores the sessions the same way.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from twisted.internet import task

from admin_api import AdminResource
from lobby import Lobby
from maintenance import CLOSE_CODE, MaintenanceError
from tests.test_lobby import LOGGER, FakeChabanas, FakeProtocol
from user import User


class ClosingProtocol(FakeProtocol):
    """A FakeProtocol whose close code is kept, as autobahn's sendClose takes one."""

    def __init__(self):
        super().__init__()
        self.close_code = None

    def sendClose(self, code=None, reason=None):
        self.closed = True
        self.close_code = code


def decoded(protocol):
    return [json.loads(payload) for payload in protocol.sent]


@pytest.fixture
def clock():
    return task.Clock()


@pytest.fixture
def lobby(session_info, clock):
    return Lobby(LOGGER, FakeChabanas(session_info), clock=clock)


def connect(lobby, name):
    user = User(name=name, protocol=ClosingProtocol())
    lobby.add_user(user)
    return user


def in_minutes(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


class TestAnnounce:
    def test_every_screen_gets_the_countdown(self, lobby):
        alice, bob = connect(lobby, "alice"), connect(lobby, "bob")
        state = lobby.announce_maintenance(in_minutes(5), 10, "  Mise à jour  ")
        assert state["phase"] == "announced"
        assert state["duration_minutes"] == 10 and state["message"] == "Mise à jour"
        for user in (alice, bob):
            event = decoded(user.protocol)[-1]
            assert event["event"] == "maintenance" and event["phase"] == "announced"
            assert event["starts_at"] == state["starts_at"]

    @pytest.mark.parametrize("starts_at, duration, message", [
        ("tomorrow", 10, ""), ("2026-10-31T10:00:00", 10, ""), (None, 10, ""),
        (in_minutes(5), 0, ""), (in_minutes(5), "10", ""), (in_minutes(5), True, ""),
        (in_minutes(5), 10, "x" * 301), (in_minutes(5), 10, 3),
    ])
    def test_a_badly_written_announcement_is_refused(self, lobby, starts_at, duration, message):
        with pytest.raises(MaintenanceError):
            lobby.announce_maintenance(starts_at, duration, message)
        assert lobby.maintenance.phase == "none"

    def test_no_game_is_created_once_announced(self, lobby, sync):
        lobby.announce_maintenance(in_minutes(5), 10)
        alice = connect(lobby, "alice")
        assert sync(lobby.create_session("Waterloo", alice, "")) == (False, "maintenance")
        assert "/session/create" not in lobby.chabanas.calls

    def test_nobody_new_joins_a_game_once_announced(self, lobby, sync, session_info):
        lobby.chabanas.info["players"] = [{"nickname": "alice"}]
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        lobby.announce_maintenance(in_minutes(5), 10)
        for name, role in (("bob", "player"), ("carol", "watcher")):
            newcomer = connect(lobby, name)
            assert sync(lobby.join_session("CODE1", newcomer, "", role)) == (False, "maintenance")
        assert "/session/join" not in lobby.chabanas.calls

    def test_a_player_comes_back_to_a_game_no_longer_in_memory(self, lobby, sync, session_info):
        lobby.chabanas.info["players"] = [{"nickname": "alice"}]
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        lobby.delete_user(alice)
        assert lobby.sessions == {}
        lobby.announce_maintenance(in_minutes(5), 10)
        back = connect(lobby, "alice")
        assert sync(lobby.join_session("CODE1", back, "", "player")) == (True, None)
        stranger = connect(lobby, "bob")
        assert sync(lobby.join_session("CODE1", stranger, "", "player")) == (False, "maintenance")

    def test_cancelling_hides_the_countdown_and_nothing_starts(self, lobby, clock):
        alice = connect(lobby, "alice")
        lobby.announce_maintenance(in_minutes(5), 10)
        assert lobby.cancel_maintenance()["phase"] == "none"
        assert decoded(alice.protocol)[-1] == {
            "event": "maintenance", "phase": "none", "starts_at": None, "duration_minutes": 0, "message": ""}
        clock.advance(600)
        assert lobby.maintenance.phase == "none" and not alice.protocol.closed


class TestStart:
    def test_it_starts_by_itself_at_the_time_announced(self, lobby, clock, sync, session_info):
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        watcher = connect(lobby, "dave")
        lobby.announce_maintenance(in_minutes(2), 10)
        clock.advance(60)
        assert lobby.maintenance.phase == "announced" and not alice.protocol.closed
        clock.advance(61)
        assert lobby.maintenance.phase == "started"
        # every screen was told, then disconnected
        for user in (alice, watcher):
            assert decoded(user.protocol)[-1]["event"] == "maintenance_started"
            assert user.protocol.closed and user.protocol.close_code == CLOSE_CODE
        # the session was stored, freed and forgotten: the next join rebuilds it
        assert "/session/update" in lobby.chabanas.calls
        assert "/session/vacate" in lobby.chabanas.calls
        assert lobby.sessions == {} and lobby.users == {}

    def test_the_stored_state_is_the_one_of_the_table(self, lobby, sync, session_info):
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        expected = alice.session.game_json_state()
        sync(lobby.start_maintenance())
        [(_, payload)] = [call for call in lobby.chabanas.payloads if call[0] == "/session/update"]
        assert payload["game_json"] == expected

    def test_a_start_at_once_and_a_second_start(self, lobby, sync):
        alice = connect(lobby, "alice")
        lobby.announce_maintenance(in_minutes(30), 10)
        sync(lobby.start_maintenance())
        assert lobby.maintenance.phase == "started"
        # started before the time announced: it began now
        assert datetime.fromisoformat(lobby.maintenance.state_json()["starts_at"]) <= datetime.now(UTC)
        assert alice.protocol.closed
        assert sync(lobby.start_maintenance()) is None
        # no announcement once started
        with pytest.raises(MaintenanceError):
            lobby.announce_maintenance(in_minutes(5), 10)

    def test_a_storage_failure_does_not_stop_the_maintenance(self, lobby, sync):
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        lobby.chabanas.available = False
        sync(lobby.start_maintenance())
        assert lobby.maintenance.phase == "started" and alice.protocol.closed

    def test_ending_lets_people_in_again(self, lobby, sync):
        sync(lobby.start_maintenance())
        assert lobby.end_maintenance()["phase"] == "none"
        alice = connect(lobby, "alice")
        assert sync(lobby.create_session("Waterloo", alice, ""))[0] is True

    def test_cancelling_a_started_maintenance_ends_it(self, lobby, sync):
        sync(lobby.start_maintenance())
        assert lobby.cancel_maintenance()["phase"] == "none"


class TestShutdown:
    def test_the_sessions_are_stored_before_the_process_exits(self, lobby, sync):
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        expected = alice.session.game_json_state()
        stored = lobby.shutdown()
        sync(stored)
        [(_, payload)] = [call for call in lobby.chabanas.payloads if call[0] == "/session/update"]
        assert payload["game_json"] == expected
        assert alice.protocol.closed

    def test_a_closed_session_is_not_stored_again(self, lobby, sync):
        alice = connect(lobby, "alice")
        sync(lobby.create_session("Waterloo", alice, ""))
        alice.session.closed = True
        sync(lobby.shutdown())
        assert "/session/update" not in lobby.chabanas.calls


class FakeRequest:
    """What AdminResource reads of a twisted.web request."""

    def __init__(self, path, body=None, secret="s3cret"):
        self.path = path
        self.headers = {"x-obg-admin-secret": secret}
        self.content = type("Content", (), {"read": lambda _: json.dumps(body).encode() if body is not None else b""})()
        self.code = None
        self.written = b""
        self.finished = False
        self._disconnected = False

    def getHeader(self, name):
        return self.headers.get(name.decode() if isinstance(name, bytes) else name)

    def setResponseCode(self, code):
        self.code = code

    def setHeader(self, name, value):
        pass

    def write(self, data):
        self.written += data

    def finish(self):
        self.finished = True


class TestAdminApi:
    @pytest.fixture
    def api(self, lobby):
        return AdminResource(lobby, LOGGER, "s3cret")

    def post(self, api, body, secret="s3cret"):
        request = FakeRequest(b"/admin/maintenance", body, secret)
        answer = api.render_POST(request)
        if isinstance(answer, bytes):
            return request.code, json.loads(answer)
        return request.code, json.loads(request.written)

    def test_announce_then_read_then_cancel(self, api):
        code, state = self.post(api, {"action": "announce", "starts_at": in_minutes(5), "duration_minutes": 10})
        assert code == 200 and state["phase"] == "announced"
        request = FakeRequest(b"/admin/maintenance")
        assert json.loads(api.render_GET(request))["phase"] == "announced"
        assert self.post(api, {"action": "cancel"}) == (200, {
            "phase": "none", "starts_at": None, "duration_minutes": 0, "message": ""})

    def test_start_answers_once_the_sessions_are_stored(self, api, lobby):
        code, state = self.post(api, {"action": "start"})
        assert code == 200 and state["phase"] == "started"
        assert self.post(api, {"action": "end"})[1]["phase"] == "none"

    def test_refusals(self, api):
        assert self.post(api, {"action": "announce", "starts_at": "soon", "duration_minutes": 10})[0] == 400
        assert self.post(api, {"action": "reboot"})[0] == 400
        assert self.post(api, ["start"])[0] == 400
        assert self.post(api, {"action": "start"}, secret="wrong")[0] == 403

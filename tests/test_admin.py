"""
Tests of the administration of OBG (Chabanas /manage/ pages): an administrator
watching any game, the presence of each session, closing and deleting a
session with people in it, and the internal HTTP API that exposes them.
"""
import io
import json
import logging

import pytest

autobahn = pytest.importorskip("autobahn", reason="autobahn[twisted] is required")

from twisted.web.test.requesthelper import DummyRequest  # noqa: E402

from admin_api import AdminResource  # noqa: E402
from conftest import SESSION_INFO  # noqa: E402
from lobby import Lobby  # noqa: E402
from test_session_close import (  # noqa: E402
    FakeChabanas, connect, join, only_session, open_session, receive, session_info,
)

LOGGER = logging.getLogger("tests")
SECRET = "s3cret"


def watch_as_admin(lobby, nickname="Administrateur"):
    protocol = connect(lobby)
    receive(protocol, {
        "action": "resume_session",
        "session_key": SESSION_INFO["key"],
        "session_code": SESSION_INFO["code"],
        "nickname": nickname, "role": "admin",
    })
    return protocol


@pytest.fixture
def chabanas():
    return FakeChabanas(session_info(max_watchers=1))


@pytest.fixture
def lobby(chabanas):
    return Lobby(LOGGER, chabanas)


class TestAdminWatcher:
    def test_an_admin_watches_a_game_with_all_its_watchers(self, lobby):
        open_session(lobby)
        join(lobby, "carol", role="watcher")

        admin = watch_as_admin(lobby)

        assert admin.last_event() == "session_joined", admin.last
        assert admin.last["role"] == "watcher"
        assert len(only_session(lobby).watchers) == 2

    def test_an_admin_never_takes_the_place_of_a_participant(self, lobby):
        open_session(lobby, nickname="Administrateur")

        admin = watch_as_admin(lobby)

        assert admin.last_error() == "nickname_connected"
        assert [u.name for u in only_session(lobby).players] == ["Administrateur"]

    def test_the_presence_counts_the_admins_apart(self, lobby):
        open_session(lobby)
        join(lobby, "carol", role="watcher")
        watch_as_admin(lobby)

        [presence] = lobby.presence()

        assert presence["players"] == ["alice"]
        assert presence["watchers"] == ["carol"]
        assert presence["admins"] == 1


class TestAdminClose:
    def test_the_players_become_watchers_told_by_the_admin(self, lobby, chabanas, on_reactor):
        owner = open_session(lobby)

        handled, reason = on_reactor(lobby.admin_close, SESSION_INFO["key"])

        assert (handled, reason) == (True, None)
        assert owner.events("session_closed") == [{"event": "session_closed", "by_admin": True}]
        session = only_session(lobby)
        assert session.closed and not session.players
        assert "/session/archive" in chabanas.paths()

    def test_a_session_not_in_memory_is_left_to_chabanas(self, lobby, on_reactor):
        assert on_reactor(lobby.admin_close, "unknown") == (False, None)


class TestAdminDelete:
    def test_everybody_is_told_and_taken_out(self, lobby, chabanas):
        owner = open_session(lobby)
        watcher = join(lobby, "carol", role="watcher")
        users = [lobby.get_user(owner), lobby.get_user(watcher)]

        assert lobby.admin_delete(SESSION_INFO["key"]) is True

        assert owner.events("session_deleted") and watcher.events("session_deleted")
        assert lobby.sessions == {}
        assert all(user.session is None for user in users)
        assert "/session/vacate" in chabanas.paths()

    def test_a_session_not_in_memory(self, lobby):
        assert lobby.admin_delete("unknown") is False


def request(method, path, secret=SECRET, body=None):
    req = DummyRequest([path.strip("/").encode()])
    req.method = method.encode()
    req.path = path.encode()
    if secret is not None:
        req.requestHeaders.setRawHeaders(b"x-obg-admin-secret", [secret.encode()])
    req.content = io.BytesIO(json.dumps(body or {}).encode())
    return req


class TestAdminApi:
    def test_a_request_without_the_secret_is_refused(self, lobby):
        req = request("GET", "/admin/presence", secret=None)

        AdminResource(lobby, LOGGER, SECRET).render(req)

        assert req.responseCode == 403

    def test_a_wrong_secret_is_refused(self, lobby):
        req = request("GET", "/admin/presence", secret="nope")

        AdminResource(lobby, LOGGER, SECRET).render(req)

        assert req.responseCode == 403

    def test_the_presence(self, lobby):
        open_session(lobby)
        req = request("GET", "/admin/presence")

        body = AdminResource(lobby, LOGGER, SECRET).render(req)

        assert json.loads(body)["sessions"][0]["players"] == ["alice"]

    def test_delete(self, lobby):
        open_session(lobby)
        req = request("POST", "/admin/delete", body={"key": SESSION_INFO["key"]})

        body = AdminResource(lobby, LOGGER, SECRET).render(req)

        assert json.loads(body) == {"handled": True}
        assert lobby.sessions == {}


class TestAdminCheck:
    def test_the_problems_of_a_game_json(self, lobby):
        req = request("POST", "/admin/check", body={"game_json": {"fixed": [{"kind": "bord", "id": "x"}]}})

        body = json.loads(AdminResource(lobby, LOGGER, SECRET).render(req))

        assert req.responseCode == 200
        assert len(body["problems"]) == 1

    def test_without_game_json(self, lobby):
        req = request("POST", "/admin/check", body={})

        AdminResource(lobby, LOGGER, SECRET).render(req)

        assert req.responseCode == 400

    def test_without_the_secret(self, lobby):
        req = request("POST", "/admin/check", secret=None, body={"game_json": {}})

        AdminResource(lobby, LOGGER, SECRET).render(req)

        assert req.responseCode == 403

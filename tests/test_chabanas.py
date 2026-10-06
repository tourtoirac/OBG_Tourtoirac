import json
import logging
import threading

import pytest
from twisted.internet import reactor
from twisted.web import resource, server
from twisted.web.server import NOT_DONE_YET

from chabanas import Chabanas

LOGGER = logging.getLogger("tests")

SESSION = {
    "name": "Waterloo",
    "key": "KEY1",
    "code": "CODE1",
    "active": True,
    "variant": "std",
    "game_json": {
        "game": {"max_players": 2, "max_watchers": 1},
        "fixed": [],
        "movable": [],
    },
}


class FakeUser:
    name = "alice"


class Backend(resource.Resource):
    isLeaf = True

    def render_POST(self, request):
        body = json.loads(request.content.read().decode())
        path = request.path.decode()

        if path == "/session/create":
            if body.get("game_name") == "Inconnu":
                request.setResponseCode(404)
                return b'{"error":"unknown game"}'
            return json.dumps({"session_code": SESSION["code"]}).encode()

        if path in ("/session/get", "/session/join"):
            if body.get("session_code") == "INCONNU":
                request.setResponseCode(404)
                return b'{"error":"not found"}'
            if body.get("access_key") == "MAUVAISE":
                request.setResponseCode(401)
                return b"Unauthorized"
            if body.get("role") == "watcher" and SESSION["name"] == "Waterloo":
                request.setResponseCode(403)
                return b"Conflict"
            if body.get("role") == "admin":
                request.setResponseCode(400)
                return b"Bad Request"
            return json.dumps({"session": SESSION}).encode()

        if path == "/session/list":
            if "Casse" in body.get("game_name_list", []):
                request.setResponseCode(404)
                return b'{"error":"nope"}'
            return json.dumps({"sessions": {"CODE1": {"code": "CODE1"}}}).encode()

        if path == "/game/list":
            if "Casse" in body.get("game_name_list", []):
                request.setResponseCode(404)
                return b'{"error":"nope"}'
            return json.dumps({"game_list": [{"name": "Waterloo"}]}).encode()

        if path == "/session/update":
            return json.dumps({"status": "success"}).encode()

        if path == "/session/archive":
            # the real view answers with plain text, not JSON, and says so
            # through its status code
            if body.get("key") == "INCONNU":
                request.setResponseCode(404)
                return b"{}"
            return b"Success"

        if path == "/not-json":
            return b"<html>definitely not json</html>"

        if path == "/no-session-field":
            return json.dumps({"unexpected": True}).encode()

        request.setResponseCode(404)
        return b"{}"


class SilentBackend(resource.Resource):
    """Accepts the connection but never answers."""

    isLeaf = True

    def render_POST(self, request):
        return NOT_DONE_YET


@pytest.fixture
def serve(on_reactor):
    """Starts a Resource on a random port, yields the base URL, then stops it."""
    servers = []

    def _serve(site_resource):
        listening = on_reactor(
            reactor.listenTCP, 0, server.Site(site_resource), interface="127.0.0.1"
        )
        servers.append(listening)
        return f"http://127.0.0.1:{listening.getHost().port}"

    yield _serve

    for listening in servers:
        try:
            on_reactor(listening.stopListening)
        except Exception:
            pass


@pytest.fixture
def chabanas(serve):
    instance = Chabanas(LOGGER, timeout=10.0)
    instance.host_url = serve(Backend())
    return instance


class TestSuccessfulCalls:
    def test_create_session_returns_the_session(self, chabanas, sync):
        session = sync(chabanas.create_session("Waterloo", FakeUser(), "key"))

        assert session["code"] == SESSION["code"]
        assert "game_json" in session

    def test_join_session_returns_the_unwrapped_session(self, chabanas, sync):
        """
        join_session used to return the whole response body while create_session
        returned ['session'], so callers indexing ['name'] raised KeyError.
        It now returns (session, None) so the caller can tell a refusal from a
        session it could not read.
        """
        session, reason = sync(chabanas.join_session("CODE1", FakeUser(), ""))

        assert reason is None
        assert session["name"] == "Waterloo"
        assert session["key"] == "KEY1"

    def test_join_session_forwards_access_key_and_role(self, chabanas, sync):
        """Chabanas is the only one able to check the access_key, so it must be sent."""
        session, reason = sync(
            chabanas.join_session("CODE1", FakeUser(), "key", "MAUVAISE", "player")
        )

        assert session is None
        assert reason == "access_key_incorrect"

    def test_join_session_reports_watchers_not_allowed(self, chabanas, sync):
        session, reason = sync(
            chabanas.join_session("CODE1", FakeUser(), "key", "", "watcher")
        )

        assert session is None
        assert reason == "watchers_not_allowed"

    def test_join_session_reports_an_invalid_role(self, chabanas, sync):
        session, reason = sync(
            chabanas.join_session("CODE1", FakeUser(), "key", "", "admin")
        )

        assert session is None
        assert reason == "invalid_role"

    def test_get_active_sessions(self, chabanas, sync):
        assert sync(chabanas.get_active_sessions(["Waterloo"], [])) == {
            "CODE1": {"code": "CODE1"}
        }

    def test_get_game_list(self, chabanas, sync):
        assert sync(chabanas.get_game_list(["Waterloo"])) == [{"name": "Waterloo"}]

    def test_get_session_info(self, chabanas, sync):
        assert sync(chabanas.get_session_info("CODE1"))["key"] == "KEY1"

    def test_update_session_state(self, chabanas, sync):
        assert sync(chabanas.update_session_state("KEY1", {"movable": []})) is True

    def test_archive_session_reads_a_plain_text_answer(self, chabanas, sync):
        """
        /session/archive answers with the text "Success", which the JSON reader
        cannot decode. Only the status code tells the session was archived.
        """
        assert sync(chabanas.archive_session("KEY1")) is True


class TestFailureHandling:
    """Every failure must resolve to a falsy value, never raise."""

    def test_create_session_http_error(self, chabanas, sync):
        assert sync(chabanas.create_session("Inconnu", FakeUser(), "k")) is False

    def test_join_session_http_error(self, chabanas, sync):
        """A 404 is an unknown session, not a wrong access_key."""
        session, reason = sync(chabanas.join_session("INCONNU", FakeUser(), ""))

        assert session is None
        assert reason == "Unable to join session"

    def test_get_active_sessions_http_error(self, chabanas, sync):
        assert sync(chabanas.get_active_sessions(["Casse"], [])) == {}

    def test_archive_session_http_error(self, chabanas, sync):
        assert sync(chabanas.archive_session("INCONNU")) is False

    def test_get_game_list_http_error(self, chabanas, sync):
        assert sync(chabanas.get_game_list(["Casse"])) is False

    def test_body_that_is_not_json(self, chabanas, sync):
        assert sync(chabanas._post_json("/not-json", {})) is False

    def test_response_without_the_session_field(self, chabanas):
        assert chabanas._extract_session({"unexpected": True}) is False
        assert chabanas._extract_session({}) is False
        assert chabanas._extract_session("not a dict") is False

    def test_create_session_without_a_session_code(self, chabanas, sync):
        assert sync(chabanas._post_json("/no-session-field", {})) == {"unexpected": True}

    def test_connection_refused(self, sync):
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = "http://127.0.0.1:1"  # nothing listens there

        assert sync(instance.get_game_list(["Waterloo"])) is False

    def test_unresolvable_host(self, sync):
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = "http://not-a-real-host.invalid"

        assert sync(instance.get_game_list(["Waterloo"])) is False


class TestTimeout:
    def test_timeout_resolves_instead_of_hanging(self, serve, sync):
        """
        requests.post had no timeout and ran on the reactor thread, so a slow or
        dead back-end froze the entire service.
        """
        instance = Chabanas(LOGGER, timeout=1.0)
        instance.host_url = serve(SilentBackend())

        started = threading.Event()
        finished = threading.Event()
        captured = {}

        def run():
            instance.get_game_list(["Waterloo"]).addBoth(
                lambda result: (captured.update(value=result), finished.set())
            )
            started.set()

        reactor.callFromThread(run)
        assert started.wait(5)
        assert finished.wait(15), "le timeout n'a pas declenche"
        assert captured["value"] is False


class FlakyBackend(resource.Resource):
    """Answers 503 to the first `failures` requests, then behaves like Backend."""

    isLeaf = True

    def __init__(self, failures):
        super().__init__()
        self.failures = failures
        self.calls = 0

    def render_POST(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            request.setResponseCode(503)
            return b"{}"
        return Backend().render_POST(request)


class TestReadiness:
    """Tourtoirac must not listen for players before Chabanas answers."""

    def test_ready_when_chabanas_answers(self, chabanas, on_reactor):
        assert on_reactor(chabanas.is_ready) is True

    def test_not_ready_on_an_http_error(self, serve, on_reactor):
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = serve(FlakyBackend(failures=1))

        assert on_reactor(instance.is_ready) is False

    def test_not_ready_when_nothing_listens(self, on_reactor):
        """A network failure resolves to False, not to a (status, body) tuple."""
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = "http://127.0.0.1:1"

        assert on_reactor(instance.is_ready) is False

    def test_waits_until_chabanas_answers(self, serve, on_reactor):
        backend = FlakyBackend(failures=2)
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = serve(backend)

        assert on_reactor(instance.wait_until_ready, 5, 0.05) is True
        assert backend.calls == 3

    def test_gives_up_after_max_retries(self, serve, on_reactor):
        backend = FlakyBackend(failures=100)
        instance = Chabanas(LOGGER, timeout=2.0)
        instance.host_url = serve(backend)

        assert on_reactor(instance.wait_until_ready, 3, 0.05) is False
        assert backend.calls == 3

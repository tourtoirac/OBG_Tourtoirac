"""
Internal HTTP API of the administration of OBG: the Chabanas /manage/ pages
ask here who is connected to each session, and close or delete one while
people are in it. Tourtoirac holds the live connections, Chabanas only the
stored sessions.

Listens on ADMIN_PORT, on the Docker network only (the port is not published).
Every request carries the secret shared with Chabanas (OBG_ADMIN_SECRET) in
the X-OBG-Admin-Secret header; without that variable the API is not started.

    GET  /admin/presence                    {sessions: Lobby.presence()}
    POST /admin/close   {"key": ...}        {handled}: False when the session is
                                            not in memory (Chabanas archives it)
    POST /admin/delete  {"key": ...}        {handled}: False when not in memory
    POST /admin/check   {"game_json": ...}  {problems: [...]}: what keeps that game_json
                                            from being played (game_check.py), empty
                                            when it can be; for the prototypes of Chabanas
"""
import hmac
import json
import os

from twisted.internet import defer
from twisted.web import resource, server

from game_check import check_game_json

ADMIN_PORT = 9001
SECRET_HEADER = b"x-obg-admin-secret"


def admin_secret() -> str:
    return os.environ.get("OBG_ADMIN_SECRET", "")


class AdminResource(resource.Resource):
    isLeaf = True

    def __init__(self, lobby, logger, secret: str):
        super().__init__()
        self.lobby = lobby
        self.logger = logger
        self.secret = secret.encode("utf-8")

    def _authorized(self, request) -> bool:
        given = request.getHeader(SECRET_HEADER)
        given = given.encode("utf-8") if isinstance(given, str) else (given or b"")
        return bool(self.secret) and hmac.compare_digest(given, self.secret)

    @staticmethod
    def _answer(request, status: int, body: dict) -> bytes:
        request.setResponseCode(status)
        request.setHeader(b"content-type", b"application/json")
        return json.dumps(body).encode("utf-8")

    def _finish(self, request, status: int, body: dict) -> None:
        # Chabanas may have given up waiting: nobody to answer any more
        if request.finished or request._disconnected:
            return
        request.write(self._answer(request, status, body))
        request.finish()

    def render_GET(self, request):
        if not self._authorized(request):
            return self._answer(request, 403, {"error": "forbidden"})
        if request.path == b"/admin/presence":
            return self._answer(request, 200, {"sessions": self.lobby.presence()})
        return self._answer(request, 404, {"error": "not_found"})

    def render_POST(self, request):
        if not self._authorized(request):
            return self._answer(request, 403, {"error": "forbidden"})
        try:
            body = json.loads(request.content.read() or b"{}")
        except ValueError:
            body = None
        if request.path == b"/admin/check":
            if not isinstance(body, dict) or "game_json" not in body:
                return self._answer(request, 400, {"error": "missing_game_json"})
            return self._answer(request, 200, {"problems": check_game_json(body["game_json"])})
        if not isinstance(body, dict) or not body.get("key"):
            return self._answer(request, 400, {"error": "missing_key"})
        key = str(body["key"])
        if request.path == b"/admin/delete":
            return self._answer(request, 200, {"handled": self.lobby.admin_delete(key)})
        if request.path != b"/admin/close":
            return self._answer(request, 404, {"error": "not_found"})

        def closed(result):
            handled, reason = result
            # in memory but not archived: the closure was refused, Chabanas must not go on
            if reason is not None:
                self._finish(request, 502, {"error": reason})
            else:
                self._finish(request, 200, {"handled": handled})

        def failed(failure):
            self.logger.error(f"[ADMIN] Closing session {key} failed: {failure.getErrorMessage()}")
            self._finish(request, 500, {"error": "close_failed"})

        defer.maybeDeferred(self.lobby.admin_close, key).addCallbacks(closed, failed)
        return server.NOT_DONE_YET


def start_admin_api(reactor, lobby, logger) -> bool:
    """Listens for the administration of OBG, when Chabanas shares a secret with us."""
    secret = admin_secret()
    if not secret:
        logger.warning("[ADMIN] OBG_ADMIN_SECRET is not set: the administration API is not started")
        return False
    reactor.listenTCP(ADMIN_PORT, server.Site(AdminResource(lobby, logger, secret)))
    logger.info(f"[ADMIN] Administration API started on port {ADMIN_PORT}")
    return True

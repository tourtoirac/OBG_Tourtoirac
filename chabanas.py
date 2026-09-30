import json

from twisted.internet import defer, reactor
from twisted.web.client import Agent, readBody
from twisted.web.http_headers import Headers
from twisted.web.iweb import IBodyProducer
from zope.interface import implementer

from user import User


@implementer(IBodyProducer)
class BytesBodyProducer:
    """
    Minimal IBodyProducer sending an in-memory payload.
    Agent.request() only accepts a bodyProducer, and BytesProducer is not
    available on every Twisted version supported by this service.
    """

    def __init__(self, body: bytes):
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        consumer.write(self.body)
        return defer.succeed(None)

    def pauseProducing(self):
        pass

    def stopProducing(self):
        pass

    def resumeProducing(self):
        pass


class Chabanas:
    def __init__(self, logger, agent=None, timeout=10.0):
        self.logger = logger
        self.host_url = "http://obg-chabanas:80" # NOSONAR
        self.timeout = timeout
        self._agent = agent

    def _get_agent(self):
        if self._agent is None:
            self._agent = Agent(reactor, connectTimeout=self.timeout)
        return self._agent

    def _post_json(self, path, payload):
        """
        Asynchronously POSTs payload as JSON to the back-end.
        Fires with the decoded response body, or False on any failure
        (network error, timeout, undecodable body) so that callers keep
        a simple truthiness check without risking an unhandled Failure.
        """
        url = f"{self.host_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = Headers({
            b"Content-Type": [b"application/json"],
            b"Accept": [b"application/json"],
        })
        deferred = self._get_agent().request(
            b"POST",
            url.encode("utf-8"),
            headers,
            BytesBodyProducer(body),
        )
        deferred.addTimeout(self.timeout, reactor)
        deferred.addCallback(self._decode_response)
        deferred.addErrback(self._log_failure, f"POST {url}")
        return deferred

    def _decode_response(self, response):
        self.logger.debug(f"Chabanas response: {response.code}")
        body = readBody(response)
        body.addCallback(lambda raw: json.loads(raw.decode("utf-8")))
        body.addErrback(self._log_failure, "Reading chabanas response body")
        return body

    def _log_failure(self, reason, context):
        self.logger.error(f"[CHABANAS] {context} failed: {reason}")
        return False

    def _extract_session(self, payload, default=False):
        session = payload.get('session') if isinstance(payload, dict) else None
        if not session:
            self.logger.error("[CHABANAS] Response has no 'session' field")
            return default
        return session

    @defer.inlineCallbacks
    def create_session(
            self,
            game_name: str,
            user: User,
            key: str,
            allows_watchers: bool,
            session_min_players: int | None,
            session_max_players: int | None,
            access_key: str | None = None,
            variant_name: str | None = None
    ):
        # Try creating a session
        created = yield self._post_json("/session/create", {
            "game_name": game_name,
            "nickname": user.name,
            "key": key,
            "allows_watchers": allows_watchers,
            "session_min_players": session_min_players,
            "session_max_players": session_max_players,
            "access_key": access_key,
            "variant_name": variant_name,
        })
        if not created:
            return False

        session_code = created.get("session_code")
        if not session_code:
            self.logger.error("[CHABANAS] Response has no 'session_code' field")
            return False

        session_info = yield self._post_json("/session/get", {
            "session_code": session_code,
            "sat_list": ["game_json"],
        })
        if not session_info:
            return False

        return self._extract_session(session_info)

    @defer.inlineCallbacks
    def join_session(self, session_code: str, user: User, key: str):
        # Call back-end to find out if user can join a session
        response = yield self._post_json("/session/join", {
            "session_code": session_code,
            "nickname": user.name,
            "key": key
        })
        if not response:
            return False
        # /session/join returns the session object at the root of the response,
        # while /session/get and /session/get_archive wrap it in {"session": ...}
        session = response.get('session', response)
        if not isinstance(session, dict) or 'key' not in session:
            self.logger.error("[CHABANAS] Response has no session description")
            return False
        return session

    @defer.inlineCallbacks
    def get_active_sessions(self, game_name_list, sat_list):
        self.logger.debug("Getting lobby active sessions")
        response = yield self._post_json("/session/list", {
            "game_name_list": game_name_list,
            "sat_list": sat_list
        })
        if not response:
            return {}
        return response.get('sessions', {})

    @defer.inlineCallbacks
    def get_session_info(self, session_code: str):
        response = yield self._post_json("/session/get", {
            "session_code": session_code,
            "sat_list": ["game_json"]
        })
        if not response:
            return False
        return self._extract_session(response)

    @defer.inlineCallbacks
    def update_session_state(self, session_key: str, game_json: dict):
        """
        Stores the current situation of a session, so it can be resumed later on.
        :return: True when Chabanas acknowledged the update
        """
        response = yield self._post_json("/session/update", {
            "key": str(session_key),
            "game_json": game_json,
        })
        return bool(response)

    @defer.inlineCallbacks
    def get_game_list(self, game_name_list: list):
        response = yield self._post_json("/game/list", {
            "game_name_list": game_name_list,
        })
        if not response:
            return False
        game_list = response.get('game_list')
        if not game_list:
            self.logger.error("[CHABANAS] Response has no 'game_list' field")
            return False
        return game_list

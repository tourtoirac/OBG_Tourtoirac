import json

from twisted.internet import defer, reactor, task
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

    def _post_json(self, path, payload, with_status=False):
        """
        Asynchronously POSTs payload as JSON to the back-end.
        Fires with the decoded response body, or False on any failure
        (network error, timeout, undecodable body) so that callers keep
        a simple truthiness check without risking an unhandled Failure.

        With with_status=True it fires with (status, body) instead, so a caller
        that must tell a refusal from a success can look at the status code.
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
        if with_status:
            deferred.addCallback(self._decode_response_with_status)
        else:
            deferred.addCallback(self._decode_response)
        deferred.addErrback(self._log_failure, f"POST {url}")
        return deferred

    def _decode_body(self, response):
        """The decoded body, or False if it is not JSON (an error page)."""
        body = readBody(response)
        body.addCallback(lambda raw: json.loads(raw.decode("utf-8")))
        body.addErrback(self._log_failure, "Reading chabanas response body")
        return body

    def _decode_response(self, response):
        self.logger.debug(f"Chabanas response: {response.code}")
        return self._decode_body(response)

    def _decode_response_with_status(self, response):
        self.logger.debug(f"Chabanas response: {response.code}")
        body = self._decode_body(response)
        body.addCallback(lambda decoded: (response.code, decoded))
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
    def join_session(self, session_code: str, user: User, key: str, access_key: str = "", role: str = "player"):
        """
        Asks Chabanas whether that user may join. Chabanas alone knows the
        session's access_key and the key of every nickname, so it is the only
        place where the decision is made.

        :return: (session_info, None) on success, (None, reason) otherwise.
        """
        status, response = yield self._post_json("/session/join", {
            "session_code": session_code,
            "nickname": user.name,
            "key": key,
            "access_key": access_key,
            "role": role
        }, with_status=True)

        if status == 400:
            # role refuse par Chabanas : aucune ligne Player n'a ete creee
            return None, "invalid_role"
        if status == 401:
            # access_key fausse, ou key différente de celle de ce pseudo
            return None, "access_key_incorrect"
        if status == 403:
            # les spectateurs ne sont pas autorisés sur cette partie
            return None, "watchers_not_allowed"
        if status == 409:
            return None, "session_unavailable"
        if not response:
            return None, "Unable to join session"
        # /session/join returns the session object at the root of the response,
        # while /session/get and /session/get_archive wrap it in {"session": ...}
        session = response.get('session', response)
        if not isinstance(session, dict) or 'key' not in session:
            self.logger.error("[CHABANAS] Response has no session description")
            return None, "Unable to join session"
        return session, None

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
    def archive_session(self, session_key: str):
        """
        Archives a session for good: it leaves the lobby and nobody can join it
        anymore. The players stay attached to it in the back-end, so the
        archived game still lists who was sitting at the table.

        with_status is required: /session/archive answers with the plain text
        "Success", which the JSON body reader cannot decode.
        :return: True when Chabanas acknowledged the archive
        """
        status, _ = yield self._post_json("/session/archive", {
            "key": str(session_key),
        }, with_status=True)
        return status == 200

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

    @defer.inlineCallbacks
    def is_ready(self):
        """
        Chabanas repond-il ? On interroge /game/list avec une liste vide : la
        route ne touche a aucun jeu, mais elle traverse Django et la base, ce
        qu'une simple connexion TCP ne prouverait pas.
        :return: True quand Chabanas a repondu 200
        """
        result = yield self._post_json("/game/list", {
            "game_name_list": [],
        }, with_status=True)
        # un echec reseau se resout en False, pas en (status, body)
        if not isinstance(result, tuple):
            return False
        status, _ = result
        return status == 200

    @defer.inlineCallbacks
    def wait_until_ready(self, max_retries: int, retry_interval: float):
        """
        Attend que Chabanas reponde avant que le serveur n'accepte des joueurs :
        sans lui, aucune partie ne peut etre listee, creee ni rejointe.
        :param max_retries: nombre de tentatives avant d'abandonner
        :param retry_interval: secondes entre deux tentatives
        :return: True quand Chabanas repond, False apres max_retries echecs
        """
        for attempt in range(1, max_retries + 1):
            ready = yield self.is_ready()
            if ready:
                self.logger.info(f"[CHABANAS] {self.host_url} is ready")
                return True
            self.logger.info(
                f"[CHABANAS] Waiting for {self.host_url} "
                f"(attempt {attempt}/{max_retries})"
            )
            if attempt < max_retries:
                yield task.deferLater(reactor, retry_interval, lambda: None)
        return False

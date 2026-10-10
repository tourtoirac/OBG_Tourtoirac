import json
import logging
import sys

from autobahn.twisted.websocket import (
    WebSocketServerProtocol,
    WebSocketServerFactory,
    listenWS,
)
from autobahn.websocket.protocol import WebSocketProtocol
from twisted.internet import reactor, task
from twisted.internet.defer import Deferred

from chabanas import Chabanas
from handle_message import list_sessions, create_session, join_session, resume_session, close_session, start_session, list_game, acquire, release, pick, move, roll, roll_pool, rotate, flip, fix_positions, apply_setup, increment, decrement
from lobby import Lobby
from user import User

from twisted.internet import reactor

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# attente de Chabanas au demarrage : 30 tentatives espacees de 2 s
CHABANAS_MAX_RETRIES = 30
CHABANAS_RETRY_INTERVAL = 2.0

class GameWebSocketProtocol(WebSocketServerProtocol):
    def onConnect(self, request):
        logger.info(f"New connection : {request.peer}")
        user = User(
            name="Anonymous",
            protocol=self
        )

        # Add user to lobby
        self.factory.lobby.add_user(user)

    def onOpen(self):
        logger.info("WebSocket connection opened")

    def onMessage(self, payload, is_binary):
        if is_binary:
            self.send_error(
                "binary_not_supported",
                "Only JSON text messages are supported"
            )
            return

        try:
            message = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(
                "invalid_json",
                "Invalid JSON message"
            )
            return
        if 'action' in message.keys() and message['action'] not in ['move']:
            logger.debug(f"Received message : {message}")
        try:
            self.handle_message(message)
        except Exception as error:
            logger.error(f"[ON_MESSAGE] Unable to process message: {error}", exc_info=True)
            self.send_error(
                "internal_error",
                "Unable to process the message"
            )

    def handle_message(self, message):
        if not isinstance(message, dict):
            self.send_error(
                "invalid_message",
                "The message must be a JSON object"
            )
            return

        action = message.get("action")
        result = None
        match action:
            case "list_game": # Gets the initialization values of the games
                result = list_game(self, self.factory.lobby.logger, message)
            case "list_sessions": # get list of available sessions in lobby
                result = list_sessions(self, self.factory.lobby.logger, message)
            case "create_session": # Creates a new session for a game
                result = create_session(self, self.factory.lobby.logger, message)
            case "join_session": # join an active session
                result = join_session(self, self.factory.lobby.logger, message)
            case "resume_session": # rebind a new connection to an already joined session
                result = resume_session(self, self.factory.lobby.logger, message)
            case "close_session": # the owner archives the game and everyone else watches
                result = close_session(self, self.factory.lobby.logger, message)
            case "start_session": # a player starts the game before every seat is taken
                result = start_session(self, self.factory.lobby.logger, message)
            case "acquire": # associate a component to a user
                acquire(self, self.factory.lobby.logger, message)
            case "release": # release a component from a user
                release(self, self.factory.lobby.logger, message)
            case "pick": # a player takes a random token out of a bag
                pick(self, self.factory.lobby.logger, message)
            case "move": # change the x and y value of a component
                move(self, self.factory.lobby.logger, message)
            case "roll": # players launch a dice and everyone sees the new face
                roll(self, self.factory.lobby.logger, message)
            case "roll_pool": # players launch every dice of a dice pool at once
                roll_pool(self, self.factory.lobby.logger, message)
            case "rotate": # a player turns an orientable token, everyone sees the new angle
                rotate(self, self.factory.lobby.logger, message)
            case "increment": # a player raises a counter, everyone sees the new value
                increment(self, self.factory.lobby.logger, message)
            case "decrement": # a player lowers a counter, everyone sees the new value
                decrement(self, self.factory.lobby.logger, message)
            case "flip": # a player turns a counter over, everyone sees the new face
                flip(self, self.factory.lobby.logger, message)
            case "fix_positions": # players restore the green border on the counters
                result = fix_positions(self, self.factory.lobby.logger, message)
            case "apply_setup": # the game setup lands once every component is placed
                result = apply_setup(self, self.factory.lobby.logger, message)
            case _:
                self.send_error(
                    "unknown_action",
                    f"Unknown action: {action}"
                )
                return

        if isinstance(result, Deferred):
            result.addErrback(self.log_handler_error, action)

    def log_handler_error(self, failure, action):
        logger.error(f"[HANDLER] Action '{action}' failed: {failure}")

    def is_connection_open(self):
        """
        Tells whether the WebSocket session is usable to send a message.
        autobahn tracks the session in self.state and raises Disconnected from
        sendMessage() when it is not STATE_OPEN.
        """
        return self.state == WebSocketProtocol.STATE_OPEN

    def send_error(self, code, message):
        if not self.is_connection_open():
            return
        payload = {
            "event": "error",
            "error": {
                "code": code,
                "message": message
            }
        }
        encoded = json.dumps(payload).encode("utf-8")
        self.sendMessage(
            encoded,
            isBinary=False
        )

    def onClose(self, was_clean, code, reason):
        logger.info(f"Closed connection : clean={was_clean}, code={code}, reason={reason}")
        # shutdown() clears the user registry before the close handshakes
        # complete, so the user may already be gone at this point.
        user = self.factory.lobby.get_user(self)
        if user is None:
            return
        logger.info("[LOBBY] Deleting user")
        self.factory.lobby.delete_user(
            user
        )

class GameWebSocketFactory(WebSocketServerFactory):
    protocol = GameWebSocketProtocol
    def __init__(self, url, lobby):
        super().__init__(url)
        self.lobby = lobby


def create_lobby(logger, chabanas):
    lobby = Lobby(logger, chabanas)
    return lobby


def start_server(lobby):
    # WebSocket init
    factory = GameWebSocketFactory(
        "ws://0.0.0.0:9000",
        lobby
    )
    listenWS(factory)
    logger.info("WebSocket server started on ws://0.0.0.0:9000")

    keep_alive_loop = task.LoopingCall(
        lobby.send_keep_alive
    )

    keep_alive_loop.start(30.0)

    # Graceful shutdown : on SIGTERM/SIGINT, the Twisted reactor stops,
    # notify all users and disconnect them before the process exits
    reactor.addSystemEventTrigger(
        'before',
        'shutdown',
        lobby.shutdown
    )
    logger.info("[SERVER] Shutdown handler registered")


def main():
    # Lobby creation
    chabanas = Chabanas(logger)
    lobby = create_lobby(logger, chabanas)
    logger.info("[SERVER] Lobby created")

    exit_code = 0

    def on_chabanas_checked(ready):
        nonlocal exit_code
        if ready:
            start_server(lobby)
            return
        # sans Chabanas aucune partie ne se liste, ne se cree ni ne se rejoint :
        # on refuse de demarrer plutot que d'accepter des joueurs pour rien
        logger.error(
            f"[SERVER] Chabanas did not answer after {CHABANAS_MAX_RETRIES} "
            f"attempts, giving up"
        )
        exit_code = 1
        reactor.stop()

    # le WebSocket n'ecoute qu'une fois Chabanas joignable
    def wait_for_chabanas():
        deferred = chabanas.wait_until_ready(CHABANAS_MAX_RETRIES, CHABANAS_RETRY_INTERVAL)
        # une erreur inattendue ne doit pas laisser le processus en vie sans
        # ecouter : elle vaut un Chabanas injoignable
        deferred.addErrback(chabanas._log_failure, "[SERVER] Waiting for Chabanas")
        deferred.addCallback(on_chabanas_checked)

    reactor.callWhenRunning(wait_for_chabanas)
    reactor.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
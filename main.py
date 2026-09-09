import json
import logging

from autobahn.twisted.websocket import (
    WebSocketServerProtocol,
    WebSocketServerFactory,
    listenWS,
)
from twisted.internet import reactor, task

from chabanas import Chabanas
from handle_message import list_sessions, create_session, join_session, list_game
from lobby import Lobby
from user import User

from twisted.internet import reactor

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
        logger.debug(f"Received message : {message}")
        self.handle_message(message)

    def handle_message(self, message):
        action = message.get("action")
        match action:
            case "list_game": # Gets the initialization values of the games
                list_game(self, self.factory.lobby.logger, message)
            case "list_sessions": # get list of available sessions in lobby
                list_sessions(self, self.factory.lobby.logger, message)
            case "create_session": # Creates a new session for a game
                create_session(self, self.factory.lobby.logger, message)
            case "join_session": # join an active session
                join_session(self, self.factory.lobby.logger, message)
            case _:
                self.send_error(
                    "unknown_action",
                    f"Unknown action: {action}"
                )

    def send_error(self, code, message):
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
        user = self.factory.lobby.get_user(self)
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


def main():
    # Lobby creation
    chabanas = Chabanas(logger)
    lobby = create_lobby(logger, chabanas)
    logger.info(f"[SERVER] Lobby created")

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
    reactor.run()


if __name__ == "__main__":
    main()
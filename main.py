import json
import logging
import requests

from autobahn.twisted.websocket import (
    WebSocketServerProtocol,
    WebSocketServerFactory,
    listenWS,
)

from lobby import Lobby
from user import User

from twisted.internet import reactor

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
            case "join":
                self.handle_join(message)
            case _:
                self.send_error(
                    "unknown_action",
                    f"Unknown action: {action}"
                )

    def handle_join(self, message):
        user = self.factory.lobby.get_user(self)
        game_id = message.get("game_id")
        if game_id is None:
            self.send_error(
                "missing_game_id",
                "The join message requires a game_id"
            )
            return
        success, error = self.factory.lobby.join_game(
            user,
            game_id
        )

        if not success:
            self.send_error(
                error,
                f"Unable to join game {game_id}"
            )
            return
        logger.debug(f"{user.name} joined game {game_id}")

        user.send({
            "event": "joined",
            "game": {
                "id": user.game.id,
                "users": [
                    {
                        "id": user.id,
                        "name": user.name
                    }
                    for user in user.table.users
                ]
            }
        })

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
        logger.info(f"[LOBBY] Deleting user : {user.name}")
        self.factory.lobby.delete_user(
            user
        )

class GameWebSocketFactory(WebSocketServerFactory):
    protocol = GameWebSocketProtocol
    def __init__(self, url, lobby):
        super().__init__(url)
        self.lobby = lobby


def create_lobby():
    lobby = Lobby()
    return lobby


def main():
    # Lobby creation
    lobby = create_lobby()
    logger.info(f"[SERVER] Lobby created : {lobby.return_lobby_json()}")

    # WebSocket init
    factory = GameWebSocketFactory(
        "ws://0.0.0.0:9000",
        lobby
    )
    listenWS(factory)
    logger.info("WebSocket server started on ws://0.0.0.0:9000")
    reactor.run()


if __name__ == "__main__":
    logger.info("Starting server...")
    main()
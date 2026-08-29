import asyncio
import json

import websockets

WEBSOCKET_URL = "ws://localhost:12201"


async def main():
    async with websockets.connect(WEBSOCKET_URL) as websocket:
        # message = {
        #     "action": "start_game",
        #     "game_name": "waterloo",
        #     "player": "Chins",
        #     "key": "123456789",
        # }
        message = {
            "action": "get_lobby",
        }

        # Envoi du message
        await websocket.send(json.dumps(message))
        print("Message envoyé :", message)

        # Attente de la réponse
        response = await websocket.recv()
        print("Réponse du serveur :", response)

        # Attente jusqu'à Ctrl+C
        print("En attente... (Ctrl+C pour quitter)")
        await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt du client.")
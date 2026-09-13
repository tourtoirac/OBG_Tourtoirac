import asyncio
import json

from datetime import datetime, timedelta

import websockets

WEBSOCKET_URL = "ws://localhost:12201"


async def main():
    async with websockets.connect(WEBSOCKET_URL) as websocket:
        # message = {
        #     "action": "list_game",
        #     "game_name_list": ["waterloo", "diplomacy"],
        # }
        # message = {
        #     "action": "list_sessions",
        #     "game_name_list": ["waterloo", "diplomacy"],
        # }
        message = {
            "action": "create_session",
            "game_name": "waterloo",
            "nickname": f"chins_{datetime.now().strftime('%H%M%S')}",
            "key": "toto",
        }
        # message = {
        #     "action": "join_session",
        #     "session_code": "WKL1A87E",
        #     "nickname": "Chins 002",
        #     "key": "123456",
        #     "role": "player"
        # }

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
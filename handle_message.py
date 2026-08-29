def get_lobby(self, logger, message, chabanas):
    logger.debug("Process get_lobby message")
    user = self.factory.lobby.get_user(self)
    lobby_info = self.factory.lobby.return_json(sat_list=[])
    user.send(
        {
            "event": "lobby_info_plop",
            "content": lobby_info
        }
    )


def join_game(self, logger, message):
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


def start_game(self, logger, message):
    user = self.factory.lobby.get_user(self)
    required_fields = ["game_name", "player", "key"]
    for required_field in required_fields:
        if required_field not in message:
            self.send_error(
                "missing_field",
                f"The start message requires a {required_field} field"
            )
            return
    game_name = message["game_name"]
    user.name = message["player"]
    key = message["key"]

    if game_name is None:
        self.send_error(
            "missing_game_name",
            "The start message requires a game_name"
        )
        return
    success, error = self.factory.lobby.create_game(
        game_name,
        user,
        key
    )

    if not success:
        self.send_error(
            error,
            f"Unable to join game {game_name}"
        )
        return
    logger.debug(f"{user.name} joined game {game_name}")

    user.send({
        "event": "joined",
        "game": {
            "key": user.game.key,
            "game_json": user.game.game_json,
            "users": [
                {
                    "id": user.id,
                    "name": user.name
                }
                for user in user.game.players
            ]
        }
    })

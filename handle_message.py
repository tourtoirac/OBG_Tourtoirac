from session import Session

def list_sessions(self, logger, message):
    logger.debug("Process list_sessions message")
    user = self.factory.lobby.get_user(self)
    if 'game_name_list' not in message or not isinstance(message['game_name_list'], list):
        self.send_error(
            "missing_field",
            "The list_sessions message requires a game_name_list list field"
        )
        return

    sessions_info = self.factory.lobby.return_active_sessions(message['game_name_list'], sat_list=[])
    user.send(
        {
            "event": "sessions_info",
            "content": sessions_info
        }
    )

def create_session(self, logger, message):
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

    success, error = self.factory.lobby.create_session(
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
        "event": "session_created",
        "session": {
            "key": user.session.key,
            "game_json": user.session.game_json,
            "users": [
                {
                    "id": user.id,
                    "name": user.name
                }
                for user in user.session.players
            ]
        }
    })

def join_session(self, logger, message):
    user = self.factory.lobby.get_user(self)
    required_fields = ["session_code", "nickname", "key", "role"]
    for required_field in required_fields:
        if required_field not in message:
            self.send_error(
                "missing_field",
                f"The start message requires a {required_field} field"
            )
            return

    session_code = message["session_code"]
    user.name = message["nickname"]
    player_key = message["key"]
    role = message["role"]

    session = None
    logger.debug(f"Trying to find opened session with code {session_code}")
    for current_session in self.factory.lobby.sessions:
        if current_session.code == session_code:
            session = current_session
            break

    if session is None:
        logger.debug(f"Session with code {session_code} not found. Checking if it can be started")
        session, error = self.factory.lobby.start_session(
            session_code,
            user,
            role,
            player_key,
        )

    if not isinstance(session, Session):
        self.send_error(
            error,
            f"Unable to join session {session_code}"
        )
        return



    logger.debug(f"{user.name} joined session {session_code}")

    user.send({
        "event": "session_joined",
        "game": session.return_session_json()
    })


def list_game(self, _, message):
    user = self.factory.lobby.get_user(self)
    required_fields = ["game_name_list"]
    for required_field in required_fields:
        if required_field not in message:
            self.send_error(
                "missing_field",
                f"The list_game message requires a {required_field} field"
            )
            return
    game_name_list = message["game_name_list"]

    if game_name_list is None:
        self.send_error(
            "missing_game_name_list",
            "The list_game message requires a game_name_list"
        )
        return

    if not isinstance(game_name_list, list):
        self.send_error(
            "missing_game_name_list",
            "The game_name_list content can't be None"
        )
        return

    result, error = self.factory.lobby.list_game(
        game_name_list,
    )
    if not result:
        self.send_error(
            error,
            "Unable to retrieve game information"
        )
        return

    user.send(
        {
            "event": "list_game",
            "game_list": result,
        }
    )

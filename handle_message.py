from twisted.internet import defer

@defer.inlineCallbacks
def list_sessions(self, logger, message):
    logger.debug("Process list_sessions message")
    user = self.factory.lobby.get_user(self)
    if 'game_name_list' not in message or not isinstance(message['game_name_list'], list):
        self.send_error(
            "missing_field",
            "The list_sessions message requires a game_name_list list field"
        )
        return

    sessions_info = yield self.factory.lobby.return_active_sessions(message['game_name_list'], sat_list=[])
    user.send(
        {
            "event": "sessions_info",
            "content": sessions_info
        }
    )

@defer.inlineCallbacks
def create_session(self, logger, message):
    logger.debug("Process create_session message")
    user = self.factory.lobby.get_user(self)
    required_fields = ["game_name", "nickname", "key"]
    for required_field in required_fields:
        if required_field not in message:
            logger.error(f"The start message requires a {required_field} field")
            self.send_error(
                "missing_field",
                f"The start message requires a {required_field} field"
            )
            return
    game_name = message["game_name"]
    user.name = message["nickname"]
    key = message["key"]

    if game_name is None:
        self.send_error(
            "missing_game_name",
            "The start message requires a game_name"
        )
        return

    success, error = yield self.factory.lobby.create_session(
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

    user.send(
        {
            "event": "session_created",
            "session": user.session.return_session_json()
        }
    )

@defer.inlineCallbacks
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

    success, error = yield self.factory.lobby.join_session(
        session_code,
        user,
        player_key,
        role,
    )

    if not success:
        self.send_error(
            error,
            f"Unable to join session {session_code}"
        )
    else:
        logger.debug(f"{user.name} joined session {session_code} as {role}")
        user.send({
            "event": "session_joined",
            "game": user.session.return_session_json()
        })


@defer.inlineCallbacks
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

    result, error = yield self.factory.lobby.list_game(
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


def resolve_component_action(self, message, action, required_fields):
    """
    Shared preamble for the component actions (acquire, release, move):
    validates the message envelope, then resolves the caller's session and
    the targeted component.
    Returns (user, component, None) when the action can proceed, or
    (None, None, False) when an error has already been sent to the client.
    """
    for required_field in required_fields:
        if required_field not in message:
            self.send_error(
                "missing_field",
                f"The {action} message requires a {required_field} field"
            )
            return None, None, False

    user = self.factory.lobby.get_user(self)
    if user is None:
        self.send_error(
            "unknown_user",
            "No user is bound to this connection"
        )
        return None, None, False

    if user.session is None:
        self.send_error(
            "no_session",
            f"The {action} action requires an active session"
        )
        return None, None, False

    component = user.session.get_component(message["component_id"])
    if not component:
        self.send_error(
            "component_not_found",
            f"Component {message['component_id']} not found"
        )
        return None, None, False

    return user, component, True


def acquire(self, logger, message):
    logger.debug("Process acquire message")
    user, component, ready = resolve_component_action(
        self, message, "acquire", ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    acquired = component.acquire(user)
    acquire_message = {
                "event": "acquire",
                "component_id" : component_id,
                "user": user.id,
                "success": bool(acquired),
            }
    logger.debug(acquire_message)
    user.session.send(acquire_message)


def release(self, logger, message):
    logger.debug("Process release message")
    user, component, ready = resolve_component_action(
        self, message, "release", ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    released = component.release(user)
    release_message = {
                "event": "release",
                "component_id" : component_id,
                "component_json" : component.return_json(),
                "user": user.id,
                "success": bool(released),
            }
    logger.debug(release_message)
    user.session.send(release_message)


def move(self, logger, message):
    user, component, ready = resolve_component_action(
        self, message, "move", ["component_id", "x", "y"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    x = message["x"]
    y = message["y"]
    component.move(x, y, user)
    move_message = {
                "event": "move",
                "component_id" : component_id,
                "coordinates" : component.return_json(),
            }
    user.session.send_others(user, move_message)

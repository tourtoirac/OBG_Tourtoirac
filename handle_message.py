from twisted.internet import defer

from Components.token import ROTATION_STEP

# Un cran de rotation vaut ROTATION_STEP degres. Le client clique une zone et
# dit de quel bord il tourne ; il ne peut pas choisir l'angle.
ROTATE_DIRECTIONS = {
    "right": ROTATION_STEP,
    "left": -ROTATION_STEP,
}

@defer.inlineCallbacks
def list_sessions(self, logger, _):
    """
    Lists the active sessions of every active game: Chabanas owns the
    catalogue, the client does not choose the games.
    """
    logger.debug("Process list_sessions message")
    user = self.factory.lobby.get_user(self)
    sessions_info = yield self.factory.lobby.return_active_sessions(sat_list=[])
    user.send(
        {
            "event": "sessions_info",
            "content": sessions_info
        }
    )

# what the server answers when a game cannot be created
CREATE_REFUSALS = {
    "invalid_nationality": "This game requires choosing one of its nationalities",
    "duplicate_component_ids": "This game declares several components with the same id: it cannot be started",
}


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
    allows_watchers = message.get("allows_watchers", False)
    session_min_players = message.get("session_min_players", None)
    session_max_players = message.get("session_max_players", None)
    access_key = message.get("access_key", None)
    variant_name = message.get("variant_name", None)
    # only read by a game that declares nationalities; Chabanas checks it
    nationality = message.get("nationality", None)

    if game_name is None:
        self.send_error(
            "missing_game_name",
            "The start message requires a game_name"
        )
        return

    success, error = yield self.factory.lobby.create_session(
        game_name,
        user,
        key,
        allows_watchers,
        session_min_players,
        session_max_players,
        access_key,
        variant_name,
        nationality,
    )

    if not success:
        self.send_error(
            error,
            CREATE_REFUSALS.get(error, f"Unable to join game {game_name}")
        )
        return
    logger.debug(f"{user.name} joined game {game_name}")

    user.send(
        {
            "event": "session_joined",
            "session": user.session.return_session_json(),
            "role": user.role,
            "nationality": user.nationality,
            # l'interface n'affiche le lien de clôture qu'à l'owner : elle ne
            # peut le savoir que si le serveur le lui dit
            "owner": user.session.is_owner(user)
        }
    )

# ce que repond l'utilisateur quand l'adhesion est refusee, plutot qu'un
# "Unable to join session" qui ne l'aide pas a corriger sa saisie
JOIN_REFUSALS = {
    "access_key_incorrect": "Wrong access key, or that key is already used by this nickname",
    "watchers_not_allowed": "This game does not accept spectators",
    "watchers_full": "This game already has all its spectators",
    "session_unavailable": "This game is full or already started",
    "session_started": "This game has already started without you",
    "missing_field": "Missing information to join the game",
    "invalid_role": "Invalid role",
    "session_closed": "This game is closed",
    "nickname_connected": "This nickname is already playing",
    "invalid_nationality": "This game requires choosing one of its nationalities",
}


# ce que repond le serveur quand la clôture est refusee
CLOSE_REFUSALS = {
    "no_session": "You are not in a game",
    "not_session_owner": "Only the owner of the game can close it",
    "session_closed": "This game is already closed",
    "session_state_not_stored": "Unable to store the game before closing it",
    "session_not_archived": "Unable to archive the game",
}


# ce que repond le serveur quand le demarrage est refuse
START_REFUSALS = {
    "no_session": "You are not in a game",
    "watcher_not_allowed": "Only players can start the game",
    "not_session_owner": "Only the owner of the game can start it",
    "session_closed": "This game is closed",
    "session_already_started": "This game has already started",
    "session_start_not_stored": "Unable to start the game",
}


# ce que repond le serveur quand un pion ne peut pas encore etre pris en main
ACQUIRE_REFUSALS = {
    "session_not_started": "The game has not started yet",
    "players_missing": "Waiting for every player to come back",
}


@defer.inlineCallbacks
def start_session(self, logger, message):
    """
    Demarre la partie avant que tous les sieges ne soient pris, au nom de son
    proprietaire. Comme pour la cloture, l'owner est verifie ici et pas
    seulement dans l'interface : masquer un bouton n'est pas une permission.
    Une fois la partie demarree, plus aucun joueur ne peut la rejoindre, et les
    pions deviennent prenables des que tous les joueurs sont a la table.
    """
    logger.debug("Process start_session message")
    user = self.factory.lobby.get_user(self)
    if user is None or user.session is None:
        self.send_error("no_session", START_REFUSALS["no_session"])
        return

    session = self.factory.lobby.sessions.get(user.session.key)
    if session is None:
        self.send_error("no_session", START_REFUSALS["no_session"])
        return

    if user.role != "player":
        self.send_error("watcher_not_allowed", START_REFUSALS["watcher_not_allowed"])
        return

    if not session.is_owner(user):
        self.send_error("not_session_owner", START_REFUSALS["not_session_owner"])
        return

    success, error = yield self.factory.lobby.start_session(session)

    if not success:
        self.send_error(error, START_REFUSALS.get(error, "Unable to start the game"))
        return

    # la session a deja diffuse session_status a tout le monde
    logger.debug(f"{user.name} started session {session.code}")


@defer.inlineCallbacks
def close_session(self, logger, message):
    """
    Closes the game for good, on behalf of its owner: the situation is stored,
    the session is archived in the back-end, and everybody still connected
    becomes a spectator of a game that will not be resumed.

    The owner is checked here and not only in the interface: hiding a link is
    not a permission, and anyone can craft the message that hides it.
    """
    logger.debug("Process close_session message")
    user = self.factory.lobby.get_user(self)
    if user is None or user.session is None:
        self.send_error("no_session", CLOSE_REFUSALS["no_session"])
        return

    session = self.factory.lobby.sessions.get(user.session.key)
    if session is None:
        self.send_error("no_session", CLOSE_REFUSALS["no_session"])
        return

    if not session.is_owner(user):
        self.send_error("not_session_owner", CLOSE_REFUSALS["not_session_owner"])
        return

    success, error = yield self.factory.lobby.close_session(session)

    if not success:
        self.send_error(error, CLOSE_REFUSALS.get(error, "Unable to close the game"))
        return

    # la session a deja diffuse session_closed a tout le monde, l'owner compris
    logger.debug(f"{user.name} closed session {session.code}")


@defer.inlineCallbacks
def join_session(self, logger, message):
    user = self.factory.lobby.get_user(self)
    required_fields = ["session_code", "nickname", "role"]
    for required_field in required_fields:
        if required_field not in message:
            self.send_error(
                "missing_field",
                f"The start message requires a {required_field} field"
            )
            return

    session_code = message["session_code"]
    user.name = message["nickname"]
    role = message["role"]
    if role not in ("player", "watcher"):
        self.send_error("invalid_role", JOIN_REFUSALS["invalid_role"])
        return

    # la clé d'utilisateur est facultative : Chabanas décide de créer un
    # nouveau siège ou de reconnaître un joueur déjà connu
    player_key = message.get("key", "")
    access_key = message.get("access_key", "")
    # asked of a new player when the game declares nationalities; a returning
    # player keeps the one of their seat
    nationality = message.get("nationality", None)

    success, error = yield self.factory.lobby.join_session(
        session_code,
        user,
        player_key,
        role,
        access_key,
        nationality
    )

    if not success:
        # un refus d'identifiants doit etre comprenable par le joueur, sinon il
        # ne saura pas s'il doit ressaisir sa key ou son access_key
        self.send_error(error, JOIN_REFUSALS.get(error, f"Unable to join session {session_code}"))
    else:
        logger.debug(f"{user.name} joined session {session_code} as {role}")
        user.send({
            "event": "session_joined",
            "session": user.session.return_session_json(),
            "role": user.role,
            "nationality": user.nationality,
            # l'interface n'affiche le lien de clôture qu'à l'owner : elle ne
            # peut le savoir que si le serveur le lui dit
            "owner": user.session.is_owner(user)
        })


@defer.inlineCallbacks
def resume_session(self, logger, message):
    logger.debug("Process resume_session message")
    user = self.factory.lobby.get_user(self)
    if "session_key" not in message:
        self.send_error(
            "missing_field",
            "The resume_session message requires a session_key field"
        )
        return

    session_key = message["session_key"]
    # le code sert de piste a la reconstruction : la session peut avoir ete
    # videe et retiree de la memoire quand la page de jeu se reconnecte
    session_code = message.get("session_code")
    user.name = message.get("nickname") or user.name
    role = message.get("role", "player")

    success, error = yield self.factory.lobby.resume_session(
        session_key, user, role, session_code
    )

    if not success:
        self.send_error(error, f"Unable to resume session {session_key}")
        return

    logger.debug(f"{user.name} resumed session {session_key}")
    user.send({
        "event": "session_joined",
        "session": user.session.return_session_json(),
        "role": user.role,
        "nationality": user.nationality,
        "owner": user.session.is_owner(user)
    })


@defer.inlineCallbacks
def list_game(self, _, __):
    """
    Sends the description of every active game of the Chabanas catalogue.
    """
    user = self.factory.lobby.get_user(self)
    result, error = yield self.factory.lobby.list_game()
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

    # un spectateur regarde : il ne touche pas aux composants. Le refus est ici,
    # sur le serveur, pas seulement dans le client, qui peut sendsans y etre.
    if user.role != "player":
        self.send_error(
            "watcher_not_allowed",
            "Only players can act on the components of the game"
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

    # le serveur fait autorite : un client peut envoyer acquire meme quand son
    # interface ne le propose pas
    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return

    component_id = message["component_id"]
    # a token inside a bag is out of reach: it only comes out through pick,
    # which chooses it at random
    if user.session.bag_holding(component) is not None:
        self.send_error(
            "component_in_bag",
            f"Component {component_id} is inside a bag"
        )
        return

    # a token that belongs to a nationality is only taken by a player of that
    # nationality: checked here, the client only saves itself the request
    if not component.acquirable_by(user):
        self.send_error(
            "wrong_nationality",
            f"Component {component_id} belongs to {component.nationality}"
        )
        return

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
    # position de depot : le client l'envoie pour que le rectangle vert puisse
    # revenir si le jeton retrouve son emplacement initial
    bag = None
    if "x" in message and "y" in message:
        # a token released over a bag falls into it, whatever lies under the bag
        bag = user.session.bag_at(component, message["x"], message["y"])
        if bag is None:
            # on a board with a hex grid, the token is pulled onto the nearest hex
            x, y = user.session.snap_to_grid(component, message["x"], message["y"])
            component.place(x, y, user)
    released = component.release(user)
    if not released:
        bag = None
    elif bag is not None:
        # the token joins the content of the bag: it leaves the table
        user.session.put_in_bag(bag, component)
    else:
        # un pion relâché se pose au-dessus de la pile : il repasse en fin de
        # liste, l'ordre que le client dessine et que la sauvegarde conserve
        user.session.bring_to_front(component)
    release_message = {
                "event": "release",
                "component_id" : component_id,
                "component_json" : component.return_json(),
                # the bag the token fell into, None when it stays on the table
                "bag_id": bag.id if bag is not None else None,
                "user": user.id,
                "success": bool(released),
            }
    logger.debug(release_message)
    user.session.send(release_message)


def pick(self, logger, message):
    """
    Takes a token out of a bag. The client only says which bag it clicked and
    where: the server chooses the token at random, puts it back on the table
    under the pointer and in the hand of the player, as an acquire would.
    """
    logger.debug("Process pick message")
    user, component, ready = resolve_component_action(
        self, message, "pick", ["component_id"]
    )
    if not ready:
        return

    # the picked token lands in the hand of the player: same conditions as
    # taking a token from the table
    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return

    component_id = message["component_id"]
    if component.kind != 'bag':
        self.send_error(
            "component_not_a_bag",
            f"Component {component_id} is not a bag"
        )
        return

    request_id = message.get("request_id")
    token = user.session.pick_from_bag(component, user, message.get("x"), message.get("y"))
    if token is None:
        if component.components:
            # tokens are left, but they all belong to another nationality
            self.send_error(
                "wrong_nationality",
                f"Bag {component_id} holds no token this player may take"
            )
        else:
            self.send_error("bag_empty", f"Bag {component_id} is empty")
        return

    # sent to everyone: each screen takes the token out of its bag and shows
    # it in the hand of the player who picked it
    user.session.send({
        "event": "pick",
        "component_id": component_id,
        "user": user.id,
        # echoed as is: two players may pick from the same bag at once, and
        # each client must tell its own token from the other's
        "request_id": request_id if isinstance(request_id, str) else None,
        "component_json": token.return_json(),
    })


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
    # envoyé aussi a celui qui deplace : c'est lui aussi qui doit perdre
    # le rectangle vert sur son propre ecran
    user.session.send(move_message)


def roll(self, logger, message):
    logger.debug("Process roll message")
    user, component, ready = resolve_component_action(
        self, message, "roll", ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    # seuls les dés se lancent d'un clic : un jeton ou un plateau renvoyés ici
    # ne doivent pas être lancés par erreur
    if not component.clickable():
        self.send_error(
            "component_not_clickable",
            f"Component {component_id} cannot be rolled"
        )
        return

    # le délai est ici, sur le serveur : un client ne peut pas s'en affranchir
    # en ajustant son horloge, et tous les écrans voient la même chose
    if not component.is_rolling_allowed():
        self.send_error(
            "dice_cooling_down",
            f"Component {component_id} is still cooling down"
        )
        return

    src = component.roll()
    # la face est diffusée à tous, joueurs comme spectateurs : chaque écran
    # affiche le même résultat, et rejoue le délai localement
    user.session.send({
        "event": "roll",
        "component_id": component_id,
        "src": src,
        "cooldown_seconds": component.roll_cooldown(),
    })


def roll_pool(self, logger, message):
    """
    Rolls every dice of a dice_pool at once. The pool is thrown as a whole: as
    long as one of its dice is cooling down, nothing is rolled.
    """
    logger.debug("Process roll_pool message")
    user, component, ready = resolve_component_action(
        self, message, "roll_pool", ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    if component.kind != 'dice_pool':
        self.send_error(
            "component_not_a_dice_pool",
            f"Component {component_id} is not a dice pool"
        )
        return

    # the delay is enforced here, on the server, as it is for a single dice
    if not component.is_rolling_allowed():
        self.send_error(
            "dice_cooling_down",
            f"A dice of {component_id} is still cooling down"
        )
        return

    # one message for the whole throw: every screen shows all the faces at
    # the same time
    user.session.send({
        "event": "roll_pool",
        "component_id": component_id,
        "dice": component.roll(),
    })


def rotate(self, logger, message):
    """
    Fait pivoter un jeton d'un cran. Le client n'envoie que le sens du clic :
    c'est le serveur qui compte de combien ca tourne, et qui normalise l'angle.
    """
    logger.debug("Process rotate message")
    user, component, ready = resolve_component_action(
        self, message, "rotate", ["component_id", "direction"]
    )
    if not ready:
        return

    direction = message["direction"]
    # isinstance avant la comparaison : "in" sur un dict hache la cle, et une
    # liste ou un objet venu du client ferait tomber le handler
    if not isinstance(direction, str) or direction not in ROTATE_DIRECTIONS:
        self.send_error(
            "invalid_direction",
            f"Rotation direction must be one of {sorted(ROTATE_DIRECTIONS)}"
        )
        return

    component_id = message["component_id"]
    # seuls les jetons que le jeu declare orientables se tournent : un plateau
    # ou un jeton non oriente renvoyes ici ne doivent pas pivoter par erreur
    if not component.rotatable():
        self.send_error(
            "component_not_rotatable",
            f"Component {component_id} cannot be rotated"
        )
        return

    # un pion en main tourne sous la souris d'un autre joueur : on attend qu'il
    # soit pose. C'est aussi ce qu'attend le client, qui n'affiche les zones
    # que main vide.
    if getattr(component, "acquired_by", None) is not None:
        self.send_error(
            "component_held",
            f"Component {component_id} is held by a player"
        )
        return

    orientation = component.rotate(ROTATE_DIRECTIONS[direction])
    # l'angle est diffuse a tous, joueurs comme spectateurs : chaque ecran
    # affiche le meme pion, dans le meme sens
    user.session.send({
        "event": "rotate",
        "component_id": component_id,
        "orientation": orientation,
    })


# le + et le - d'un compteur : meme cliquable, deux sens. Le client ne fait que
# dire lequel des deux il a vise, c'est le serveur qui compte de combien la
# valeur bouge.
COUNTER_ACTIONS = {
    "increment": "incrementable",
    "decrement": "decrementable",
}


def change_counter_value(self, logger, message, action: str):
    """
    Fait monter ou baisser la valeur d'un compteur d'un cran, puis la diffuse a
    tous les ecrans. Le serveur fait autorite : le client ne peut pas choisir la
    nouvelle valeur, seulement demander le sens du changement.
    """
    logger.debug(f"Process {action} message")
    user, component, ready = resolve_component_action(
        self, message, action, ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    # seuls les compteurs acceptent ce changement : un plateau ou un pion
    # renvoyes ici ne doivent pas voir leur valeur bouger par erreur
    if not getattr(component, COUNTER_ACTIONS[action])():
        self.send_error(
            f"component_not_{action}able",
            f"Component {component_id} cannot be {action}ed"
        )
        return

    if action == "increment":
        value = component.increase_value()
    else:
        value = component.decrease_value()

    # la nouvelle valeur est diffusee a tous, joueurs comme spectateurs : chaque
    # ecran affiche le meme compteur, avec le meme nombre
    user.session.send({
        "event": "counter_value",
        "component_id": component_id,
        "value": value,
    })


def increment(self, logger, message):
    change_counter_value(self, logger, message, "increment")


def decrement(self, logger, message):
    change_counter_value(self, logger, message, "decrement")


def flip(self, logger, message):
    """
    Retourne un pion. Le client ne fait que le demander : c'est le serveur qui
    verifie que le pion a une seconde face, qui choisit laquelle, et qui la
    diffuse a tous les ecrans.
    """
    logger.debug("Process flip message")
    user, component, ready = resolve_component_action(
        self, message, "flip", ["component_id"]
    )
    if not ready:
        return

    component_id = message["component_id"]
    # seuls les pions qui ont une image de dos se retournent : un compteur a une
    # seule face, un plateau ou un de renvoyes ici ne changent pas d'image
    if not component.flippable():
        self.send_error(
            "component_not_flippable",
            f"Component {component_id} cannot be flipped"
        )
        return

    # un pion en main se retourne quand meme : changer de face ne le deplace
    # pas, et c'est souvent le joueur qui le tient qui veut le retourner
    component.flip()
    # la face est diffusee a tous, joueurs comme spectateurs : chaque ecran
    # montre le meme pion, sur la meme face
    user.session.send({
        "event": "flip",
        "component_id": component_id,
        "side": component.side,
        "front_src": component.image_src["front"],
        "back_src": component.image_src["back"],
    })


def fix_positions(self, logger, message):
    logger.debug("Process fix_positions message")
    user = self.factory.lobby.get_user(self)

    if user is None:
        self.send_error("unknown_user", "No user is bound to this connection")
        return

    if user.session is None:
        self.send_error(
            "no_session",
            "The fix_positions action requires an active session"
        )
        return

    if user.role != "player":
        self.send_error(
            "watcher_not_allowed",
            "Only players can fix the positions of the counters"
        )
        return

    if user.session.fix_positions_disabled():
        # masquer le bouton ne suffit pas : le client n'est pas une autorisation
        self.send_error(
            "fix_positions_disabled",
            "This game does not offer the fix positions button"
        )
        return

    components = user.session.fix_positions()
    logger.debug(f"[FIX] {user.name} fixed {len(components)} counters")
    user.session.send({
        "event": "fix_positions",
        "components": components,
    })


def apply_setup(self, logger, message):
    """
    Le client annonce que tous les composants sont charges : c'est le moment que
    le jeu attendait pour placer sa mise en place. Le serveur applique alors son
    setup, qu'il detient seul, puis diffuse le resultat a tous les ecrans. Comme
    pour "Fixe la position", le client ne choisit aucune position : il demande
    qu'on applique celles du jeu.
    """
    logger.debug("Process apply_setup message")
    user = self.factory.lobby.get_user(self)

    if user is None:
        self.send_error("unknown_user", "No user is bound to this connection")
        return

    if user.session is None:
        self.send_error(
            "no_session",
            "The apply_setup action requires an active session"
        )
        return

    if user.role != "player":
        # un spectateur ne modifie pas la partie : c'est le role que le serveur
        # a sous les yeux qui compte, pas celui de celui qui regarde
        self.send_error(
            "watcher_not_allowed",
            "Only players can install the game"
        )
        return

    if not user.session.setup:
        self.send_error("no_setup", "This game asks for no setup")
        return

    components = user.session.apply_setup()
    logger.debug(f"[SETUP] {user.name} installed {len(components)} components")
    user.session.send({
        "event": "setup",
        "components": components,
    })

import json

from twisted.internet import defer

from Components.deck import Deck
from Components.token import ROTATION_STEP
from session import MOVABLE_KINDS
from voice import MAX_SIGNAL_SIZE, ice_servers

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
            "session": user.session.return_session_json(user),
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
            "session": user.session.return_session_json(user),
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
        "session": user.session.return_session_json(user),
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


def refuse_modification(self, user, component) -> bool:
    """
    The single place that says whether a player may touch a component: take it
    in hand, turn it, flip it. Every rule that forbids an acquire belongs here,
    so that it forbids the other changes too: a player who may not take a
    token may not turn it over either.

    The server decides: a client may send the action even when its interface
    does not offer it.
    :return: True when the action is refused; the error is already sent
    """
    # the game has not started, or a seated player is away
    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return True

    # a token inside a bag is out of reach: it only comes out through pick,
    # which chooses it at random
    if user.session.bag_holding(component) is not None:
        self.send_error(
            "component_in_bag",
            f"Component {component.id} is inside a bag"
        )
        return True

    # a card inside a deck is out of reach: it only comes out through draw
    if user.session.deck_holding(component) is not None:
        self.send_error(
            "component_in_deck",
            f"Component {component.id} is inside a deck"
        )
        return True

    # a token in a private hand is off the table: only its holder plays it
    # again, through take
    if user.session.hand_holding(component) is not None:
        self.send_error(
            "component_in_hand",
            f"Component {component.id} is in the hand of a player"
        )
        return True

    # a token that belongs to a nationality is only touched by a player of
    # that nationality
    if not component.acquirable_by(user):
        self.send_error(
            "wrong_nationality",
            f"Component {component.id} belongs to {component.nationality}"
        )
        return True

    return False


def acquire(self, logger, message):
    logger.debug("Process acquire message")
    user, component, ready = resolve_component_action(
        self, message, "acquire", ["component_id"]
    )
    if not ready:
        return

    if refuse_modification(self, user, component):
        return

    component_id = message["component_id"]
    acquired = component.acquire(user)
    acquire_message = {
                "event": "acquire",
                # who did it, by nickname: the user id alone names a connection
                "nickname": user.name,
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
    deck, pile = None, None
    # hand: true asks to keep the token in the private hand of the player
    # instead of putting it down. Only a token or a card goes there.
    # A game without private hands puts the token down where it is released.
    to_hand = (
        message.get("hand") is True
        and component.kind in MOVABLE_KINDS
        and user.session.private_hand_enabled()
    )
    # to_player: the token goes to the hand of another player, released on
    # the name of that player. Asked for nobody at the table, it is refused
    # and the token stays in the mouse hand.
    receiver = user.name
    if to_hand and message.get("to_player") not in (None, user.name):
        receiver = message["to_player"]
        if not user.session.receives_hand_of(user, receiver):
            self.send_error(
                "unknown_player",
                "A token is given to another player of the game"
            )
            return
    if "x" in message and "y" in message and not to_hand:
        # a token released over a bag falls into it, whatever lies under the bag
        bag = user.session.bag_at(component, message["x"], message["y"])
        # and a card released over a pile of a deck joins that pile
        deck, pile = user.session.deck_at(component, message["x"], message["y"])
        if bag is None and deck is None:
            # on a board with a hex grid, the token is pulled onto the nearest hex
            x, y = user.session.snap_to_grid(component, message["x"], message["y"])
            component.place(x, y, user)
    released = component.release(user)
    if not released:
        bag = None
        deck, pile = None, None
        to_hand = False
    elif to_hand:
        # the token leaves the table for the hand of the player, or of the
        # player it was given to
        user.session.put_in_hand(user, component, receiver)
    elif bag is not None:
        # the token joins the content of the bag: it leaves the table
        user.session.put_in_bag(bag, component)
    elif deck is not None:
        # the card goes on top of the pile: it leaves the table
        user.session.put_in_deck(deck, pile, component)
    else:
        # un pion relâché se pose au-dessus de la pile : il repasse en fin de
        # liste, l'ordre que le client dessine et que la sauvegarde conserve
        user.session.bring_to_front(component)
    release_message = {
                "event": "release",
                # who did it, by nickname: the user id alone names a connection
                "nickname": user.name,
                "component_id" : component_id,
                "component_json" : component.return_json(),
                # the bag the token fell into, None when it stays on the table
                "bag_id": bag.id if bag is not None else None,
                # the deck and the pile the card went on, None when it stays
                # on the table; deck_json is that deck after the release
                "deck_id": deck.id if deck is not None else None,
                "pile": pile,
                "deck_json": deck.return_json() if deck is not None else None,
                # the nickname of the player whose private hand took the token,
                # None when it did not go to a hand
                "hand_of": receiver if to_hand else None,
                # what everybody sees of the private hands after this release
                "hands": user.session.hands_json(),
                "user": user.id,
                "success": bool(released),
            }
    logger.debug(release_message)
    user.session.send(release_message)


def take(self, logger, message):
    """
    Plays a token of the private hand of the player: it comes back on the
    table under the pointer, held by that player as an acquire would.
    """
    logger.debug("Process take message")
    user, component, ready = resolve_component_action(
        self, message, "take", ["component_id"]
    )
    if not ready:
        return

    # the token lands in the mouse hand of the player: same conditions as
    # taking a token from the table
    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return

    if not user.session.private_hand_enabled():
        self.send_error(
            "private_hand_disabled",
            "This game has no private hand"
        )
        return

    component_id = message["component_id"]
    was_shown = user.session.shown_by(component) == user.name
    if not user.session.take_from_hand(user, component, message.get("x"), message.get("y")):
        self.send_error(
            "component_not_in_hand",
            f"Component {component_id} is not in the hand of this player"
        )
        return

    request_id = message.get("request_id")
    # sent to everyone: each screen puts the token back on the table, in the
    # mouse hand of the player who played it
    user.session.send({
        "event": "take",
        # who did it, by nickname: the user id alone names a connection
        "nickname": user.name,
        "component_id": component_id,
        "user": user.id,
        # the nickname of the player the token came from
        "hand_of": user.name,
        "hands": user.session.hands_json(),
        # echoed as is, so the client recognises the answer to its own request
        "request_id": request_id if isinstance(request_id, str) else None,
        "component_json": component.return_json(),
    })
    # the card was shown from the hand: it leaves that area too
    if was_shown:
        user.session.send_show(user.name)


def give(self, logger, message):
    """
    Gives a token of the private hand of the player to another player: it
    goes straight into that hand, without crossing the table. Only the
    receiver reads the face of a card given.
    """
    logger.debug("Process give message")
    user, component, ready = resolve_component_action(
        self, message, "give", ["component_id"]
    )
    if not ready:
        return

    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return

    if not user.session.private_hand_enabled():
        self.send_error(
            "private_hand_disabled",
            "This game has no private hand"
        )
        return

    receiver = message.get("to")
    if not user.session.receives_hand_of(user, receiver):
        self.send_error(
            "unknown_player",
            "A token is given to another player of the game"
        )
        return

    component_id = message["component_id"]
    was_shown = user.session.shown_by(component) == user.name
    if not user.session.give_from_hand(user, component, receiver):
        self.send_error(
            "component_not_in_hand",
            f"Component {component_id} is not in the hand of this player"
        )
        return

    # sent to everyone: the counts of both hands change. for_user leaves the
    # face of a card to its new holder alone
    user.session.send({
        "event": "give",
        "nickname": user.name,
        "to": receiver,
        "component_id": component_id,
        "user": user.id,
        "hands": user.session.hands_json(),
        "component_json": component.return_json(),
    })
    # the card was shown from the hand: it leaves that area too
    if was_shown:
        user.session.send_show(user.name)


def resolve_voice_action(self, action):
    """
    Shared preamble of the voice actions (voice_join, voice_leave,
    voice_signal): only a player of a session talks.
    Returns the user when the action can proceed, or None when an error has
    already been sent to the client.
    """
    user = self.factory.lobby.get_user(self)
    if user is None:
        self.send_error(
            "unknown_user",
            "No user is bound to this connection"
        )
        return None

    if user.session is None:
        self.send_error(
            "no_session",
            f"The {action} action requires an active session"
        )
        return None

    if user.role != "player":
        self.send_error(
            "watcher_not_allowed",
            "Only players can talk"
        )
        return None

    return user


def voice_join(self, logger, _):
    """
    Puts the player in the voice channel of its session. It is told who is
    already there, with the ICE servers to reach them: its browser calls each
    of them. Everybody is told the new list of members.
    """
    logger.debug("Process voice_join message")
    user = resolve_voice_action(self, "voice_join")
    if user is None:
        return

    peers = user.session.join_voice(user)
    user.send({
        "event": "voice_joined",
        "peers": peers,
        "ice_servers": ice_servers(user.name),
    })
    user.session.send_voice_members()


def voice_leave(self, logger, _):
    """
    Takes the player out of the voice channel: the others hang up on it.
    """
    logger.debug("Process voice_leave message")
    user = resolve_voice_action(self, "voice_leave")
    if user is None:
        return

    if user.session.leave_voice(user):
        user.session.send_voice_members()


def voice_signal(self, logger, message):
    """
    Relays a WebRTC message (offer, answer or ICE candidate) to another player
    of the voice channel. Its content is the browsers' business: the server
    only checks who sends it to whom.
    """
    logger.debug("Process voice_signal message")
    user = resolve_voice_action(self, "voice_signal")
    if user is None:
        return

    session = user.session
    if user not in session.voice:
        self.send_error(
            "voice_not_joined",
            "This player is not in the voice channel"
        )
        return

    data = message.get("data")
    if not isinstance(data, dict) or len(json.dumps(data)) > MAX_SIGNAL_SIZE:
        self.send_error(
            "invalid_voice_signal",
            "A voice signal is an object of reasonable size"
        )
        return

    target = session.voice_member(message.get("to"))
    if target is None or target is user:
        self.send_error(
            "unknown_player",
            "A voice signal goes to another player of the voice channel"
        )
        return

    target.send({"event": "voice_signal", "from": user.name, "data": data})


def resolve_show_action(self, action):
    """
    Shared preamble of the actions on the area a player shows cards of its
    hand in (show_open, show_add, show_remove, show_close).
    Returns the user when the action can proceed, or None when an error has
    already been sent to the client.
    """
    user = self.factory.lobby.get_user(self)
    if user is None:
        self.send_error(
            "unknown_user",
            "No user is bound to this connection"
        )
        return None

    if user.session is None:
        self.send_error(
            "no_session",
            f"The {action} action requires an active session"
        )
        return None

    if user.role != "player":
        self.send_error(
            "watcher_not_allowed",
            "Only players can show cards of their hand"
        )
        return None

    if not user.session.show_hands_enabled():
        self.send_error(
            "show_hands_disabled",
            "This game does not let the players show their hand"
        )
        return None

    return user


def show_open(self, logger, message):
    """
    Opens the area a player shows cards of its hand in, for one other player
    or for all of them. Asked again, it changes who reads the area.
    """
    logger.debug("Process show_open message")
    user = resolve_show_action(self, "show_open")
    if user is None:
        return

    to = message.get("to")
    if not isinstance(to, str) or not user.session.open_show(user.name, to):
        self.send_error(
            "unknown_player",
            "The hand is shown to \"all\" or to another player of the game"
        )
        return

    user.session.send_show(user.name)


def show_add(self, logger, message):
    """
    Shows one more card of the hand of the player, on the side it has there.
    """
    logger.debug("Process show_add message")
    user = resolve_show_action(self, "show_add")
    if user is None:
        return

    session = user.session
    if user.name not in session.shows:
        self.send_error(
            "show_not_open",
            "This player is not showing its hand"
        )
        return

    component_id = message.get("component_id")
    component = session.get_component(component_id)
    if not component or not session.add_to_show(user.name, component):
        self.send_error(
            "component_not_in_hand",
            f"Component {component_id} is not in the hand of this player"
        )
        return

    session.send_show(user.name)


def show_remove(self, logger, message):
    """
    Takes a card back from the area of the player into its hand, where it
    stays hidden again. The other cards of the area keep their rank.
    """
    logger.debug("Process show_remove message")
    user = resolve_show_action(self, "show_remove")
    if user is None:
        return

    session = user.session
    component_id = message.get("component_id")
    component = session.get_component(component_id)
    if not component or not session.remove_from_show(user.name, component):
        self.send_error(
            "component_not_shown",
            f"Component {component_id} is not shown by this player"
        )
        return

    session.send_show(user.name)


def show_close(self, logger, _):
    """
    Closes the area of the player: the cards it showed are only in its hand
    again, back on their front.
    """
    logger.debug("Process show_close message")
    user = resolve_show_action(self, "show_close")
    if user is None:
        return

    turned = user.session.close_show(user.name)
    if turned is None:
        return
    user.session.send_show(user.name)
    # only the holder sees its hand, so only that player is told of the turn
    for component in turned:
        user.send(user.session.for_user({
            "event": "flip",
            "component_id": component.id,
            "side": component.side,
            "front_src": component.image_src["front"],
            "back_src": component.image_src["back"],
        }, user))


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
        # who did it, by nickname: the user id alone names a connection
        "nickname": user.name,
        "component_id": component_id,
        "user": user.id,
        # echoed as is: two players may pick from the same bag at once, and
        # each client must tell its own token from the other's
        "request_id": request_id if isinstance(request_id, str) else None,
        "component_json": token.return_json(),
    })


def resolve_deck_action(self, message, action):
    """
    Shared preamble of the deck actions (draw, shuffle, refill, deal): the
    caller is a player allowed to touch the table, the component is a deck or
    a stack, and the pile the message names exists on it.
    :return: (user, deck, pile), or (None, None, None) when an error has
        already been sent to the client. The pile is "draw" when the message
        names none.
    """
    user, component, ready = resolve_component_action(
        self, message, action, ["component_id"]
    )
    if not ready:
        return None, None, None

    # a deck is only handled when a token may be taken: the game has started
    # and every seated player is there
    refusal = user.session.acquire_refusal()
    if refusal is not None:
        message_text = ACQUIRE_REFUSALS[refusal]
        missing = user.session.missing_players()
        if missing:
            message_text = f"{message_text}: {', '.join(missing)}"
        self.send_error(refusal, message_text)
        return None, None, None

    component_id = message["component_id"]
    if not isinstance(component, Deck):
        self.send_error(
            "component_not_a_deck",
            f"Component {component_id} is not a deck"
        )
        return None, None, None

    pile = message.get("pile", "draw")
    if not isinstance(pile, str) or pile not in component.piles:
        self.send_error(
            "unknown_pile",
            f"Deck {component_id} has no {pile} pile"
        )
        return None, None, None

    return user, component, pile


def draw(self, logger, message):
    """
    Takes the top card of a pile of a deck, or its top cards when the message
    carries a count. The client only says which pile it clicked and where: the
    server puts each card back on the table under the pointer and in the hand
    of the player, as an acquire would.
    """
    logger.debug("Process draw message")
    user, deck, pile = resolve_deck_action(self, message, "draw")
    if user is None:
        return

    cards = user.session.draw_cards_from_deck(
        deck, pile, user, message.get("count", 1), message.get("x"), message.get("y")
    )
    if not cards:
        if deck.top(pile) is not None:
            # the top card belongs to another nationality
            self.send_error(
                "wrong_nationality",
                f"The top card of deck {deck.id} is not for this player"
            )
        else:
            self.send_error("pile_empty", f"The {pile} pile of deck {deck.id} is empty")
        return

    request_id = message.get("request_id")
    deck_json = deck.return_json()
    # sent to everyone, one event for each card: each screen shows the card in
    # the hand of the player who drew it. Its front stays on the server while
    # it lies on its back.
    for index, card in enumerate(cards):
        user.session.send({
            "event": "draw",
            # who did it, by nickname: the user id alone names a connection
            "nickname": user.name,
            "component_id": deck.id,
            "pile": pile,
            "user": user.id,
            # echoed as is, so the client recognises the answers to its own
            # request: index counts them, from 0 to count - 1
            "request_id": request_id if isinstance(request_id, str) else None,
            "index": index,
            "count": len(cards),
            "component_json": card.return_json(),
            # the deck once every card of this draw is out
            "deck_json": deck_json,
        })


def shuffle(self, logger, message):
    """
    Shuffles a pile of a deck.
    """
    logger.debug("Process shuffle message")
    user, deck, pile = resolve_deck_action(self, message, "shuffle")
    if user is None:
        return

    deck.shuffle(pile)
    user.session.send({
        "event": "shuffle",
        "component_id": deck.id,
        "pile": pile,
        "user": user.id,
        # the nickname of the player, so every screen can say who shuffled
        "nickname": user.name,
        "deck_json": deck.return_json(),
    })


def refill(self, logger, message):
    """
    Puts every card of the discard pile of a deck back into its draw pile.
    """
    logger.debug("Process refill message")
    user, deck, _ = resolve_deck_action(self, dict(message, pile="discard"), "refill")
    if user is None:
        return

    deck.refill()
    user.session.send({
        "event": "refill",
        "component_id": deck.id,
        "user": user.id,
        "nickname": user.name,
        "deck_json": deck.return_json(),
    })


def deal(self, logger, message):
    """
    Deals the top cards of a stack: one to the private hand of each player.
    Nothing is dealt unless the stack holds a card for everybody.
    """
    logger.debug("Process deal message")
    user, stack, _ = resolve_deck_action(self, message, "deal")
    if user is None:
        return

    if stack.kind != 'stack':
        self.send_error(
            "component_not_a_stack",
            f"Component {stack.id} is not a stack"
        )
        return

    session = user.session
    # the cards go to the private hands: a game without them deals nothing
    if not session.private_hand_enabled():
        self.send_error(
            "private_hand_disabled",
            "This game has no private hand"
        )
        return

    dealt = session.deal_from_stack(stack)
    if not dealt:
        self.send_error(
            "not_enough_cards",
            f"Stack {stack.id} does not hold a card for each player"
        )
        return

    deal_message = {
        "event": "deal",
        "component_id": stack.id,
        "user": user.id,
        # the nickname of the player, so every screen can say who dealt
        "nickname": user.name,
        "deck_json": stack.return_json(),
        "hands": session.hands_json(),
    }
    # each player only reads the card it was dealt, with its front: the cards
    # of the others stay unknown, down to their id
    for recipient in session.players + session.watchers:
        card = dealt.get(recipient.name) if recipient.role == "player" else None
        cards = [card.return_json()] if card is not None else []
        recipient.send(session.for_user(dict(deal_message, cards=cards), recipient))


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

    # whoever may not take the token may not turn it
    if refuse_modification(self, user, component):
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

    # a player turns over what it keeps in its private hand, whatever happens
    # on the table: nobody else sees it, so only that player is told
    if user.session.hand_holding(component) == user.name:
        component.flip()
        user.send(user.session.for_user({
            "event": "flip",
            "component_id": component_id,
            "side": component.side,
            "front_src": component.image_src["front"],
            "back_src": component.image_src["back"],
        }, user))
        # whoever reads that card in the area of the player sees it turn
        if user.session.shown_by(component) == user.name:
            user.session.send_show(user.name)
        return

    # whoever may not take the token may not turn it over
    if refuse_modification(self, user, component):
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


def order_players(self, logger, message):
    """
    The owner of the session changes the order the players are listed in.
    Every screen gets the new list with a session_status.
    """
    logger.debug("Process order_players message")
    user = self.factory.lobby.get_user(self)

    if user is None:
        self.send_error("unknown_user", "No user is bound to this connection")
        return

    session = user.session
    if session is None:
        self.send_error(
            "no_session",
            "The order_players action requires an active session"
        )
        return

    if not session.is_owner(user):
        self.send_error(
            "not_session_owner",
            "Only the owner of the session orders its players"
        )
        return

    if not session.order_players(message.get("nicknames")):
        self.send_error(
            "unknown_player",
            "The order_players message requires the nicknames of players of the game, each once"
        )
        return

    session.send(session.return_status_json())


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

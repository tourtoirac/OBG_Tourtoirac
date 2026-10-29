import json
import uuid

from twisted.internet import defer

from chabanas import Chabanas
from session import InvalidComponentError, Session, duplicate_component_ids
from user import User


class Lobby:

    def __init__(self, logger, chabanas):
        self.id = str(uuid.uuid4())
        self.sessions = {}
        self.users = {}
        self.logger = logger
        self.chabanas = chabanas

    @defer.inlineCallbacks
    def return_active_sessions(self, sat_list):
        """
        Returns a list of the active sessions of the lobby
        """
        response = yield self.chabanas.list_active(sat_list)
        active = response.get("sessions", {})
        self._mark_connected_players(active)
        self._hide_secrets(active)
        return {
            "active": active,
            # every game of the platform, Air Force included, and the limit
            "active_games": response.get("active_games"),
            "max_active_games": response.get("max_active_games"),
        }

    def _mark_connected_players(self, active: dict) -> None:
        """
        Chabanas liste tous les sieges jamais pris dans une session, connectes ou
        non. Le lobby doit savoir qui est reellement assis a la table pour ne pas
        laisser reprendre un pseudo deja en jeu. Les sessions en memoire sont le
        seul endroit qui connait les connexions vivantes.
        """
        for sessions in active.values():
            for session_info in sessions:
                live = self._find_session_by_code(session_info.get("code"))
                online = set()
                if live is not None:
                    online = {user.name for user in live.players + live.watchers}
                for player in session_info.get("players") or []:
                    player["connected"] = player.get("nickname") in online

    @staticmethod
    def _hide_secrets(active: dict) -> None:
        """
        The list goes to every browser of the lobby: the key of a session lets
        resume_session in without the checks of join_session (access_key, key of
        a game in prototyping), and the access_key is the secret itself.
        """
        for sessions in active.values():
            for session_info in sessions:
                session_info.pop("key", None)
                session_info.pop("access_key", None)

    @staticmethod
    def _owner_of(session_info: dict) -> str | None:
        """
        Lit le proprietaire dans la liste des joueurs que Chabanas vient de
        renvoyer. C'est la seule source de verite de l'ownership : c'est elle
        qui decide qui a le droit de clore une partie.
        :return: le pseudo du createur, ou None si la liste n'en dit rien
        """
        for player in session_info.get("players") or []:
            if player.get("owner"):
                return player.get("nickname")
        return None

    @staticmethod
    def _seat_names(session_info: dict) -> list:
        """
        Les pseudos des sieges que Chabanas a enregistres, connectes ou non :
        une partie commencee attend qu'ils soient tous la.
        """
        return [
            player["nickname"]
            for player in session_info.get("players") or []
            if player.get("nickname")
        ]

    @staticmethod
    def _seat_nationalities(session_info: dict) -> dict:
        """
        The nationality Chabanas stored on each seat, by nickname. Chabanas is
        the only judge of it: a player chooses once, when taking the seat.
        """
        return {
            player["nickname"]: player["nationality"]
            for player in session_info.get("players") or []
            if player.get("nickname") and player.get("nationality")
        }

    def _build_session(self, session_info):
        # a session already stored in Chabanas is rebuilt here on join and on
        # resume, without the check create_session runs: duplicated ids are
        # only logged, so the game they break can be found and fixed
        duplicates = duplicate_component_ids(session_info.get('game_json') or {})
        if duplicates:
            self.logger.error(
                f"Session {session_info.get('key')} ({session_info.get('name')}): "
                f"duplicated component ids {duplicates}"
            )
        return Session(
            user=None,
            name=session_info['name'],
            key=session_info["key"],
            code=session_info["code"],
            active=session_info["active"],
            variant=session_info["variant"],
            game_json=session_info['game_json'],
            owner_nickname=self._owner_of(session_info),
            started=session_info.get("started", False),
            player_names=self._seat_names(session_info),
            player_nationalities=self._seat_nationalities(session_info)
        )

    def _refresh_seats(self, session: Session, session_info: dict) -> None:
        """
        Une adhesion peut ajouter un siege, et le dernier siege pris demarre la
        partie : la session en memoire reprend les deux depuis Chabanas.
        """
        session.refresh_seats(
            session_info.get("started", False),
            self._seat_names(session_info),
            self._seat_nationalities(session_info)
        )

    @staticmethod
    def _send_status(session: Session) -> None:
        """
        Chaque ecran de la partie apprend si elle a commence et qui manque a la
        table : c'est ce qui autorise ou non a prendre un pion.
        """
        session.send(session.return_status_json())

    def _refresh_owner(self, session: Session, session_info: dict) -> Session:
        """
        Retient le proprietaire d'une session construite avant qu'on puisse le
        lire. Une session n'est construite que par le premier client qui la
        demande : le createur peut donc apparaitre apres la session elle-meme.
        """
        if session.owner_nickname is None:
            session.owner_nickname = self._owner_of(session_info)
        return session

    def _find_session_by_code(self, session_code):
        for session in self.sessions.values():
            if session.code == session_code:
                return session
        return None

    @defer.inlineCallbacks
    def create_session(
            self,
            game_name: str,
            user: User,
            key: str = "",
            allows_watchers: bool = True,
            session_min_players: int | None = None,
            session_max_players: int | None = None,
            access_key: str | None = None,
            variant_name: str | None = None,
            nationality: str | None = None,
            prototype_key: str = ""
    ):
        # checks if a session can be created with that user
        session_info, reason = yield self.chabanas.create_session(
            game_name,
            user,
            key,
            allows_watchers,
            session_min_players,
            session_max_players,
            access_key,
            variant_name,
            nationality,
            prototype_key
        )
        if not session_info:
            return False, reason or "Unable to create session"

        # every component must have its own id: a duplicate would overwrite
        # another one in components_dict. The game is refused, and the session
        # Chabanas has just created is archived so it does not linger in the lobby
        duplicates = duplicate_component_ids(session_info.get('game_json') or {})
        if duplicates:
            self.logger.error(
                f"Game {game_name} refused: duplicated component ids {duplicates}"
            )
            archived = yield self.chabanas.archive_session(session_info["key"])
            if not archived:
                self.logger.error(
                    f"Unable to archive refused session {session_info['key']}"
                )
            return False, "duplicate_component_ids"

        try:
            session = self._build_session(session_info)
        except InvalidComponentError as error:
            # same fate as duplicated ids: the session Chabanas has just
            # created cannot be played. The error goes on to the handler,
            # which tells the caller which component is at fault
            self.logger.error(f"Game {game_name} refused: {error}")
            archived = yield self.chabanas.archive_session(session_info["key"])
            if not archived:
                self.logger.error(
                    f"Unable to archive refused session {session_info['key']}"
                )
            raise
        self.add_session(session)

        # the platform may have filled up since Chabanas created the session
        admitted = yield self._admit_player(session)
        if not admitted:
            self.remove_session(session)
            yield self.chabanas.archive_session(session.key)
            return False, "server_full"

        success, reason = session.add_user(user, "player")
        if not success:
            self.remove_session(session)
            self._release_if_empty(session)
            return False, reason

        user.session = session
        self._send_status(session)
        return True, None

    @defer.inlineCallbacks
    def join_session(self, session_code: str, user: User, key: str = "", role: str = "player", access_key: str = "",
                     nationality: str | None = None, prototype_key: str = ""):
        self.logger.debug(f"Trying to find opened session with code {session_code}")

        # un pseudo actuellement assis a la table ne peut pas etre repris par une
        # autre connexion, meme en connaissant sa key : le siege est occupe. Une
        # place liberee (joueur deconnecte) reste reprenable, et c'est alors
        # Chabanas qui tranche avec sa key.
        sitting = self._find_session_by_code(session_code)
        if sitting is not None and sitting.find_user(user.name) is not None:
            return False, "nickname_connected"

        # Chabanas est seul juge : c'est lui qui connait l'access_key de la
        # partie et la key de chaque pseudo. On l'interroge donc toujours, meme
        # quand la session est deja chargee ici, sinon on laisserait rejoindre
        # n'importe qui avec un code devine.
        session_info, reason = yield self.chabanas.join_session(
            session_code, user, key, access_key, role, nationality, prototype_key
        )
        if not session_info:
            return False, reason

        session = self._find_session_by_code(session_code)
        if session is None:
            # premiere adhésion : la session n'est pas encore en memoire
            session = self._build_session(session_info)
            self.add_session(session)
        else:
            self.logger.debug(f"Session with code {session_code} found in lobby")
            # la session vit deja ici : son proprietaire peut ne pas etre connu
            # encore, la description fraiche de Chabanas permet de le retenir
            self._refresh_owner(session, session_info)
            # ni le siege qui vient d'etre pris, ni le demarrage qu'il a pu
            # declencher
            self._refresh_seats(session, session_info)
            # la session vit deja ici : on garde son etat (jetons deplaces) et on
            # ignore le game_json renvoie par Chabanas, qui est celui du stockage

        if role == "player":
            admitted = yield self._admit_player(session)
            if not admitted:
                self._forget_if_unused(session)
                return False, "server_full"

        session = self._kept_in_memory(session)
        success, reason = session.add_user(user, role)
        if not success:
            self._release_if_empty(session)
            return False, reason

        user.session = session
        self._notify_lobby_users("join", session, user)
        self._send_status(session)
        return True, None

    @defer.inlineCallbacks
    def resume_session(
            self,
            session_key: str,
            user: User,
            role: str = "player",
            session_code: str | None = None
    ):
        """
        Rebinds a freshly connected user to a session it already belonged to from
        another page. The lobby and the game are two distinct documents, so the
        game page opens its own WebSocket and gets a brand new User with no
        session attached. It has to claim the session again by key, otherwise
        acquire/release/move are all rejected with "no_session".

        Le passage lobby -> jeu ferme la connexion du lobby, ce qui vide la
        session et la retire de la memoire (son etat est stocke dans Chabanas).
        La page de jeu se connecte juste apres : si la session n'est plus la, on
        la reconstruit depuis l'etat stocke pour que le lancement aboutisse.
        """
        session = self.sessions.get(session_key)
        if session is None:
            session = yield self._reload_session(session_key, session_code)
        if session is None:
            return False, "session_not_found"

        # une partie closee n'est plus rejouable : Chabanas a vide son code,
        # seule une session encore en memoire pourrait la retrouver
        if session.closed:
            return False, "session_closed"

        if user.session is session:
            return True, None

        if role not in ("player", "watcher", "admin"):
            return False, "invalid_role"
        # an administrator of OBG watches any game, even one refusing watchers
        # or with all its watchers: the session key it holds comes from Chabanas
        admin = role == "admin"
        if admin:
            role = "watcher"

        # the same player may still be attached through a connection that has
        # not been torn down yet: the newest connection wins.
        previous = session.find_user(user.name)
        if previous is not None and previous is not user:
            # an administrator never takes the place of a participant of that name
            if admin and not previous.admin:
                return False, "nickname_connected"
            self.logger.debug(
                f"[RESUME] Replacing stale connection of {user.name} on session {session.code}"
            )
            session.remove_user(previous)

        # la reprise ne passe pas par Chabanas : c'est ici qu'une partie
        # commencee refuse un joueur qui n'y a pas de siege. Sans liste de
        # sieges, on ne sait pas qui en a un : on ne refuse personne.
        if (role == "player" and session.started and session.player_names
                and user.name not in session.player_names):
            return False, "session_started"

        if role == "player":
            admitted = yield self._admit_player(session)
            if not admitted:
                self._forget_if_unused(session)
                return False, "server_full"

        session = self._kept_in_memory(session)
        success, reason = session.add_user(user, role, admin=admin)
        if not success:
            self._release_if_empty(session)
            return False, reason

        user.admin = admin
        user.session = session
        if role == "player":
            self._record_launch(session)
        self._notify_lobby_users("join", session, user)
        self._send_status(session)
        return True, None

    def _kept_in_memory(self, session: Session) -> Session:
        """
        The session a user is about to enter, back in the lobby if it left it.
        Admitting a player waits for Chabanas (occupy_game): meanwhile the state
        of the session, stored when its last participant left (the page going
        from the lobby to the game), may come back and drop it from memory, as
        it is still empty. The user would then enter a session the lobby no
        longer knows, and its next action would be refused (no_session).
        :return: the session of the lobby under that key: this one, put back,
            or the one a concurrent resume rebuilt meanwhile
        """
        current = self.sessions.get(session.key)
        if current is None:
            self.logger.debug(f"[SESSION] {session.key} dropped while a user was entering it: kept in memory")
            self.add_session(session)
            return session
        return current

    def _record_launch(self, session: Session):
        """
        Dates the launch of the game in Chabanas, without holding the player
        back: a launch that cannot be dated is only logged.
        """
        deferred = self.chabanas.launch_session(session.key)
        deferred.addCallback(
            lambda launched: launched or self.logger.error(
                f"[SESSION] Unable to date the launch of session {session.key}"
            )
        )

    @defer.inlineCallbacks
    def _admit_player(self, session: Session):
        """
        A player is about to enter the session. A session with players in it is
        already counted by Chabanas; an empty one is declared, and refused when
        the platform already runs its maximum number of games in progress.
        Chabanas not answering does not hold the player back: the hourly check
        corrects the count.
        :return: False when the platform is full
        """
        if session.players:
            return True
        status = yield self.chabanas.occupy_game(session.code)
        if status is None:
            self.logger.error(f"[SESSION] Chabanas did not count session {session.code} as in progress")
        return status != 409

    def _release_if_empty(self, session: Session):
        """The last player left: the session no longer counts as in progress."""
        if not session.players and session.code:
            self.chabanas.vacate_game(session.code)

    def _forget_if_unused(self, session: Session):
        """A session rebuilt for a player who was refused, with nobody else in it."""
        if not session.players and not session.watchers:
            self.remove_session(session)

    def occupied_codes(self) -> list:
        """The codes of the sessions with players in them, for the hourly check of Chabanas."""
        return sorted(
            session.code for session in self.sessions.values()
            if session.players and session.code and not session.closed
        )

    @defer.inlineCallbacks
    def sync_occupancy(self):
        """Sends Chabanas the sessions with players in them: it checks and corrects its count."""
        codes = self.occupied_codes()
        response = yield self.chabanas.sync_occupancy(codes)
        if response:
            self.logger.info(f"[SESSION] {len(codes)} session(s) in progress checked with Chabanas: {response}")

    @defer.inlineCallbacks
    def _reload_session(self, session_key: str, session_code: str | None):
        """
        Reconstruit une session videe puis retiree de la memoire, a partir de la
        situation que Chabanas a stockee quand son dernier participant est parti.
        La cle est le secret qui prouve que l'appelant etait dans la session ; le
        code n'est qu'une piste de recherche, et les deux doivent designer la
        meme session.
        :return: la Session reconstruite, ou None si on ne la retrouve pas
        """
        if not session_code:
            return None
        session_info = yield self.chabanas.get_session_info(session_code)
        if not session_info or session_info.get("key") != session_key:
            return None
        session = self._build_session(session_info)
        self.add_session(session)
        self.logger.info(
            f"[RESUME] Session {session_key} rebuilt from Chabanas stored state"
        )
        # add_session ne remplace pas une session deja presente : deux reprises
        # concurrentes doivent partager la meme instance, pas en garder chacune
        # une copie dont une seule finirait dans le lobby
        return self.sessions.get(session_key)

    def _notify_lobby_users(self, event: str, session: Session, user: User = None):
        """
        Broadcasts a session membership change to every connected user so the
        lobby can refresh the seats available on each session.

        A closure has no acting user: the nickname it then reports is the one of
        the session owner, the only one entitled to that action.
        """
        message = {
            "event": "session_players_changed",
            "code": session.code,
            "game_name": session.name,
            "action": event,
            "nickname": user.name if user is not None else session.owner_nickname,
            "players": len(session.players),
            "max_players": session.max_players,
        }
        for lobby_user in list(self.users.values()):
            lobby_user.send(message)

    def add_session(self, session: Session):
        if session.key not in self.sessions:
            self.sessions[session.key] = session

    def remove_session(self, session: Session):
        if self.sessions.get(session.key) is session:
            del self.sessions[session.key]

    @defer.inlineCallbacks
    def list_game(self):
        game_list = yield self.chabanas.get_game_list()
        if game_list:
            return game_list, None
        else:
            return False, "Unable to retrieve game information"

    @defer.inlineCallbacks
    def start_session(self, session: Session):
        """
        Demarre la partie a la demande de son proprietaire, avant que tous les
        sieges ne soient pris. Chabanas l'enregistre d'abord : c'est lui qui refuse
        ensuite tout nouveau joueur, et qui garde la valeur si la partie est
        coupee puis reprise.
        :return: (True, None) on success, (False, reason) otherwise
        """
        if session.closed:
            return False, "session_closed"
        if session.started:
            return False, "session_already_started"
        stored = yield self.chabanas.start_session(session.key)
        if not stored:
            return False, "session_start_not_stored"
        session.started = True
        self.logger.info(f"[SESSION] {session.key} started")
        self._send_status(session)
        # le lobby doit cesser de proposer cette partie aux nouveaux joueurs
        self._notify_lobby_users("started", session)
        return True, None

    @defer.inlineCallbacks
    def close_session(self, session: Session, by_admin: bool = False):
        """
        Archive une session pour de bon, au nom de son proprietaire, ou d'un
        administrateur d'OBG (by_admin) : chaque ecran l'annonce alors.

        Chabanas est appele deux fois, dans cet ordre : d'abord pour stocker ou
        en sont les pions, afin que la partie archivee garde sa derniere
        situation et non la disposition initiale de sa creation, puis pour
        archiver la session elle-meme. Un des deux appels peut echouer, et la
        partie reste alors ouverte : annoncer une cloture qui n'a pas ete
        enregistree ne ferait que perdre la session.

        :return: (True, None) on success, (False, reason) otherwise
        """
        if session.closed:
            return False, "session_closed"

        stored = yield self.chabanas.update_session_state(
            session.key,
            session.game_json_state()
        )
        if not stored:
            return False, "session_state_not_stored"

        archived = yield self.chabanas.archive_session(session.key)
        if not archived:
            return False, "session_not_archived"

        demoted = session.close()
        # the players became watchers: the game is no longer in progress
        self.chabanas.vacate_game(session.code)
        self.logger.info(
            f"[SESSION] {session.key} closed by {'an administrator' if by_admin else 'its owner'}, "
            f"{len(demoted)} player(s) turned into watcher(s)"
        )
        # toute la session l'apprend : l'owner qui reclique, et les autres qui
        # passent du coup du role de joueur a celui de spectateur
        session.send({"event": "session_closed", "by_admin": by_admin})
        # the players became watchers: each screen refreshes its list of names
        self._send_status(session)
        self._notify_lobby_users("closed", session)
        return True, None

    # -------------------------------------------------
    # Administration of OBG (admin_api.py, for the Chabanas /manage/ pages)
    # -------------------------------------------------

    def presence(self) -> list:
        """
        Who is connected to each session in memory, the only place that knows
        the live connections. A session that is not listed has nobody in it.
        :return: [{key, code, closed, players, watchers}], nicknames of the
            connected users; the administrators watching are counted apart
        """
        return [
            {
                "key": str(session.key),
                "code": session.code,
                "closed": session.closed,
                "players": [user.name for user in session.players],
                "watchers": [user.name for user in session.watchers if not user.admin],
                "admins": sum(1 for user in session.watchers if user.admin),
            }
            for session in self.sessions.values()
        ]

    @defer.inlineCallbacks
    def admin_close(self, session_key: str):
        """
        An administrator archives a session. One in memory is closed here as by
        its owner (state stored, then archived): its players become watchers
        and are told the administrator closed it.
        :return: (handled, reason): handled is False when the session is not in
            memory, and Chabanas archives it alone
        """
        session = self.sessions.get(session_key)
        if session is None:
            return False, None
        if session.closed:
            return True, None
        closed, reason = yield self.close_session(session, by_admin=True)
        return closed, reason

    def admin_delete(self, session_key: str) -> bool:
        """
        An administrator deletes a session: everybody in it is told and taken
        out of it, and it is dropped from memory without being stored, since
        Chabanas deletes it right after.
        :return: whether the session was in memory
        """
        session = self.sessions.get(session_key)
        if session is None:
            return False
        had_players = bool(session.players)
        # nothing is stored any more when it empties
        session.closed = True
        session.send({"event": "session_deleted"})
        for user in list(session.players) + list(session.watchers):
            session.remove_user(user)
            user.session = None
            user.acquired = []
        self.remove_session(session)
        if had_players and session.code:
            self.chabanas.vacate_game(session.code)
        self.logger.info(f"[SESSION] {session.key} deleted by an administrator")
        self._notify_lobby_users("closed", session)
        return True

    def add_user(self, user: User):
        self.users[user.protocol] = user

    def get_user(self, protocol):
        return self.users.get(protocol)

    def _save_state_if_empty(self, session: Session):
        """
        Removes the session from memory as soon as nobody is left in it
        (no players, no watchers). The session will be recreated from the
        persisted state on the next join.
        """
        # une partie closee a deja ete stockee puis archivee : on ne la supprime
        # pas automatiquement ici
        if session.closed:
            return
        if session.players or session.watchers:
            return
        if session.empty_since is not None:
            # already handled for this empty period
            self.remove_session(session)
            return
        session.empty_since = True
        state = session.game_json_state()
        self.logger.info(
            f"[SESSION] {session.key} is now empty, removing from lobby "
            f"({len(state['movable'])} movable components)"
        )
        deferred = self.chabanas.update_session_state(session.key, state)
        deferred.addCallback(lambda _: self._remove_session_if_still_empty(session))
        deferred.addErrback(
            self.chabanas._log_failure,
            f"[LOBBY] Storing the state of session {session.key}"
        )
        deferred.addErrback(lambda _: self._remove_session_if_still_empty(session))

    def _remove_session_if_still_empty(self, session: Session):
        """
        Le stockage de l'etat est asynchrone : la page de jeu peut reprendre la
        session (elle se reconnecte juste apres avoir quitte le lobby) avant
        qu'il ne se termine. On ne retire donc la session que si elle est encore
        vide, sinon on emporterait la partie qui vient d'etre reprise.
        """
        if session.players or session.watchers:
            return
        self.remove_session(session)

    def delete_user(self, user: User):
        if user is None:
            return
        if user.protocol in self.users:
            if user.session is not None and user.session.key is not None:
                session = self.sessions.get(user.session.key)
                if session is not None:
                    session.remove_user(user)
                    self._release_if_empty(session)
                    self._notify_lobby_users("leave", session, user)
                    self._send_status(session)
                    self._save_state_if_empty(session)
                else:
                    self.logger.warning(
                        f"[DELETE_USER] Session {user.session.key} not found in lobby for user {user.name}"
                    )
            del self.users[user.protocol]

    def shutdown(self):
        """
        Notifies all connected users and disconnects them gracefully.
        Called by the reactor shutdown trigger.
        """
        self.logger.info("[SERVER] Shutdown initiated, notifying all users")
        message = {
            "event": "server_shutdown",
            "reason": "Server is shutting down"
        }
        encoded = json.dumps(message).encode("utf-8")
        for user in list(self.users.values()):
            try:
                user.protocol.sendMessage(
                    encoded,
                    isBinary=False
                )
                user.protocol.sendClose()
            except Exception as error:
                self.logger.error(
                    f"[SHUTDOWN] Error notifying user {user.name}: {error}"
                )
        self.users.clear()
        self.sessions.clear()
        self.logger.info("[SERVER] All users disconnected")


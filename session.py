from unittest import loader

from user import User
from Components.board import Board
from Components.counter import Counter, DEFAULT_COUNTER_COLOR
from Components.dice import Dice
from Components.token import Token

import uuid


# champs de position arrondis dans l'état sauvegardé
POSITION_FIELDS = ('x', 'y', 'initial_x', 'initial_y', 'origin_x', 'origin_y')

# champs de position qu'une entrée de setup peut porter
SETUP_POSITION_FIELDS = ('x', 'y')

# les deux faces qu'un setup peut demander pour un composant qui se retourne
SETUP_SIDES = ('front', 'back')


def read_coordinate(value):
    """
    Lit une coordonnee de setup. Le jeu ecrit "x": 100, mais un game_json ecrit
    a la main peut mettre une chaine, un booleen, ou oublier le champ : la
    modification est alors ignoree, plutot que de deplacer le composant sur 0.
    :param value: le champ x ou y tel que lu dans le game_json
    :return: un entier, ou None quand la coordonnee ne se lit pas
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def read_setup(setup):
    """
    Lit le setup d'un jeu : la liste des modifications a apporter apres
    l'installation de la partie. Une entree mal ecrite est plutot ignoree que
    fatale : un setup incomplet vaut mieux qu'une partie qui ne demarre pas.
    :param setup: le champ "setup" tel que lu dans le game_json
    :return: une liste d'entrees, chacune pourvue au moins d'un component_id
    """
    if not isinstance(setup, list):
        return []

    entries = []
    for entry in setup:
        if not isinstance(entry, dict):
            continue
        component_id = entry.get('component_id')
        if not isinstance(component_id, str) or not component_id:
            continue

        cleaned = {'component_id': component_id}
        for field in SETUP_POSITION_FIELDS:
            coordinate = read_coordinate(entry.get(field))
            if coordinate is not None:
                cleaned[field] = coordinate
        # une face qui n'existe pas est une faute de saisie du jeu : le composant
        # reste sur celle qu'il montre plutot que de disparaitre
        if entry.get('side') in SETUP_SIDES:
            cleaned['side'] = entry['side']
        entries.append(cleaned)
    return entries


def duplicate_component_ids(game_json: dict) -> list:
    """
    Lists the component ids a game_json declares more than once, across its
    "fixed", "movable" and "dice" lists. Components are indexed by id in
    Session.components_dict, so a duplicate would silently hide one of them and
    every action on that id would hit the wrong component.
    :param game_json: the game_json as Chabanas returns it
    :return: the duplicated ids, in order of first appearance, empty when all
        ids are unique
    """
    seen = set()
    duplicates = []
    for list_name in ("fixed", "movable", "dice"):
        components = game_json.get(list_name) or []
        if not isinstance(components, list):
            continue
        for component in components:
            if not isinstance(component, dict) or 'id' not in component:
                continue
            component_id = component['id']
            if component_id in seen and component_id not in duplicates:
                duplicates.append(component_id)
            seen.add(component_id)
    return duplicates


class Session:
    def __init__(
            self,
            user: User,
            name: str,
            key: str,
            code: str,
            active: bool,
            variant: str,
            game_json: dict,
            owner_nickname: str | None = None,
            started: bool = False,
            player_names: list | None = None,
            ):
        self.key = key
        self.name = name
        self.code = code
        self.components_lists = {
            "fixed": [],
            "movable": [],
            # les dés sont leur propre liste : ils ne sont ni plateaux fixes ni
            # pions déplaçables, et surtout ils n'ont pas de position initiale à
            # remettre, ce que "Fixe la position" ferait sur un jeton
            "dice": [],
        }
        self.components_dict = {}
        self.max_players = game_json["game"]["max_players"]
        self.max_watchers = game_json["game"]["max_watchers"]
        self.active = active
        self.variant = variant
        self.players = []
        self.watchers = []
        self.game_json = game_json
        # options du jeu : bloc libre, recopie pour ne jamais écrire dans le
        # game_json source. options.fix_positions porte la position du bouton
        # "Fixe la position" ; null demande de le masquer.
        options = game_json.get("options")
        self.options = dict(options) if isinstance(options, dict) else {}
        # setup : les modifications que le jeu demande d'apporter apres
        # l'installation de la partie, une fois tous les composants charges. Le
        # client ne fait que demander qu'on les applique : les positions
        # restent ici, seul juge de la partie.
        self.setup = read_setup(game_json.get('setup'))
        # le setup a-t-il deja ete applique ? Une fois que c'est fait, il ne doit
        # plus ete renvoye : un joueur qui rejoint apres coup ne replacerait pas
        # les pions au milieu de la partie.
        self.setup_done = False
        self.empty_since = None
        # Chabanas est seul juge de l'ownership : le pseudo du createur arrive
        # dans la description de session. Aucun client ne peut s'octroyer ce
        # droit, il ne fait que le lire.
        self.owner_nickname = owner_nickname
        # une session close reste en memoire le temps que ses connexions se
        # ferment, mais elle n'accepte plus personne et ne se rejoins plus
        self.closed = False
        # la partie a-t-elle commence ? Chabanas en est seul juge : il la
        # demarre quand le dernier siege est pris, ou sur demande d'un joueur.
        # Tant qu'elle n'a pas commence, personne ne prend de pion en main.
        self.started = bool(started)
        # les pseudos des sieges que Chabanas a enregistres pour cette partie,
        # connectes ou non. Une partie commencee attend qu'ils soient tous la
        # avant de laisser prendre un pion.
        self.player_names = list(player_names or [])
        self.load_session_components()

    def load_session_components(self):
        # un dé est accepté dans les trois listes : il n'est ni un plateau ni un
        # pion, donc l'endroit où le jeu le déclare ne regarde pas la session
        for list_name in ("fixed", "movable", "dice"):
            for component in self.game_json.get(list_name, []):
                match component['kind']:
                    case 'board':
                        game_component = Board(
                            component['id'],
                            component['x'],
                            component['y'],
                            component['src'],
                            component['width'],
                            component['height'],
                            # flippable : absent d'un game_json de jeu, present
                            # seulement si le jeu autorise le retournement
                            component.get('flippable', False)
                        )
                        self.components_lists["fixed"].append(game_component)
                        self.components_dict[component['id']] = game_component
                    case 'counter':
                        # un compteur est fige comme un plateau, mais cliquable :
                        # le client dessine son fond et ses deux zones + et -
                        game_component = Counter(
                            component['id'],
                            component['x'],
                            component['y'],
                            component['width'],
                            component['height'],
                            # color et value : absents d'un jeu qui les oublie,
                            # le compteur s'affiche alors sans fond, a zero
                            component.get('color', DEFAULT_COUNTER_COLOR),
                            component.get('value', 0),
                            # font_color : absent, le chiffre prend une couleur
                            # lisible sur le fond
                            component.get('font_color'),
                            # min et max : absents ou null, la valeur est libre de
                            # ce cote ; presents, le serveur refuse de sortir de la
                            # plage et le client grise le signe devenu impossible
                            component.get('min'),
                            component.get('max')
                        )
                        self.components_lists["fixed"].append(game_component)
                        self.components_dict[component['id']] = game_component
                    case 'dice':
                        self.add_dice(component, list_name)
                    case 'token' if list_name == 'movable':
                        # initial/in_place/orientation sont absents d'un game_json de
                        # jeu : ils ne sont presents que si la session a ete reprise
                        # apres une sauvegarde de la position courante.
                        initial = None
                        if 'initial_x' in component and 'initial_y' in component:
                            initial = (component['initial_x'], component['initial_y'])
                        game_component = Token(
                            component['id'],
                            component['x'],
                            component['y'],
                            component['front_src'],
                            component['back_src'],
                            component['width'],
                            component['height'],
                            component.get('move_border', True),
                            initial,
                            # border : le jeu demande-t-il une ombre sous ce
                            # pion ? Rendu fige, independant des deplacements
                            component.get('border'),
                            # in_place : le rectangle vert, tel que la
                            # sauvegarde l'a laisse ; absent, un pion
                            # repositionnable commence sur sa case de depart
                            component.get('in_place'),
                            # orientable : le jeu autorise-t-il les zones de rotation
                            component.get('orientable', False),
                            # orientation : l'angle atteint avant la sauvegarde
                            component.get('orientation', 0),
                            # side : la face que le pion montrait avant la
                            # sauvegarde ; absent d'un game_json de jeu, le
                            # pion commence alors sur sa face
                            component.get('side'),
                            # origin : "transparent" fait afficher le fantome du
                            # pion sur sa case d'origine
                            component.get('origin'),
                            # origin_x / origin_y : ou ce fantome est pose. Le jeu
                            # ne les donne pas, ils ne sont la que sur une session
                            # reprise apres une sauvegarde ; le Token retombe alors
                            # sur le x/y qu'il avait a l'installation du jeu
                            component.get('origin_x'),
                            component.get('origin_y')
                        )
                        self.components_lists['movable'].append(game_component)
                        self.components_dict[component['id']] = game_component

    def add_dice(self, component: dict, list_name: str):
        """
        Builds a dice from its game_json description and files it under "dice".
        Accepts it in the "fixed", the "movable" or a dedicated "dice" list: a
        dice is neither a board nor a token, so where it sits in the game_json is
        a detail of the game, not of the session. The list it came from is kept
        so that saving the situation puts it back where it was.
        :param component: the dice description from the game_json
        :param list_name: "fixed", "movable" or "dice", where it was declared
        """
        game_component = Dice(
            component['id'],
            component['x'],
            component['y'],
            component.get('src'),
            component['width'],
            component['height'],
            component['src_list'],
            list_name,
            # delai de relance en secondes, propre au jeu ; absent du game_json
            # c'est Dice qui applique son defaut
            component.get('roll_delay')
        )
        self.components_lists['dice'].append(game_component)
        self.components_dict[component['id']] = game_component
        return game_component

    def game_json_state(self) -> dict:
        """
        returns the game_json of the session with the current position of its
        components, in the format load_session_components() reads back. Used to
        store the situation when everybody leaves, so the session can be resumed
        later on.
        :return: dict
        """
        state = dict(self.game_json)
        # le setup ne sort de l'etat stocke qu'une fois applique : les positions
        # qu'il a produites sont alors deja dans "fixed" et "movable", et le
        # recopier ferait replacer les pions a chaque reprise de session.
        #
        # Tant qu'il n'a pas ete applique, il doit au contraire y rester. L'etat
        # est stocke des que le dernier joueur quitte la page, donc avant que la
        # page de jeu ne soit chargee : une session reconstruite sans son setup
        # ne l'installerait jamais, et la partie demarrerait hors de sa mise en
        # place. "setup absent" veut donc dire "setup deja fait".
        if self.setup_done:
            state.pop('setup', None)
        dice_by_origin = {'fixed': [], 'movable': [], 'dice': []}
        for component in self.components_lists['dice']:
            dice_by_origin[component.origin_list].append(
                self._rounded_position(component.return_json())
            )
        state['fixed'] = [
            self._rounded_position(component.return_json())
            for component in self.components_lists['fixed']
        ] + dice_by_origin['fixed']
        state['movable'] = [
            self._rounded_position(component.return_json())
            for component in self.components_lists['movable']
        ] + dice_by_origin['movable']
        # une liste "dice" dédiée n'est écrite que si le jeu en a une
        if dice_by_origin['dice'] or 'dice' in state:
            state['dice'] = dice_by_origin['dice']
        return state

    @staticmethod
    def _rounded_position(component_json: dict) -> dict:
        """
        Arrondi au pixel près des coordonnées d'un composant dans l'état
        sauvegardé. Le client calcule des positions flottantes (il divise par le
        zoom), et la base ne doit contenir que des entiers. L'arrondi n'est fait
        qu'ici, pas au stockage en mémoire : le glisser reste ainsi lisse et le
        jeton ne saute pas d'un pixel sous la souris.
        :return: dict
        """
        saved = dict(component_json)
        for key in POSITION_FIELDS:
            value = saved.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                saved[key] = round(value)
        return saved

    def fix_positions_disabled(self) -> bool:
        """
        Le jeu peut masquer le bouton "Fixe la position" en posant
        options.fix_positions = null dans son game_json. Absent, le bouton reste
        affiché à la position par défaut : la clé n'ayant jamais servi, on ne
        change pas le comportement des jeux existants.
        :return: bool
        """
        return self.options.get("fix_positions", "default") is None

    def return_session_json(self) -> dict:
        """
        returns the description of the session
        :return: dict
        """
        session_json = {
            "key": self.key,
            "code": self.code,
            "owner": self.owner_nickname,
            "max_players": self.max_players,
            "max_watchers": self.max_watchers,
            "options": self.options,
            "players": f"{len(self.players)}/{self.max_players}",
            "watchers": f"{len(self.watchers)}/{self.max_watchers}",
            "started": self.started,
            "missing_players": self.missing_players(),
            # le setup en attente que le client applique : une fois la partie
            # installee, la liste est vide et personne ne rejoue la mise en place
            "setup": self.pending_setup(),
            "components": {
                "fixed" : [component.return_json() for component in self.components_lists["fixed"]],
                "movable": [component.return_json() for component in self.components_lists['movable']],
                "dice": [component.return_json() for component in self.components_lists['dice']]
            }
        }
        return session_json

    def refresh_seats(self, started: bool, player_names: list) -> None:
        """
        Reprend ce que Chabanas vient de dire de la session : les sieges
        enregistres, et si la partie a commence. Une partie commencee ne
        redevient jamais non commencee, meme sur une reponse en retard.
        """
        self.started = self.started or bool(started)
        self.player_names = list(player_names)

    def missing_players(self) -> list:
        """
        Les joueurs qui ont un siege dans la partie mais ne sont pas connectes
        en tant que joueur.
        :return: leurs pseudos, dans l'ordre des sieges
        """
        connected = {user.name for user in self.players}
        return [name for name in self.player_names if name not in connected]

    def acquire_refusal(self) -> str | None:
        """
        Peut-on prendre un pion en main ? Il faut que la partie ait commence et
        que tous ses joueurs soient a la table.
        :return: None si c'est permis, sinon le code d'erreur a renvoyer
        """
        if not self.started:
            return "session_not_started"
        if self.missing_players():
            return "players_missing"
        return None

    def return_status_json(self) -> dict:
        """
        L'etat de la partie que chaque ecran affiche, et qui decide si l'on
        peut prendre un pion : diffuse a chaque changement.
        :return: dict
        """
        return {
            "event": "session_status",
            "started": self.started,
            "missing_players": self.missing_players(),
        }

    def is_owner(self, user: User) -> bool:
        """
        Tells whether that user created the session, and may therefore close it.
        The nickname is the only thing compared: a connection is not the owner,
        the seat is.
        """
        return self.owner_nickname is not None and user.name == self.owner_nickname

    def close(self) -> list:
        """
        Closes the game: the session stops being joinable and everyone still
        connected becomes a spectator, so nobody keeps a hand on a token of a
        game that no longer exists.

        The watchers quota is deliberately ignored here. It exists to keep the
        lobby tidy on a running game; on a closed one it would leave somebody
        without any role at all, and the conversion cannot fail halfway.

        :return: the users demoted from player to watcher
        """
        self.closed = True
        self.active = False
        demoted = []
        for user in list(self.players):
            self.players.remove(user)
            user.role = 'watcher'
            self.watchers.append(user)
            demoted.append(user)
        return demoted

    def add_user(self, user, role):
        """
        Adds a user to the session. role determines where the user is added.
        Returns (True, None) if successful, (False, reason) otherwise.
        """
        if user in self.players or user in self.watchers:
            return False, "User is already in this session"
        # somebody came back: the next emptying must be stored again
        self.empty_since = None
        match role:
            case 'player':
                if len(self.players) >= self.max_players:
                    return False, f"Session is full ({self.max_players} players)"
                self.players.append(user)
                user.role = 'player'
                return True, None
            case 'watcher':
                if len(self.watchers) >= self.max_watchers:
                    # code stable : le client doit pouvoir dire a l'utilisateur
                    # que la partie refuse des spectateurs, pas qu'elle est pleine
                    return False, "watchers_full"
                self.watchers.append(user)
                user.role = 'watcher'
                return True, None
            case _:
                return False, f"Invalid role '{role}'"

    def find_user(self, name):
        """
        Returns the player or watcher registered under that nickname, or None.
        A same-named entry means a connection that was never properly closed.
        """
        for user in self.players:
            if user.name == name:
                return user
        for user in self.watchers:
            if user.name == name:
                return user
        return None

    def remove_user(self, user):
        """
        Removes a user from the session.
        """
        if user in self.players:
            self.players.remove(user)
        elif user in self.watchers:
            self.watchers.remove(user)

    def send(self, message):
        for user in self.players:
            user.send(message)
        for user in self.watchers:
            user.send(message)

    def send_others(self, sending_user, message):
        for user in self.players:
            if sending_user != user:
                user.send(message)
        for user in self.watchers:
            user.send(message)

    def get_component(self, component_id):
        return self.components_dict.get(component_id, False)

    def bring_to_front(self, component):
        """
        Un pion relâché repasse en fin de la liste des déplaçables. C'est l'ordre
        de cette liste que le client dessine : le pion se retrouve ainsi
        au-dessus de la pile qu'il vient de rejoindre, et l'ordre survit à une
        sauvegarde / reprise.
        :return: None
        """
        movable = self.components_lists['movable']
        for index, candidate in enumerate(movable):
            if candidate is component:
                movable.append(movable.pop(index))
                return

    def fix_positions(self):
        """
        Remet le rectangle vert sur tous les jetons repositionnables.
        Le nouvel etat est renvoyé pour que chaque ecran, joueurs comme
        spectateurs, l'applique.
        :return: list
        """
        fixed = []
        for component in self.components_lists['movable']:
            component.fix_position()
            fixed.append(component.return_json())
        return fixed

    def pending_setup(self) -> list:
        """
        Le setup tant qu'il n'a pas ete applique. La liste redevient vide une
        fois la partie installee : un joueur qui rejoint entre-temps ne doit pas
        replacer les pions au milieu du jeu.
        :return: la liste des modifications encore a faire
        """
        return [] if self.setup_done else self.setup

    def apply_setup(self) -> list:
        """
        Applique les modifications que le jeu demande apres l'installation de la
        partie, puis renvoie les composants touches : chaque ecran, joueur comme
        spectateur, applique la meme chose.

        Les positions du setup sont absolues, jamais relatives : reappliquer le
        setup ne bouge donc rien, ce qui laisse deux joueurs le demander sans que
        la partie bouge deux fois.

        :return: la liste des composants modifies
        """
        movable_ids = {component.id for component in self.components_lists['movable']}
        applied = {}

        for entry in self.setup:
            component = self.components_dict.get(entry['component_id'])
            if component is None:
                # un setup qui parle d'un composant que le jeu ne declare pas
                # est une faute de saisie : on l'ignore, la partie demarre
                continue

            moved = False
            for field in SETUP_POSITION_FIELDS:
                if field in entry:
                    setattr(component, field, entry[field])
                    moved = True

            if moved:
                component.coordinates = (component.x, component.y)
                # la case de depart d'un pion devient celle du setup : c'est elle
                # que montre le rectangle vert et que vise le retour du pion. Son
                # eventual fantome, lui, reste sur la case d'origine du game_json :
                # le setup installe le jeu, il ne deplace pas les reperes que le
                # jeu a poses sur son plateau
                if component.id in movable_ids:
                    component.fix_position()

            # un composant qui ne se retourne pas garde la face qu'il montre :
            # set_side refuse alors le changement plutot que de pointer vers une
            # image absente
            if 'side' in entry:
                component.set_side(entry['side'])

            # un composant peut figurer dans deux entrees, une qui le deplace et
            # une qui le retourne : on ne l'annonce qu'une fois, dans son etat
            # final
            applied[component.id] = component.return_json()

        self.setup_done = True
        return list(applied.values())

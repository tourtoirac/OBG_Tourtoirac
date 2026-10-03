from unittest import loader

from user import User
from Components.board import Board
from Components.dice import Dice
from Components.token import Token

import uuid


# champs de position arrondis dans l'état sauvegardé
POSITION_FIELDS = ('x', 'y', 'initial_x', 'initial_y')


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
        self.empty_since = None
        # Chabanas est seul juge de l'ownership : le pseudo du createur arrive
        # dans la description de session. Aucun client ne peut s'octroyer ce
        # droit, il ne fait que le lire.
        self.owner_nickname = owner_nickname
        # une session close reste en memoire le temps que ses connexions se
        # ferment, mais elle n'accepte plus personne et ne se rejoins plus
        self.closed = False
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
                            component['height']
                        )
                        self.components_lists["fixed"].append(game_component)
                        self.components_dict[component['id']] = game_component
                    case 'dice':
                        self.add_dice(component, list_name)
                    case 'token' if list_name == 'movable':
                        # initial/border/orientation sont absents d'un game_json de
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
                            component.get('border'),
                            # orientable : le jeu autorise-t-il les zones de rotation
                            component.get('orientable', False),
                            # orientation : l'angle atteint avant la sauvegarde
                            component.get('orientation', 0),
                            # side : la face que le pion montrait avant la
                            # sauvegarde ; absent d'un game_json de jeu, le
                            # pion commence alors sur sa face
                            component.get('side'),
                            # origin : "transparent" fait afficher le fantome du
                            # pion sur sa case de depart
                            component.get('origin')
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
            "components": {
                "fixed" : [component.return_json() for component in self.components_lists["fixed"]],
                "movable": [component.return_json() for component in self.components_lists['movable']],
                "dice": [component.return_json() for component in self.components_lists['dice']]
            }
        }
        return session_json

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

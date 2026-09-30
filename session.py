from unittest import loader

from user import User
from Components.board import Board
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
            ):
        self.key = key
        self.name = name
        self.code = code
        self.components_lists = {
            "fixed": [],
            "movable": [],
        }
        self.components_dict = {}
        self.max_players = game_json["game"]["max_players"]
        self.max_watchers = game_json["game"]["max_watchers"]
        self.active = active
        self.variant = variant
        self.players = []
        self.watchers = []
        self.game_json = game_json
        self.empty_since = None
        self.load_session_components()

    def load_session_components(self):
        for component in self.game_json['fixed']:
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
        for component in self.game_json['movable']:
            match component['kind']:
                case 'token':
                    # initial/border sont absents d'un game_json de jeu : ils ne
                    # sont presents que si la session a ete reprise apres une
                    # sauvegarde de la position courante.
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
                        component.get('border')
                    )
                    self.components_lists['movable'].append(game_component)
                    self.components_dict[component['id']] = game_component

    def game_json_state(self) -> dict:
        """
        returns the game_json of the session with the current position of its
        components, in the format load_session_components() reads back. Used to
        store the situation when everybody leaves, so the session can be resumed
        later on.
        :return: dict
        """
        state = dict(self.game_json)
        state['fixed'] = [
            self._rounded_position(component.return_json())
            for component in self.components_lists['fixed']
        ]
        state['movable'] = [
            self._rounded_position(component.return_json())
            for component in self.components_lists['movable']
        ]
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

    def return_session_json(self) -> dict:
        """
        returns the description of the session
        :return: dict
        """
        session_json = {
            "key": self.key,
            "code": self.code,
            "max_players": self.max_players,
            "max_watchers": self.max_watchers,
            "players": f"{len(self.players)}/{self.max_players}",
            "watchers": f"{len(self.watchers)}/{self.max_watchers}",
            "components": {
                "fixed" : [component.return_json() for component in self.components_lists["fixed"]],
                "movable": [component.return_json() for component in self.components_lists['movable']]
            }
        }
        return session_json

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
                    return False, f"Session is full ({self.max_watchers} watchers)"
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

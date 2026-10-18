from unittest import loader

from user import User
from chat import ChatHistory
from Components.bag import Bag
from Components.board import Board
from Components.board_group import BoardGroup
from Components.card import Card
from Components.counter import Counter, DEFAULT_COUNTER_COLOR
from Components.mw_character import MwCharacter
from Components.deck import Deck
from Components.dice import Dice
from Components.dice_pool import DicePool
from Components.stack import Stack
from Components.token import Token

import random
import uuid


# champs de position arrondis dans l'état sauvegardé
POSITION_FIELDS = ('x', 'y', 'initial_x', 'initial_y', 'origin_x', 'origin_y')

# champs de position qu'une entrée de setup peut porter
SETUP_POSITION_FIELDS = ('x', 'y')

# les deux faces qu'un setup peut demander pour un composant qui se retourne
SETUP_SIDES = ('front', 'back')

# randomizer: a setup entry that lays components drawn at random on a list of
# targets. Its source is a container (the kind of the component named by
# source_id) or a list of component ids.
RANDOMIZER_KIND = 'randomizer'
RANDOMIZER_CONTAINER_KINDS = ('bag', 'deck', 'stack')
RANDOMIZER_ID_LIST = 'id_list'
# the piles a randomizer target may name in a deck or a stack
RANDOMIZER_TARGET_PILES = ('draw', 'discard')

# the field holding the face a token does not show, by the side it shows
HIDDEN_FACE_FIELDS = {'back': 'front_src', 'front': 'back_src'}

# the kinds a player moves with the mouse: a card is handled like a token
MOVABLE_KINDS = ('token', 'card')

# the lists of a deck description holding its cards: the draw pile, then the
# discard pile a saved state adds
DECK_CARD_LISTS = ('components', 'discard')

# the most cards one draw action takes out of a pile, unless the deck has a
# multi-draw button for more (card_pickup_quantity), and how far apart, in
# pixels, they come out: side by side, so the player sees how many it holds
DRAW_MAX = 10
DRAW_FAN = 20

# the kinds holding cards: a stack is a deck of one pile that deals its cards
DECK_KINDS = ('deck', 'stack')


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


def read_randomizer(entry: dict) -> dict | None:
    """
    Reads a randomizer of the setup. A badly written one is ignored, like any
    setup entry: the game starts without it.
    :param entry: {"kind": "randomizer", "source_kind", "source_id", "targets"}
    :return: the cleaned randomizer, or None when it cannot be used: unknown
        source_kind, source_id of the wrong type, or no usable target
    """
    source_kind = entry.get('source_kind')
    source_id = entry.get('source_id')
    if source_kind == RANDOMIZER_ID_LIST:
        if not isinstance(source_id, list):
            return None
        source_id = [item for item in source_id if isinstance(item, str) and item]
    elif source_kind in RANDOMIZER_CONTAINER_KINDS:
        if not isinstance(source_id, str) or not source_id:
            return None
    else:
        return None

    targets = []
    for target in entry.get('targets') if isinstance(entry.get('targets'), list) else []:
        if not isinstance(target, dict):
            continue
        # a target is a deck or a stack: the card drawn goes on one of its
        # piles, as if it were released on it (Session.put_in_deck)
        container_id = target.get('container_id')
        if isinstance(container_id, str) and container_id:
            cleaned = {'container_id': container_id}
            if target.get('pile') in RANDOMIZER_TARGET_PILES:
                cleaned['pile'] = target['pile']
            targets.append(cleaned)
            continue
        # or a place: without both coordinates there is nowhere to put a
        # component, and it is skipped
        cleaned = {field: read_coordinate(target.get(field)) for field in SETUP_POSITION_FIELDS}
        if None in cleaned.values():
            continue
        if target.get('side') in SETUP_SIDES:
            cleaned['side'] = target['side']
        orientation = target.get('orientation')
        if isinstance(orientation, (int, float)) and not isinstance(orientation, bool):
            cleaned['orientation'] = int(orientation)
        targets.append(cleaned)
    if not targets:
        return None
    return {'kind': RANDOMIZER_KIND, 'source_kind': source_kind, 'source_id': source_id, 'targets': targets}


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
        # a randomizer stays as it is: the session draws its components once
        # they are built (Session.resolve_randomizers)
        if entry.get('kind') == RANDOMIZER_KIND:
            randomizer = read_randomizer(entry)
            if randomizer is not None:
                entries.append(randomizer)
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


# what a builder raises on a component the game_json describes wrongly: a
# missing field, a description that is not an object, a value of the wrong type
COMPONENT_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


class InvalidComponentError(Exception):
    """
    A component of a game_json cannot be built. Carries what is needed to find
    it in the game_json: its id and kind when it has them, the list it was
    declared in, the component holding it (bag, board_group, dice_pool) and
    the field that is missing.
    """

    def __init__(self, component, list_name: str, error: Exception, parent_id=None):
        described = component if isinstance(component, dict) else {}
        self.component_id = described.get('id')
        self.kind = described.get('kind')
        self.list_name = list_name
        self.parent_id = parent_id
        # a KeyError names the field the builder could not read
        self.missing_field = error.args[0] if isinstance(error, KeyError) and error.args else None
        if self.missing_field is not None:
            self.problem = f"missing field '{self.missing_field}'"
        elif not isinstance(component, dict):
            self.problem = "the component is not a JSON object"
        else:
            self.problem = f"{type(error).__name__}: {error}"
        super().__init__(self.describe())

    def describe(self) -> str:
        name = f"'{self.component_id}'" if self.component_id is not None else "without id"
        where = f'list "{self.list_name}"'
        if self.parent_id is not None:
            where += f", inside '{self.parent_id}'"
        return f"Component {name} ({self.kind or 'unknown kind'}, {where}): {self.problem}"

    def details(self) -> dict:
        """What the error event carries besides its message."""
        return {
            "component_id": self.component_id,
            "kind": self.kind,
            "list": self.list_name,
            "parent_id": self.parent_id,
            "missing_field": self.missing_field,
        }


def build_checked(builder, component, list_name: str, parent_id=None):
    """
    Runs a component builder and turns its failure into an
    InvalidComponentError naming the component.
    """
    try:
        return builder(component)
    except InvalidComponentError:
        raise
    except COMPONENT_ERRORS as error:
        raise InvalidComponentError(component, list_name, error, parent_id) from error


def read_nationalities(game_json) -> list:
    """
    Reads the optional "nationalities" key of the options of a game_json: the
    sides the players choose from when they take a seat. Same reading as Chabanas
    (Game/tools.py), which is the one that enforces the choice.
    :return: the nationalities in the order of the game_json, without
        duplicates; empty when the game declares none
    """
    options = game_json.get('options') if isinstance(game_json, dict) else None
    declared = options.get('nationalities') if isinstance(options, dict) else None
    if not isinstance(declared, list):
        return []
    nationalities = []
    for nationality in declared:
        if isinstance(nationality, str) and nationality and nationality not in nationalities:
            nationalities.append(nationality)
    return nationalities


def read_documents(game_json) -> list:
    """
    Reads the optional "documents" key of a game_json: the PDF files (rules,
    charts...) the players may open from the game page.
    :return: the documents in the order of the game_json, each one as
        {"src": ..., "name": ...}; an entry without a src is left out, and one
        without a name takes its src as name
    """
    declared = game_json.get('documents') if isinstance(game_json, dict) else None
    if not isinstance(declared, list):
        return []
    documents = []
    for document in declared:
        if not isinstance(document, dict):
            continue
        src = document.get('src')
        if not isinstance(src, str) or not src:
            continue
        name = document.get('name')
        documents.append({"src": src, "name": name if isinstance(name, str) and name else src})
    return documents


def expand_copies(component) -> list:
    """
    A token declared with "copies": n stands for n identical tokens: it is
    replaced by n copies whose ids are the declared id followed by -01, -02...
    The declared token itself is not created, and the copies do not carry
    "copies", so a saved session stores them as ordinary tokens and a resumed
    one does not multiply them again.
    :param component: a component as the game_json declares it
    :return: the copies, or the component alone when it asks for none. A
        "copies" that is not an integer of at least 1 is ignored.
    """
    if not isinstance(component, dict) or component.get('kind') not in MOVABLE_KINDS or 'id' not in component:
        return [component]
    copies = component.get('copies')
    # bool is an int in Python: true is a typing mistake, not one copy
    if type(copies) is not int or copies < 1:
        return [component]
    # two digits at least, more when the game asks for 100 copies or more
    digits = max(2, len(str(copies)))
    expanded = []
    for number in range(1, copies + 1):
        copy = dict(component)
        del copy['copies']
        copy['id'] = f"{component['id']}-{number:0{digits}d}"
        expanded.append(copy)
    return expanded


def expand_components(components: list) -> list:
    """
    The components of one list of a game_json, each token that asks for copies
    replaced by them, in the order of declaration.
    """
    return [copy for component in components for copy in expand_copies(component)]


def anonymize_ids(game_json: dict) -> dict:
    """
    A game whose options carry "anonymous_id": true does not let the id of a
    token tell what the token is: every token of "movable" and of the bags
    gets a random id (uuid) in place of the one the game_json gives it. The
    setup entries naming a token follow the new id.

    It is done once, when the session is first built from the game: the
    options then carry "anonymized": true, the new ids are the ones stored
    with the session, and a resumed session keeps them.

    Tokens asking for copies are expanded first: each copy gets its own id.
    Boards, counters, dice and bags keep their id, which hides nothing.
    :param game_json: the game_json as Chabanas returns it; left untouched
    :return: the game_json to build the session from, the same object when
        there is nothing to anonymize
    """
    options = game_json.get('options') if isinstance(game_json, dict) else None
    if not isinstance(options, dict) or options.get('anonymous_id') is not True:
        return game_json
    if options.get('anonymized') is True:
        return game_json

    new_ids = {}

    def anonymized(components) -> list:
        renamed = []
        for component in expand_components(components if isinstance(components, list) else []):
            if isinstance(component, dict) and component.get('kind') in MOVABLE_KINDS and 'id' in component:
                component = dict(component)
                new_ids[component['id']] = component['id'] = str(uuid.uuid4())
            renamed.append(component)
        return renamed

    def anonymized_container(component):
        # the tokens of a bag and the cards of a deck are renamed like those
        # of "movable"
        if not isinstance(component, dict):
            return component
        if component.get('kind') == 'bag':
            return dict(component, components=anonymized(component.get('components')))
        if component.get('kind') in DECK_KINDS:
            return dict(component, **{
                key: anonymized(component[key])
                for key in DECK_CARD_LISTS if isinstance(component.get(key), list)
            })
        return component

    result = dict(game_json)
    result['options'] = dict(options, anonymized=True)
    if isinstance(game_json.get('movable'), list):
        result['movable'] = anonymized(game_json['movable'])
    if isinstance(game_json.get('fixed'), list):
        result['fixed'] = [anonymized_container(component) for component in game_json['fixed']]
    def renamed_entry(entry):
        if not isinstance(entry, dict):
            return entry
        if isinstance(entry.get('component_id'), str) and entry['component_id'] in new_ids:
            return dict(entry, component_id=new_ids[entry['component_id']])
        # the ids a randomizer draws from are renamed too
        if entry.get('kind') == RANDOMIZER_KIND and entry.get('source_kind') == RANDOMIZER_ID_LIST \
                and isinstance(entry.get('source_id'), list):
            return dict(entry, source_id=[
                new_ids.get(item, item) if isinstance(item, str) else item for item in entry['source_id']
            ])
        return entry

    if isinstance(game_json.get('setup'), list):
        result['setup'] = [renamed_entry(entry) for entry in game_json['setup']]
    return result


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
        # the copies of a token take ids of their own, which may collide with
        # an id the game declares elsewhere
        for component in expand_components(components):
            if not isinstance(component, dict) or 'id' not in component:
                continue
            # the boards of a board_group, the dice of a dice_pool and the
            # tokens of a bag are indexed by their own id, like a board, a dice
            # or a token declared alone: they share the same namespace
            nested = None
            if component.get('kind') == 'board_group':
                nested = component.get('boards')
            elif component.get('kind') == 'dice_pool':
                nested = component.get('dice')
            elif component.get('kind') == 'bag' and isinstance(component.get('components'), list):
                # the tokens of a bag may ask for copies too
                nested = expand_components(component['components'])
            elif component.get('kind') == 'mw_character' and isinstance(component.get('cards'), list):
                nested = component['cards']
            elif component.get('kind') in DECK_KINDS:
                # and so may the cards of a deck, in either of its piles
                nested = [
                    card
                    for key in DECK_CARD_LISTS if isinstance(component.get(key), list)
                    for card in expand_components(component[key])
                ]
            ids = [component['id']]
            if isinstance(nested, list):
                ids += [item['id'] for item in nested if isinstance(item, dict) and 'id' in item]
            for component_id in ids:
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
            player_nationalities: dict | None = None,
            ):
        # a game asking for anonymous ids gets them here, once: everything
        # below, and the stored state, only knows the new ids
        game_json = anonymize_ids(game_json)
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
        # game_json source. options.fix_positions is a boolean: false hides the
        # "Fixe la position" button of the client and refuses the action.
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
        # the order the players are listed in: the one they joined the game
        # in, unless the owner changed it. Saved with the state (player_order).
        saved_order = game_json.get('player_order')
        self.player_order = [
            name for name in (saved_order if isinstance(saved_order, list) else [])
            if isinstance(name, str)
        ]
        self.remember_player_order(self.player_names)
        # nationalities: the sides the game declares, empty for a game without
        # any. No rule depends on them yet: they are only carried to the clients.
        self.nationalities = read_nationalities(game_json)
        # the nationality Chabanas stored on each seat, by nickname
        self.player_nationalities = dict(player_nationalities or {})
        # documents: the PDF files the game lets the players consult. They
        # stay in the game_json, so the saved state keeps them.
        self.documents = read_documents(game_json)
        # private hands: the tokens each player keeps off the table, by
        # nickname. The seat holds the hand, not the connection: it is still
        # there when the player comes back.
        self.hands = {}
        # what the players show of their hand, by nickname of the owner:
        # {"to": "all" or a nickname, "cards": [components of that hand],
        #  "ranks": [rank of each card], "next_rank": rank of the next one}.
        # A rank is given once: it never changes, nor is it given again,
        # until the area is closed.
        # Never saved: a resumed session finds every card back in its hand.
        self.shows = {}
        # the players in the voice channel of the session, in the order they
        # joined it: one entry per connection. Never saved.
        self.voice = []
        # the text chat: a general channel and a private one between each pair
        # of players. Saved with the state.
        self.chat = ChatHistory.from_json(game_json.get('chat'))
        self.load_session_components()
        self.load_hands(game_json.get('hands'))
        # the components a randomizer of the setup lays on the table: when the
        # setup is applied, they leave the bag or the deck holding them
        self.setup_take_out = set()
        self.setup = self.resolve_randomizers(self.setup)
        # does a token of this game belong to a nationality, or is it a card ?
        # Without any, nothing is hidden from anybody and messages go out
        # untouched
        self.hides_faces = any(
            isinstance(component, Card)
            or (isinstance(component, Token) and component.nationality is not None)
            for component in self.components_dict.values()
        )

    def load_session_components(self):
        # un dé est accepté dans les trois listes : il n'est ni un plateau ni un
        # pion, donc l'endroit où le jeu le déclare ne regarde pas la session
        for list_name in ("fixed", "movable", "dice"):
            # a token declared with "copies" is replaced by its copies
            for component in expand_components(self.game_json.get(list_name, [])):
                # a component the game describes wrongly stops the build with
                # an error that names it
                try:
                    match component['kind']:
                        case 'board':
                            game_component = self.build_board(component)
                            self.components_lists["fixed"].append(game_component)
                            self.components_dict[component['id']] = game_component
                        case 'board_group':
                            game_component = self.build_board_group(component)
                            if game_component is not None:
                                self.components_lists["fixed"].append(game_component)
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
                        case 'dice_pool' if list_name == 'fixed':
                            game_component = self.build_dice_pool(component)
                            if game_component is not None:
                                self.components_lists["fixed"].append(game_component)
                                self.components_dict[component['id']] = game_component
                        case 'bag' if list_name == 'fixed':
                            game_component = self.build_bag(component)
                            self.components_lists["fixed"].append(game_component)
                            self.components_dict[component['id']] = game_component
                        case 'deck' | 'stack' if list_name == 'fixed':
                            game_component = self.build_deck(component)
                            self.components_lists["fixed"].append(game_component)
                            self.components_dict[component['id']] = game_component
                        case 'mw_character' if list_name == 'fixed':
                            # mw_character: a card that never moves, with two columns of boxes
                            game_component = MwCharacter(
                                component['id'],
                                component['x'],
                                component['y'],
                                component['width'],
                                component['height'],
                                component['front_src'],
                                component.get('back_src'),
                                # side and marks: only there on a saved session
                                component.get('side'),
                                component.get('nationality'),
                                component.get('left'),
                                component.get('right'),
                            )
                            # the cards under it, the last one on top: a
                            # card without a size takes the one of the character
                            for card in component.get('cards') or []:
                                built = build_checked(
                                    lambda description: self.build_card(description, component),
                                    card, list_name, component['id'],
                                )
                                built.set_side('front')
                                game_component.cards.append(built)
                                self.components_dict[built.id] = built
                            self.components_lists["fixed"].append(game_component)
                            self.components_dict[component['id']] = game_component
                        case 'token' if list_name == 'movable':
                            game_component = self.build_token(component)
                            self.components_lists['movable'].append(game_component)
                            self.components_dict[component['id']] = game_component
                        case 'card' if list_name == 'movable':
                            game_component = self.build_card(component)
                            self.components_lists['movable'].append(game_component)
                            self.components_dict[component['id']] = game_component
                except InvalidComponentError:
                    raise
                except COMPONENT_ERRORS as error:
                    raise InvalidComponentError(component, list_name, error) from error

    @staticmethod
    def build_token(component: dict) -> Token:
        """
        Builds a token from its game_json description, whether it is declared
        in "movable" or inside a bag.
        :param component: the token description from the game_json
        """
        # initial/in_place/orientation sont absents d'un game_json de
        # jeu : ils ne sont presents que si la session a ete reprise
        # apres une sauvegarde de la position courante.
        initial = None
        if 'initial_x' in component and 'initial_y' in component:
            initial = (component['initial_x'], component['initial_y'])
        return Token(
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
            component.get('origin_y'),
            # nationality: the side this token belongs to, when the game says so
            component.get('nationality'),
            # block_sight: hides the tokens under it from the other nationalities
            component.get('block_sight', False)
        )

    @staticmethod
    def build_card(component: dict, deck: dict | None = None) -> Card:
        """
        Builds a card from its game_json description, whether it is declared
        in "movable" or inside a deck.
        :param component: the card description from the game_json
        :param deck: the description of the deck holding the card. Such a card
            has no position, and takes the size and the back of its deck when
            it declares none of its own.
        """
        if deck is None:
            x, y = component['x'], component['y']
            width, height = component['width'], component['height']
            back_src = component.get('back_src')
        else:
            x = y = None
            width = component.get('width', deck['width'])
            height = component.get('height', deck['height'])
            back_src = component.get('back_src') or deck.get('back_src')
        return Card(
            component['id'],
            x,
            y,
            component['front_src'],
            back_src,
            width,
            height,
            # orientation and side: only there on a session resumed after a
            # save; a card of a game starts upright
            component.get('orientation', 0),
            component.get('side'),
            component.get('nationality'),
            # whether the players may turn the card, only when it says so
            component.get('orientable', False),
            component.get('border'),
        )

    def build_deck(self, component: dict) -> Deck:
        """
        Builds a deck and indexes each of its cards by its own id. The cards
        stay inside the deck: they are not filed under "movable" until a
        player draws them, the deck carries them in the saved state. A deck
        may be empty: players fill it during the game.

        "components" lists the cards of the draw pile, the last one on top.
        "discard" lists those of the discard pile: a game seldom declares it,
        a saved state does. "shuffle_at_startup" set to true shuffles the draw
        pile once, when the game is launched.
        :param component: the deck description from the game_json
        """
        piles = {}
        for key in DECK_CARD_LISTS:
            content = component.get(key)
            piles[key] = [
                build_checked(lambda item: self.build_card(item, component), item, 'fixed', component.get('id'))
                # a card of a deck may ask for copies, like a token
                for item in expand_components(content if isinstance(content, list) else [])
                # a deck only holds cards: anything else is ignored
                if isinstance(item, dict) and item.get('kind') == 'card'
            ]
            for card in piles[key]:
                self.components_dict[card.id] = card
        deck = self.new_deck(component, piles)
        # shuffle_at_startup: only true shuffles the draw pile when the game
        # is launched. The saved state never carries the key (state_json), so
        # a resumed session keeps the order its cards had.
        if component.get('shuffle_at_startup') is True:
            deck.shuffle('draw')
        return deck

    @staticmethod
    def new_deck(component: dict, piles: dict) -> Deck:
        """
        The deck or the stack a description asks for, holding the cards
        already built from it.
        :param piles: the cards of each list of DECK_CARD_LISTS
        """
        if component.get('kind') == 'stack':
            # a stack has one single pile: cards a description would list as
            # discarded go on top of the others
            return Stack(
                component['id'],
                component['x'],
                component['y'],
                component['width'],
                component['height'],
                component.get('back_src'),
                piles['components'] + piles['discard'],
                component.get('text'),
                component.get('card_pickup_quantity'),
                component.get('orientation'),
                # the Deal button: only when the game says true
                component.get('deal'),
                # the Shuffle button: hidden only when the game says false
                component.get('display_shuffle'),
            )
        return Deck(
            component['id'],
            component['x'],
            component['y'],
            component['width'],
            component['height'],
            # type: "single" is one pile, "duo" a draw pile and a discard pile
            component.get('type', 'single'),
            component.get('back_src'),
            piles['components'],
            piles['discard'],
            # text: optional caption shown above the deck
            component.get('text'),
            # where the discard pile lies; next to the draw pile by default
            component.get('discard_x'),
            component.get('discard_y'),
            # the multi-draw buttons: a list of numbers, false for none
            component.get('card_pickup_quantity'),
            # the angle the piles are turned by: 0, 90, 180 or 270
            component.get('orientation'),
            # the Shuffle buttons: hidden only when the game says false
            component.get('display_shuffle'),
        )

    def deck_holding(self, component) -> Deck | None:
        """
        The deck a component is waiting in, or None when it is elsewhere.
        """
        for candidate in self.components_lists['fixed']:
            if isinstance(candidate, Deck) and candidate.holds(component):
                return candidate
        return None

    def deck_at(self, component, x, y) -> tuple:
        """
        The pile a card released at (x, y) joins: the one its center lies on.
        Decks are drawn in list order, so the last one under the point wins.
        Only a card goes into a deck.
        :param x: top-left corner of the card, as sent by the client
        :return: (deck, pile name), or (None, None) when the card lands on
            the table
        """
        if not isinstance(component, Card):
            return None, None
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            return None, None
        center_x = x + component.width / 2
        center_y = y + component.height / 2
        for candidate in reversed(self.components_lists['fixed']):
            if isinstance(candidate, Deck):
                pile = candidate.pile_at(center_x, center_y)
                if pile is not None:
                    return candidate, pile
        return None, None

    def put_in_deck(self, deck: Deck, pile: str, component) -> None:
        """
        Moves a card from the table onto a pile of a deck: it leaves "movable"
        and loses its position until somebody draws it.
        """
        movable = self.components_lists['movable']
        if component in movable:
            movable.remove(component)
        # the card lies turned like the deck it is put on
        component.tap(deck.orientation)
        deck.add(pile, component)

    def draw_from_deck(self, deck: Deck, pile: str, user: User, x=None, y=None):
        """
        Takes the top card of a pile and puts it in the hand of user: it comes
        back on the table, above the others, centered on (x, y), on the side
        it had on its pile and upright.
        :param x: where the player clicked; the center of the pile when the
            client gives no usable point
        :return: the card, or None when the pile has none user may take: it
            is empty, or its top card belongs to another nationality
        """
        card = deck.draw(pile, user)
        if card is None:
            return None
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            pile_x, pile_y = deck.pile_position(pile)
            x = pile_x + deck.width / 2
            y = pile_y + deck.height / 2
        card.enter_table(x - card.width / 2, y - card.height / 2)
        # the card comes out upright, whatever the angle of its deck
        card.tap(0)
        self.components_lists['movable'].append(card)
        card.acquire(user)
        return card

    def draw_cards_from_deck(self, deck: Deck, pile: str, user: User, count=1, x=None, y=None) -> list:
        """
        Takes the top cards of a pile, one after the other, and puts them all
        in the hand of user. They come out fanned to the right of (x, y), the
        top card of the pile first, so it ends up under the others.
        :param count: how many cards are asked for, at most DRAW_MAX or the
            largest multi-draw button of the deck; anything that is not a
            whole number above zero counts as one
        :return: the cards drawn, fewer than asked when the pile runs out or
            shows a card user may not take, empty when it gives none
        """
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            count = 1
        has_point = all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y))
        cards = []
        limit = max([DRAW_MAX, *deck.card_pickup_quantity])
        for index in range(min(count, limit)):
            card = self.draw_from_deck(deck, pile, user, x + index * DRAW_FAN if has_point else None, y)
            if card is None:
                break
            if not has_point:
                card.x += index * DRAW_FAN
                card.coordinates = (card.x, card.y)
            cards.append(card)
        return cards

    def dealt_players(self) -> list:
        """
        The players a stack deals to: every seat of the game, then the players
        at the table without a known seat. The hand belongs to the seat, so a
        nickname is enough.
        :return: their nicknames, in the order of the seats
        """
        return self.ordered_seats()

    def deal_from_stack(self, stack: Stack) -> dict:
        """
        Deals one card of a stack to the private hand of each player, from the
        top of the stack down, in the order of the seats. The cards never
        touch the table: they keep the back they showed in the stack.
        :return: {nickname: card}, empty when the stack does not hold one card
            for each player, in which case nothing moves
        """
        names = self.dealt_players()
        dealt = dict(zip(names, stack.deal(len(names))))
        for nickname, card in dealt.items():
            self.enter_hand(nickname, card)
        return dealt

    def build_bag(self, component: dict) -> Bag:
        """
        Builds a bag and indexes each of its tokens by its own id. The tokens
        stay inside the bag: they are not filed under "movable" until a player
        picks them, the bag carries them in "fixed" and in the saved state.
        A bag may be empty: players fill it during the game.
        :param component: the bag description from the game_json
        """
        content = component.get('components')
        tokens = [
            build_checked(self.build_token, item, 'fixed', component.get('id'))
            # a token of a bag may ask for copies, like one of "movable"
            for item in expand_components(content if isinstance(content, list) else [])
            # a bag only holds tokens: anything else is ignored, like an
            # unknown kind in the "fixed" list
            if isinstance(item, dict) and item.get('kind') == 'token'
        ]
        for token in tokens:
            self.components_dict[token.id] = token
        return Bag(
            component['id'],
            component['x'],
            component['y'],
            component['width'],
            component['height'],
            # src: the picture of the bag; without one the client draws a plain box
            component.get('src'),
            tokens,
            # text: optional caption shown under the bag
            component.get('text'),
            # count: true shows the number of tokens left in the bag
            component.get('count', False)
        )

    def load_hands(self, saved) -> None:
        """
        Rebuilds the private hands of a resumed session. A game_json of a game
        declares none: the key only exists in a saved state.
        :param saved: {nickname: [token description, ...]} or anything else
        """
        if not isinstance(saved, dict):
            return
        for nickname, items in saved.items():
            if not isinstance(nickname, str) or not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get('kind') not in MOVABLE_KINDS:
                    continue
                if item['kind'] == 'card':
                    # a card held in a hand has no position, like one of a deck
                    token = build_checked(lambda card: self.build_card(card, card), item, 'hands')
                else:
                    token = build_checked(self.build_token, item, 'hands')
                token.leave_table()
                self.components_dict[token.id] = token
                self.hands.setdefault(nickname, []).append(token)

    def private_hand_enabled(self) -> bool:
        """
        A game turns the private hands on by setting options.private_hand to
        an object, even an empty one. Absent, null or anything else, nobody
        keeps a token off the table.
        """
        return isinstance(self.options.get("private_hand"), dict)

    def show_hands_enabled(self) -> bool:
        """
        A game lets its players show cards of their hand to the others by
        setting options.private_hand.show_hands_to_other_players to true.
        """
        private_hand = self.options.get("private_hand")
        return isinstance(private_hand, dict) and private_hand.get("show_hands_to_other_players") is True

    def open_show(self, nickname: str, to) -> bool:
        """
        Opens the area a player shows cards of its hand in, or changes who
        reads it; the cards already shown stay.
        :param to: "all" for every player, or the nickname of another player
        :return: False when to names nobody the hand can be shown to
        """
        if to != "all" and (to == nickname or to not in self.dealt_players()):
            return False
        self.shows.setdefault(nickname, {"cards": [], "ranks": [], "next_rank": 1})["to"] = to
        return True

    def add_to_show(self, nickname: str, component) -> bool:
        """
        Shows one more component of the hand of a player. It stays in that
        hand: it is only read by more people.
        :return: False when the player shows nothing, or does not hold it
        """
        show = self.shows.get(nickname)
        if show is None or self.hand_holding(component) != nickname:
            return False
        if not any(component is card for card in show["cards"]):
            show["cards"].append(component)
            # the ranks only grow: a card shown again gets a new one
            show["ranks"].append(show["next_rank"])
            show["next_rank"] += 1
        return True

    def remove_from_show(self, nickname: str, component) -> bool:
        """
        Stops showing a component: it stays in the hand of the player. The
        other cards keep their rank.
        :return: False when the player does not show that component
        """
        show = self.shows.get(nickname)
        if show is None:
            return False
        for index, card in enumerate(show["cards"]):
            if card is component:
                del show["cards"][index]
                del show["ranks"][index]
                return True
        return False

    def close_show(self, nickname: str) -> list | None:
        """
        Closes the area of a player: its cards are only in the hand again,
        each one turned back on its front.
        :return: the cards that had to be turned over, or None when that
            player was showing nothing
        """
        show = self.shows.pop(nickname, None)
        if show is None:
            return None
        turned = []
        for card in show["cards"]:
            if card.side != 'front' and card.set_side('front'):
                turned.append(card)
        return turned

    def shown_by(self, component) -> str | None:
        """
        The nickname of the player showing a component, or None.
        """
        for nickname, show in self.shows.items():
            if any(component is card for card in show["cards"]):
                return nickname
        return None

    def reads_show_of(self, user, nickname: str) -> bool:
        """
        Whether user reads what a player shows: that player, and the players
        it shows its cards to. A watcher never does.
        """
        show = self.shows.get(nickname)
        if show is None or getattr(user, 'role', None) != 'player':
            return False
        return user.name == nickname or show["to"] in ("all", user.name)

    def show_json(self, nickname: str, user) -> dict:
        """
        What user reads of the cards a player shows: each one as it is, on
        the side its owner left it. Whoever may not read them is told the
        area is closed.
        :return: {"event": "show", "owner", "to", "cards", "ranks",
            "component_ids"}; "to" is None when nothing is shown to user,
            "ranks" gives the rank of each card, and only the owner gets the
            ids of its cards
        """
        closed = {
            "event": "show", "owner": nickname, "to": None,
            "cards": [], "ranks": [], "component_ids": [],
        }
        if not self.reads_show_of(user, nickname):
            return closed
        show = self.shows[nickname]
        return dict(
            closed,
            to=show["to"],
            cards=[
                {"src": card.image_src[card.side], "width": card.width, "height": card.height}
                for card in show["cards"]
            ],
            ranks=list(show["ranks"]),
            component_ids=[card.id for card in show["cards"]] if user.name == nickname else [],
        )

    def shows_json(self, user) -> list:
        """
        Every area user reads, its own first if any.
        """
        return [
            self.show_json(nickname, user)
            for nickname in sorted(self.shows, key=lambda name: name != getattr(user, 'name', None))
            if self.reads_show_of(user, nickname)
        ]

    def send_show(self, nickname: str) -> None:
        """
        Tells each player what it now reads of the area of a player. Not sent
        through send(): the faces are meant to be read here, and the watchers
        are told nothing.
        """
        for user in self.players:
            user.send(self.show_json(nickname, user))

    def hand_holding(self, component) -> str | None:
        """
        The nickname of the player keeping a component in a private hand, or
        None when it is on the table or in a bag.
        """
        for nickname, held in self.hands.items():
            if any(component is item for item in held):
                return nickname
        return None

    def put_in_hand(self, user: User, component, nickname: str | None = None) -> None:
        """
        Moves a token from the table into a private hand: it leaves "movable"
        and loses its position until its holder plays it again.
        :param nickname: the player whose hand takes it, user by default
        """
        movable = self.components_lists['movable']
        if component in movable:
            movable.remove(component)
        component.leave_table()
        self.enter_hand(nickname or user.name, component)

    def receives_hand_of(self, user: User, nickname) -> bool:
        """
        Whether user may put a token in the private hand of another player:
        any seat of the game but its own.
        """
        return (
            isinstance(nickname, str)
            and nickname != user.name
            and nickname in self.dealt_players()
        )

    def give_from_hand(self, user: User, component, nickname: str) -> bool:
        """
        Moves a token of the private hand of user into the hand of another
        player, without going through the table. A card shown from the hand
        of user leaves that area.
        :return: False when the token is not in the hand of user
        """
        held = self.hands.get(user.name, [])
        if not any(component is item for item in held):
            return False
        held.remove(component)
        if not held:
            del self.hands[user.name]
        self.remove_from_show(user.name, component)
        self.enter_hand(nickname, component)
        return True

    def enter_hand(self, nickname: str, component) -> None:
        """
        Adds a component to the private hand of a player. A card joins it face
        up and upright: its holder reads it at once, and it will show that face when it is
        played, unless the holder turns it over in the hand before. The other
        players never read the front of a card held in a hand (for_user).
        """
        if isinstance(component, Card):
            # a card without a back has one single face and keeps it
            component.set_side('front')
            # and a card held in a hand stands upright
            component.tap(0)
        self.hands.setdefault(nickname, []).append(component)

    def take_from_hand(self, user: User, component, x=None, y=None) -> bool:
        """
        Puts a token of the private hand of user back on the table, above the
        others, centered on (x, y) and held by that player like a token just
        acquired.
        :return: False when the token is not in the hand of user
        """
        held = self.hands.get(user.name, [])
        if not any(component is item for item in held):
            return False
        held.remove(component)
        if not held:
            del self.hands[user.name]
        # a card played is no longer shown from the hand
        self.remove_from_show(user.name, component)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            x = component.width / 2
            y = component.height / 2
        component.enter_table(x - component.width / 2, y - component.height / 2)
        self.components_lists['movable'].append(component)
        component.acquire(user)
        return True

    def hands_json(self) -> dict:
        """
        What everybody may know of the private hands: for each player, the
        back and the size of each token held, in order. Neither the id nor the
        front is given. A token without a back, or one that belongs to a
        nationality, is described without picture.
        :return: {nickname: [{"src", "width", "height"}, ...]}
        """
        return {
            nickname: [
                {
                    "src": token.image_src['back'] if token.nationality is None else None,
                    "width": token.width,
                    "height": token.height,
                }
                for token in held
            ]
            for nickname, held in self.hands.items()
        }

    def hand_json(self, user) -> list:
        """
        The private hand of user, each token fully described: only that
        player reads it.
        """
        if user is None or getattr(user, 'role', None) != 'player':
            return []
        return [token.return_json() for token in self.hands.get(user.name, [])]

    def bag_holding(self, component) -> Bag | None:
        """
        The bag a component is waiting in, or None when it is on the table.
        """
        for candidate in self.components_lists['fixed']:
            if isinstance(candidate, Bag) and candidate.holds(component):
                return candidate
        return None

    def bag_at(self, component, x, y) -> Bag | None:
        """
        The bag a token released at (x, y) falls into: the one its center lies
        on. Bags are drawn in list order, so the last one under the point wins.
        :param x: top-left corner of the token, as sent by the client
        :return: the bag, or None when the token lands on the table
        """
        if getattr(component, 'kind', None) != 'token':
            return None
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            return None
        center_x = x + component.width / 2
        center_y = y + component.height / 2
        for candidate in reversed(self.components_lists['fixed']):
            if isinstance(candidate, Bag) and candidate.contains(center_x, center_y):
                return candidate
        return None

    def character_at(self, component, x, y, user) -> MwCharacter | None:
        """
        The mw_character a card released at (x, y) goes under: the one its
        center lies on, if user may touch it (its nationality).
        :param x: top-left corner of the card, as sent by the client
        :return: the character, or None when the card stays on the table
        """
        if not isinstance(component, Card):
            return None
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            return None
        center_x = x + component.width / 2
        center_y = y + component.height / 2
        for candidate in reversed(self.components_lists['fixed']):
            if isinstance(candidate, MwCharacter) and candidate.contains(center_x, center_y):
                return candidate if candidate.acquirable_by(user) else None
        return None

    def character_holding(self, component) -> MwCharacter | None:
        """The mw_character a card lies under, or None."""
        for candidate in self.components_lists['fixed']:
            if isinstance(candidate, MwCharacter) and any(card is component for card in candidate.cards):
                return candidate
        return None

    def put_under_character(self, character: MwCharacter, card) -> None:
        """
        Moves a card from the table under an mw_character: it leaves
        "movable", loses its position, and lies face up and upright, its
        front read by everybody.
        """
        movable = self.components_lists['movable']
        if card in movable:
            movable.remove(card)
        card.leave_table()
        card.set_side('front')
        card.tap(0)
        character.cards.append(card)

    def take_from_character(self, character: MwCharacter, user: User, x=None, y=None):
        """
        Takes the last card put under an mw_character and puts it in the hand
        of user: it comes back on the table, above the others, centered on
        (x, y), face up.
        :return: the card, or None when there is none under the character
        """
        if not character.cards:
            return None
        card = character.cards.pop()
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            x = character.x + character.width / 2
            y = character.y + character.height / 2
        card.enter_table(x - card.width / 2, y - card.height / 2)
        self.components_lists['movable'].append(card)
        card.acquire(user)
        return card

    def put_in_bag(self, bag: Bag, component):
        """
        Moves a token from the table into a bag: it leaves "movable" and loses
        its position until somebody picks it.
        """
        movable = self.components_lists['movable']
        if component in movable:
            movable.remove(component)
        bag.add(component)

    def pick_from_bag(self, bag: Bag, user: User, x=None, y=None):
        """
        Takes a token out of a bag at random and puts it in the hand of user:
        it comes back on the table, above the others, centered on (x, y).
        :param x: where the player clicked; the center of the bag when the
            client gives no usable point
        :return: the token, or None when the bag holds none that user may
            take: it is empty, or all its tokens belong to another nationality
        """
        token = bag.pick(user)
        if token is None:
            return None
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            x = bag.x + bag.width / 2
            y = bag.y + bag.height / 2
        token.enter_table(x - token.width / 2, y - token.height / 2)
        self.components_lists['movable'].append(token)
        token.acquire(user)
        return token

    @staticmethod
    def build_board(component: dict) -> Board:
        """
        Builds a board from its game_json description, whether it is declared
        alone or inside a board_group.
        :param component: the board description from the game_json
        """
        return Board(
            component['id'],
            component['x'],
            component['y'],
            component['src'],
            component['width'],
            component['height'],
            # grid: optional hex grid released tokens snap to, when the
            # board_group holding the board has magnetism
            component.get('grid')
        )

    def build_board_group(self, component: dict) -> BoardGroup | None:
        """
        Builds a board_group and indexes each of its boards by its own id: the
        setup moves them and the grid snap reads them like boards declared
        alone. The group itself is not indexed: no action targets it, and a
        setup entry naming it is ignored like any unknown id.
        :param component: the board_group description from the game_json
        :return: the group, or None when it holds no board
        """
        boards = [
            build_checked(self.build_board, item, 'fixed', component.get('id'))
            for item in component.get('boards') or []
            # a group only holds boards: anything else is ignored, like an
            # unknown kind in the "fixed" list
            if isinstance(item, dict) and item.get('kind') == 'board'
        ]
        if not boards:
            return None
        for board in boards:
            self.components_dict[board.id] = board
        return BoardGroup(
            component['id'],
            boards,
            component.get('flippable', False),
            # magnetism: tokens snap to the grids of the boards only when true
            component.get('magnetism', False)
        )

    def boards(self) -> list:
        """
        Every board of the session in drawing order, the boards of a
        board_group taking the place of their group.
        :return: list of Board
        """
        boards = []
        for component in self.components_lists['fixed']:
            if isinstance(component, BoardGroup):
                boards.extend(component.boards)
            elif isinstance(component, Board):
                boards.append(component)
        return boards

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
        game_component = self.build_dice(component, list_name)
        self.components_lists['dice'].append(game_component)
        self.components_dict[component['id']] = game_component
        return game_component

    @staticmethod
    def build_dice(component: dict, list_name: str) -> Dice:
        """
        Builds a dice from its game_json description, whether it is declared
        alone or inside a dice_pool.
        :param component: the dice description from the game_json
        :param list_name: the list of the game_json it was declared in
        """
        return Dice(
            component['id'],
            component['x'],
            component['y'],
            component.get('src'),
            component['width'],
            component['height'],
            component['src_list'],
            list_name,
            # delay between two rolls, in seconds, chosen by the game; when the
            # game_json gives none, Dice applies its default
            component.get('roll_delay')
        )

    def build_dice_pool(self, component: dict) -> DicePool | None:
        """
        Builds a dice_pool and indexes each of its dice by its own id: a click
        rolls one of them alone and the setup moves it like a dice declared
        outside a pool. The dice stay inside the pool: they are not filed
        under "dice", the pool carries them in "fixed" and in the saved state.
        :param component: the dice_pool description from the game_json
        :return: the pool, or None when it holds no dice
        """
        dice = [
            build_checked(
                lambda described: self.build_dice(described, 'fixed'),
                item, 'fixed', component.get('id')
            )
            for item in component.get('dice') or []
            # a pool only holds dice: anything else is ignored, like an
            # unknown kind in the "fixed" list
            if isinstance(item, dict) and item.get('kind') == 'dice'
        ]
        if not dice:
            return None
        for item in dice:
            self.components_dict[item.id] = item
        return DicePool(component['id'], dice)

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
            # a deck stores its cards, which it keeps from the clients
            self._rounded_position(component.state_json())
            for component in self.components_lists['fixed']
        ] + dice_by_origin['fixed']
        state['movable'] = [
            self._rounded_position(component.return_json())
            for component in self.components_lists['movable']
        ] + dice_by_origin['movable']
        # une liste "dice" dédiée n'est écrite que si le jeu en a une
        if dice_by_origin['dice'] or 'dice' in state:
            state['dice'] = dice_by_origin['dice']
        # the order the players are listed in survives the session
        state.pop('player_order', None)
        if self.player_order:
            state['player_order'] = list(self.player_order)
        # the private hands are stored by nickname, and only when there are any
        state.pop('hands', None)
        if self.hands:
            state['hands'] = {
                nickname: [token.return_json() for token in held]
                for nickname, held in self.hands.items()
            }
        # the chat too, once somebody wrote in it
        state.pop('chat', None)
        if not self.chat.is_empty():
            state['chat'] = self.chat.state_json()
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
        # the boards of a board_group are saved inside it
        if isinstance(saved.get('boards'), list):
            saved['boards'] = [Session._rounded_position(board) for board in saved['boards']]
        # and so are the dice of a dice_pool
        if isinstance(saved.get('dice'), list):
            saved['dice'] = [Session._rounded_position(dice) for dice in saved['dice']]
        return saved

    def fix_positions_disabled(self) -> bool:
        """
        A game turns "Fixe la position" off by setting options.fix_positions
        to false. Absent or true, it stays on. A game saved before the option
        became a boolean may still carry null (off) or a position (on).
        :return: bool
        """
        option = self.options.get("fix_positions", True)
        return option is None or option is False

    def sees_hidden_face(self, user, token, side=None) -> bool:
        """
        Whether user may know the face a token does not show. A token that
        belongs to a nationality keeps it for the players of that nationality:
        the others, and every watcher, only know the face shown on the table.
        :param side: the side the token is described on, when it is not the
            one it shows right now
        """
        is_player = user is not None and getattr(user, 'role', None) == 'player'
        if token.nationality is not None and not (
            is_player and getattr(user, 'nationality', None) == token.nationality
        ):
            return False
        # the front of a card lying on its back is known to nobody, except
        # the player keeping that card in a private hand
        if isinstance(token, Card) and (side or token.side) == 'back':
            return is_player and self.hand_holding(token) == user.name
        return True

    @staticmethod
    def reads_hand_of(user, nickname) -> bool:
        """
        Whether user may read what the private hand of nickname holds: only
        the player of that seat does. Always true for None, the hand of
        nobody: the component is not in a hand.
        """
        if nickname is None:
            return True
        return (
            user is not None
            and getattr(user, 'role', None) == 'player'
            and user.name == nickname
        )

    def _pending_sides(self) -> dict:
        """
        The side the setup is about to give each token it turns over. Until
        the setup is applied, a token is still described on the side of the
        game_json, but the face the setup will hide must already be kept back.
        :return: {component_id: side}, empty once the setup is done
        """
        if self.setup_done:
            return {}
        return {entry['component_id']: entry['side'] for entry in self.setup if 'side' in entry}

    def for_user(self, payload, user):
        """
        What user may read of a message or of a session description: the same
        payload, in which every token description loses the face it does not
        show when user may not know it (sees_hidden_face). The payload itself
        is never modified.

        Every description of a token is recognised by its front_src and
        back_src keys, wherever it sits in the payload: a token json, a list
        of them, a flip event. A message sent through Session.send therefore
        cannot leak a hidden face by forgetting to ask.
        """
        if not self.hides_faces:
            return payload
        return self._without_hidden_faces(payload, user, self._pending_sides())

    def _without_hidden_faces(self, value, user, pending_sides: dict):
        if isinstance(value, list):
            return [self._without_hidden_faces(item, user, pending_sides) for item in value]
        if not isinstance(value, dict):
            return value
        cleaned = {
            key: self._without_hidden_faces(item, user, pending_sides)
            for key, item in value.items()
        }
        if 'front_src' in value and 'back_src' in value:
            token = self.components_dict.get(value.get('id', value.get('component_id')))
            if isinstance(token, Token):
                side = value.get('side', token.side)
                # a token the setup is about to turn over: the face to keep
                # back is the one the setup will hide, not the one the
                # game_json hides until then
                pending = pending_sides.get(token.id)
                if pending in HIDDEN_FACE_FIELDS and token.flippable():
                    side = pending
                if not self.sees_hidden_face(user, token, side):
                    cleaned[HIDDEN_FACE_FIELDS.get(side, 'back_src')] = None
                # a card kept in a private hand lies face up there, but only
                # its holder reads that face
                if isinstance(token, Card) and not self.reads_hand_of(user, self.hand_holding(token)):
                    cleaned['front_src'] = None
        return cleaned

    def return_session_json(self, user=None) -> dict:
        """
        returns the description of the session
        :param user: who will read it: the faces that user may not know are
            left out (for_user). None keeps everything.
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
            "participants": self.participants(),
            "nationalities": self.nationalities,
            "player_nationalities": self.player_nationalities,
            "documents": self.documents,
            # le setup en attente que le client applique : une fois la partie
            # installee, la liste est vide et personne ne rejoue la mise en place
            "setup": self.pending_setup(),
            "components": {
                "fixed" : [component.return_json() for component in self.components_lists["fixed"]],
                "movable": [component.return_json() for component in self.components_lists['movable']],
                "dice": [component.return_json() for component in self.components_lists['dice']]
            }
        }
        # hands: what everybody sees of the private hands; hand: the one of the
        # reader, with its faces
        session_json["hands"] = self.hands_json()
        # who is in the voice channel
        session_json["voice"] = self.voice_members()
        session_json["hand"] = self.hand_json(user)
        # shows: the cards the players show of their hand, as far as the
        # reader may read them
        session_json["shows"] = self.shows_json(user)
        # chat: the messages the reader may read, oldest first
        session_json["chat"] = self.chat.readable_by(user.name if user is not None else None)
        if user is not None:
            session_json["components"] = self.for_user(session_json["components"], user)
            session_json["hand"] = self.for_user(session_json["hand"], user)
        return session_json

    def refresh_seats(self, started: bool, player_names: list, player_nationalities: dict | None = None) -> None:
        """
        Reprend ce que Chabanas vient de dire de la session : les sieges
        enregistres, et si la partie a commence. Une partie commencee ne
        redevient jamais non commencee, meme sur une reponse en retard.
        """
        self.started = self.started or bool(started)
        self.player_names = list(player_names)
        self.remember_player_order(self.player_names)
        if player_nationalities is not None:
            self.player_nationalities = dict(player_nationalities)

    def remember_player_order(self, names) -> None:
        """
        A player met for the first time is listed after those already known:
        the order of arrival, which later arrivals never change.
        """
        for name in names:
            if name not in self.player_order:
                self.player_order.append(name)

    def ordered_seats(self) -> list:
        """
        The seats of the game, then the players at the table without a known
        seat, in the order they are listed in (player_order).
        """
        seats = list(self.player_names)
        seats += [user.name for user in self.players if user.name not in seats]
        self.remember_player_order(seats)
        return sorted(seats, key=self.player_order.index)

    def order_players(self, nicknames) -> bool:
        """
        Lists the players in the order the owner asked for. The players it did
        not name keep their order, after the others.
        :return: False when nicknames is not a list of players of the game,
            each named once
        """
        seats = self.ordered_seats()
        if (not isinstance(nicknames, list) or len(set(map(str, nicknames))) != len(nicknames)
                or any(name not in seats for name in nicknames)):
            return False
        named = list(nicknames)
        self.player_order = named + [name for name in self.player_order if name not in named]
        return True

    def missing_players(self) -> list:
        """
        Les joueurs qui ont un siege dans la partie mais ne sont pas connectes
        en tant que joueur.
        :return: leurs pseudos, dans l'ordre des sieges
        """
        connected = {user.name for user in self.players}
        return [name for name in self.player_names if name not in connected]

    def participants(self) -> list:
        """
        Everybody attached to the game, for the list each screen shows: the
        seats first, connected or not, then the watchers.
        :return: a list of {"nickname", "role", "connected"}; role is "player"
            or "watcher", and a watcher is always connected
        """
        connected = [user.name for user in self.players]
        watching = [user.name for user in self.watchers]
        # without a list of seats, the players at the table are all we know
        seats = self.ordered_seats()
        listed = []
        for name in seats:
            # a closed game turned its players into watchers: they keep their
            # seat on the list as long as they stay
            present = name in connected or (self.closed and name in watching)
            listed.append({"nickname": name, "role": "player", "connected": present})
        for name in watching:
            if self.closed and name in seats:
                continue
            listed.append({"nickname": name, "role": "watcher", "connected": True})
        return listed

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
            "participants": self.participants(),
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
                # the seat carries the nationality, not the connection
                user.nationality = self.player_nationalities.get(user.name)
                return True, None
            case 'watcher':
                if len(self.watchers) >= self.max_watchers:
                    # code stable : le client doit pouvoir dire a l'utilisateur
                    # que la partie refuse des spectateurs, pas qu'elle est pleine
                    return False, "watchers_full"
                self.watchers.append(user)
                user.role = 'watcher'
                user.nationality = None
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
        Removes a user from the session. A player leaving leaves the voice
        channel too: the others are told.
        """
        if user in self.players:
            self.players.remove(user)
        elif user in self.watchers:
            self.watchers.remove(user)
        if self.leave_voice(user):
            self.send_voice_members()

    def voice_members(self) -> list:
        """
        The nicknames of the players in the voice channel.
        """
        return [user.name for user in self.voice]

    def join_voice(self, user) -> list:
        """
        Puts a player in the voice channel. A connection that was never
        properly closed under the same nickname leaves it.
        :return: the nicknames of the players already there, whom the newcomer
            calls
        """
        self.voice = [member for member in self.voice if member is not user and member.name != user.name]
        peers = self.voice_members()
        self.voice.append(user)
        return peers

    def leave_voice(self, user) -> bool:
        """
        :return: False when user was not in the voice channel
        """
        if user not in self.voice:
            return False
        self.voice.remove(user)
        return True

    def voice_member(self, nickname):
        """
        The connection of a player in the voice channel, or None.
        """
        for user in self.voice:
            if user.name == nickname:
                return user
        return None

    def send_voice_members(self) -> None:
        """
        Tells everybody who is in the voice channel.
        """
        self.send({"event": "voice_members", "members": self.voice_members()})

    def chat_partner(self, user, nickname):
        """
        The player a private message of user goes to: another player of the
        game, at the table or not. Only a player writes privately.
        :return: its nickname, or None
        """
        if user.role != 'player' or not isinstance(nickname, str) or nickname == user.name:
            return None
        return nickname if nickname in self.ordered_seats() else None

    def post_chat(self, user, text: str, to: str | None = None) -> dict:
        """
        Adds a message to the chat and sends it to whoever reads its channel:
        everybody for the general channel, the two players for a private one.
        :return: the message
        """
        message = self.chat.post(user.name, text, to)
        event = {"event": "chat", "message": message}
        if to is None:
            for reader in self.players + self.watchers:
                reader.send(event)
        else:
            for reader in self.players + self.watchers:
                if reader.name in (user.name, to):
                    reader.send(event)
        return message

    def send(self, message):
        # each user gets its own reading of the message: the hidden face of a
        # token of another nationality never leaves the server
        for user in self.players:
            user.send(self.for_user(message, user))
        for user in self.watchers:
            user.send(self.for_user(message, user))

    def send_others(self, sending_user, message):
        for user in self.players:
            if sending_user != user:
                user.send(self.for_user(message, user))
        for user in self.watchers:
            user.send(self.for_user(message, user))

    def get_component(self, component_id):
        return self.components_dict.get(component_id, False)

    def snap_to_grid(self, component, x, y):
        """
        Where a token released at (x, y) lands: its center is pulled onto the
        center of the nearest hex of the board it is dropped on, when that board
        has a grid and its board_group has magnetism.

        A token dropped back near its starting square keeps that rule: place()
        snaps it there and gives it back its green rectangle, which wins over
        the grid.
        :param x: top-left corner of the token, as sent by the client
        :return: the (x, y) top-left corner to place the token at
        """
        if getattr(component, 'kind', None) != 'token':
            return x, y
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, y)):
            return x, y
        if component.move_border and component.near_initial_position(x, y):
            return x, y
        center_x = x + component.width / 2
        center_y = y + component.height / 2
        # boards are drawn in list order: the last one under the point is the
        # one the player sees, and only its grid counts
        for board in reversed(self.boards()):
            if not board.contains(center_x, center_y):
                continue
            snapped = board.snap_point(center_x, center_y)
            if snapped is None:
                return x, y
            return snapped[0] - component.width / 2, snapped[1] - component.height / 2
        return x, y

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

    def resolve_randomizers(self, setup: list) -> list:
        """
        Draws the components of each randomizer of the setup, at random, one
        for each of its targets, and turns the randomizer into the plain setup
        entries that lay them there. It is done once, when the session is
        built: every player then applies the same draw, and the setup stays
        idempotent. A session rebuilt before its setup is applied draws again,
        nothing having moved yet.

        A component is drawn at most once, across all the randomizers. A
        source holding fewer components than targets fills the first targets
        only. A target naming a deck or a stack only takes a card, and is
        skipped when that container does not exist or has no such pile.
        :param setup: the entries of read_setup
        :return: the setup without randomizers, in the same order
        """
        drawn = set()
        resolved = []
        for entry in setup:
            if entry.get('kind') != RANDOMIZER_KIND:
                resolved.append(entry)
                continue
            candidates = [
                component for component in self.randomizer_candidates(entry)
                if component.id not in drawn
            ]
            random.shuffle(candidates)  # NOSONAR
            for target in entry['targets']:
                if 'container_id' in target:
                    target = self.container_target(target)
                    if target is None:
                        continue
                    # only a card goes in a deck
                    component = next((card for card in candidates if isinstance(card, Card)), None)
                else:
                    component = candidates[0] if candidates else None
                if component is None:
                    continue
                candidates.remove(component)
                drawn.add(component.id)
                self.setup_take_out.add(component.id)
                resolved.append(dict(target, component_id=component.id))
        return resolved

    def container_target(self, target: dict) -> dict | None:
        """
        A randomizer target naming a deck or a stack, with the pile the card
        goes on: the draw pile when it names none.
        :return: None when there is no such deck or stack, or no such pile
        """
        container = self.components_dict.get(target['container_id'])
        pile = target.get('pile', 'draw')
        if not isinstance(container, Deck) or pile not in container.piles:
            return None
        return {'container_id': container.id, 'pile': pile}

    def randomizer_candidates(self, randomizer: dict) -> list:
        """
        The components a randomizer may draw: those of its container (a bag,
        or the draw pile of a deck or a stack), or those of its id list that
        are tokens or cards. Unknown ids, and a container of another kind than
        source_kind, give nothing.
        """
        source_id = randomizer['source_id']
        if randomizer['source_kind'] == RANDOMIZER_ID_LIST:
            candidates = []
            for component_id in source_id:
                component = self.components_dict.get(component_id)
                if isinstance(component, Token) and component not in candidates \
                        and self.hand_holding(component) is None \
                        and self.character_holding(component) is None:
                    candidates.append(component)
            return candidates
        container = self.components_dict.get(source_id)
        if getattr(container, 'kind', None) != randomizer['source_kind']:
            return []
        if isinstance(container, Bag):
            return list(container.components)
        if isinstance(container, Deck):
            return list(container.piles['draw'])
        return []

    def take_out_for_setup(self, component):
        """
        Takes a component a randomizer drew out of the bag or the deck holding
        it, onto the table, above the others. Its position is the setup's.
        :return: the container it left, or None when it was already out
        """
        container = self.bag_holding(component)
        if container is not None:
            container.components.remove(component)
        else:
            container = self.deck_holding(component)
            if container is None:
                return None
            container.piles[container.pile_holding(component)].remove(component)
        component.enter_table(0, 0)
        self.components_lists['movable'].append(component)
        return container

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
        # the bags and decks a randomizer took components out of: each screen
        # reads them again
        emptied = {}

        for entry in self.setup:
            component = self.components_dict.get(entry['component_id'])
            if component is None:
                # un setup qui parle d'un composant que le jeu ne declare pas
                # est une faute de saisie : on l'ignore, la partie demarre
                continue

            if 'container_id' in entry:
                # the card goes on a pile of a deck or a stack, as if it were
                # released on it; it is already there when the setup is applied
                # a second time
                target = self.components_dict.get(entry['container_id'])
                if self.deck_holding(component) is not target:
                    container = self.take_out_for_setup(component)
                    if container is not None:
                        emptied[container.id] = container
                    self.put_in_deck(target, entry['pile'], component)
                emptied[target.id] = target
                # each screen takes the card off the table: it has no position
                applied[component.id] = component.return_json()
                continue

            if component.id in self.setup_take_out:
                container = self.take_out_for_setup(component)
                if container is not None:
                    emptied[container.id] = container
                movable_ids.add(component.id)

            moved = False
            # a token inside a bag has no position: the setup may only choose
            # the face it will show when it comes out
            in_bag = (
                self.bag_holding(component) is not None
                or self.deck_holding(component) is not None
                or self.hand_holding(component) is not None
            )
            for field in SETUP_POSITION_FIELDS:
                if field in entry and not in_bag:
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

            # only a randomizer target gives an orientation
            if 'orientation' in entry and isinstance(component, Token):
                component.tap(entry['orientation'])

            # un composant peut figurer dans deux entrees, une qui le deplace et
            # une qui le retourne : on ne l'annonce qu'une fois, dans son etat
            # final
            applied[component.id] = component.return_json()

        for container in emptied.values():
            applied[container.id] = container.return_json()

        self.setup_done = True
        return list(applied.values())

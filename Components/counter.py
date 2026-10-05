from Components.component import Component
from user import User

# de combien un clic sur le + ou le - fait bouger la valeur
VALUE_STEP = 1

# couleur de fond quand le jeu n'en donne pas : le meme bleu que les zones
# de rotation d'un pion, pour que le compteur reste lisible sur n'importe quel
# plateau
DEFAULT_COUNTER_COLOR = "#528cff"

# texte du chiffre quand le jeu ne choisit pas font_color. Il ne peut pas
# prendre la couleur du fond : il y disparaitrait. Blanc ou noir selon que le
# fond est sombre ou clair, donc lisible dans les deux cas.
DEFAULT_DARK_FONT_COLOR = "#ffffff"
DEFAULT_LIGHT_FONT_COLOR = "#000000"


def read_rgb(color) -> tuple:
    """
    Lit une couleur "#rrggbb" en trois canaux. Une couleur que le serveur ne sait
    pas lire (un nom, un "#rgb", un "rgb(...)") ne doit pas casser le chargement
    du jeu : elle est simplement traitee comme inconnue.
    :param color: la couleur telle qu'ecrite dans le game_json
    :return: un triplet (r, g, b), ou None si la couleur n'est pas lisible
    """
    if not isinstance(color, str) or not color.startswith("#"):
        return None
    digits = color[1:]
    if len(digits) != 6:
        return None
    try:
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def readable_font_color(background) -> str:
    """
    Choisit la couleur du chiffre quand le jeu n'en donne pas. Le chiffre ne doit
    jamais se confondre avec son fond : on mesure la clarte du fond, et on prend
    le blanc sur un fond sombre, le noir sur un fond clair. Un fond que le serveur
    ne sait pas lire est traite comme sombre, le blanc restant le choix le plus
    sur.
    :param background: la couleur de fond du compteur
    :return: une couleur de texte lisible sur ce fond
    """
    rgb = read_rgb(background)
    if rgb is None:
        return DEFAULT_DARK_FONT_COLOR
    # ponderation rec. 601 : la perception de la clarte suit l'oeil, pas les
    # canaux bruts. Sous la moitie, le texte blanc contraste mieux.
    luma = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return DEFAULT_LIGHT_FONT_COLOR if luma >= 128 else DEFAULT_DARK_FONT_COLOR


def read_value(value) -> int:
    """
    Lit la valeur initiale d'un compteur. Le game_json peut porter un nombre, mais
    un absent, une chaine ou un booleen ne doivent pas casser la session : ils
    repartent de zero plutot que de lever une exception au chargement du jeu.
    :param value: le champ value tel que lu dans le game_json
    :return: un entier
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def read_limit(limit):
    """
    Lit une borne de compteur. Le jeu peut declarer min ou max a null pour dire
    que la valeur est libre de ce cote-la : une borne absente, vide ou fausse
    vaut donc "aucune borne", et pas zero.
    :param limit: le champ min ou max tel que lu dans le game_json
    :return: un entier, ou None quand la valeur n'est pas bornee
    """
    if limit is None or isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return None
    return int(limit)


class Counter(Component):
    """
    Un compteur de valeurs : un pave fige qui affiche un nombre, qu'on fait
    monter ou descendre d'un cran. Il n'a pas d'image : le fond et le chiffre
    sont dessines par le client, qui affiche aussi les deux zones + et -.

    Le jeu peut borner la valeur avec min et max, tous deux facultatifs : None
    d'un cote, la valeur est libre de ce cote. Le serveur fait autorite, ces
    bornes sont annoncees au client qui grise alors le signe impossible.
    """
    # counters are objects that display an integer value
    def __init__(self, component_id, x, y, width, height, color, value,
                 font_color=None, min_value=None, max_value=None):
        super().__init__(component_id)
        self.kind = 'counter'
        self.x = x
        self.y = y
        # couleur du fond du composant, comme un "#rrggbb" : c'est le jeu qui choisit, le
        # serveur ne fait que la transmettre
        self.color = color
        # couleur du chiffre, choisie independamment du fond : un jeu peut vouloir
        # un fond discret et un score qui se voit. Absente, le chiffre prend une
        # couleur lisible sur ce fond, jamais la couleur du fond elle-meme.
        self.font_color = font_color if font_color else readable_font_color(color)
        self.width = width
        self.height = height
        # bornes de la valeur, facultatives : None des deux cotes, le compteur est
        # libre. Un jeu qui declare min plus grand que max les intervertit plutot
        # que d interdire toute valeur : il veut une plage, pas un compteur bloque.
        self.min = read_limit(min_value)
        self.max = read_limit(max_value)
        if self.min is not None and self.max is not None and self.min > self.max:
            self.min, self.max = self.max, self.min
        # la valeur demarre dans la plage annoncee, sinon un compteur opens a 5
        # alors que son max vaut 3 afficherait d'emblee un etat impossible
        self.value = self.clamp_value(read_value(value))
        # inamovible comme un plateau, mais cliquable sur ses deux zones
        self.coordinates = (x, y)

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "color": self.color,
            "font_color": self.font_color,
            "value": self.value,
            "min": self.min,
            "max": self.max
        }

    def clamp_value(self, value: int) -> int:
        """
        Ramene une valeur dans la plage [min, max] du compteur. Utilise au
        chargement et apres chaque changement, pour qu'aucun chemin ne puisse
        sortir de la plage annoncee au client.
        :param value: la valeur a borner
        :return: la valeur, ramenee entre les bornes quand elles existent
        """
        if self.min is not None:
            value = max(value, self.min)
        if self.max is not None:
            value = min(value, self.max)
        return value

    def incrementable(self) -> bool:
        """
        Le + est disponible tant que la valeur peut monter. Le client s'en sert
        pour griser le signe, donc la reponse doit venir des bornes, pas d'un
        simple "c'est un compteur".
        :return: True si la valeur peut encore monter
        """
        return self.max is None or self.value < self.max

    def decrementable(self) -> bool:
        """
        Le - est disponible tant que la valeur peut descendre, jusqu'a min quand
        le jeu en donne un.
        :return: True si la valeur peut encore descendre
        """
        return self.min is None or self.value > self.min

    def increase_value(self, amount: int = VALUE_STEP) -> int:
        """
        Fait monter la valeur du compteur, sans jamais dépasser max. Le serveur
        fait autorite : le client n'audit que la demande, jamais la nouvelle
        valeur.
        :return: la valeur atteinte, bloquee sur max s'il est atteint
        """
        if not self.incrementable():
            return self.value
        self.value = self.clamp_value(self.value + amount)
        return self.value

    def decrease_value(self, amount: int = VALUE_STEP) -> int:
        """
        Fait descendre la valeur du compteur, sans jamais passer sous min. Sans
        borne basse, la valeur peut devenir negative : c'est au jeu de fixer un
        min s'il ne le veut pas.
        :return: la valeur atteinte, bloquee sur min s'il est atteint
        """
        if not self.decrementable():
            return self.value
        self.value = self.clamp_value(self.value - amount)
        return self.value
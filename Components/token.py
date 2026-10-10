from Components.component import Component
from user import User

# Un jeton lâché à moins de MOVE_THRESHOLD pixels de sa case de départ est
# automatiquement recadré dessus. Un pion "transparent" est recadré sur tout son
# fantôme, pas seulement dans ce rayon. La valeur est en dur dans le code :
# c'est une règle de jeu, pas un réglage.
MOVE_THRESHOLD = 30

# degres : un cran de rotation du pion. Le client ne propose que le sens du
# clic, c'est le serveur qui compte de combien ca tourne. 45 degres donne les
# 8 positions d'un pion pose droit, cliquables sans viser juste.
ROTATION_STEP = 45


def normalize_orientation(orientation) -> int:
    """
    Ramene un angle dans [0, 360). Huit crans de 45 degres font le tour
    complet : on veut lire 0 plutot que 360 sur tous les ecrans.
    :return: l'angle normalise, en degres entier
    """
    try:
        value = int(orientation)
    except (TypeError, ValueError):
        return 0
    return value % 360


class Token (Component):
    # tokens are objects that can be moved, flipped
    def __init__(self, component_id, x, y, front_image, back_image, width, height, move_border=True, initial=None, border=None, in_place=None, orientable=False, orientation=0, side=None, origin=None, origin_x=None, origin_y=None):
        super().__init__(component_id)
        self.kind = 'token'
        self.x = x
        self.y = y
        # origin : le jeu peut poser une image du pion en transparence sur sa
        # case de depart, pour marquer l'endroit ou il doit revenir. Null sur un
        # pion ordinaire ; "transparent" affiche ce fantome.
        self.origin = origin
        self.image_src = {
            "front": front_image,
            "back": back_image
        }
        # face affichee : une session reprise rend chaque pion sur la face qu'il
        # montrait. Un dos absent ramene toujours sur la face, faute d'image.
        self.side = 'back' if side == 'back' and back_image is not None else 'front'
        self.src = self.image_src[self.side]
        self.width = width
        self.height = height
        self.orientation = normalize_orientation(orientation)
        self.acquired_by = None
        self.coordinates = (self.x, self.y)
        # move_border : le jeton peut-il être repositionne pendant le tour
        self.move_border = move_border
        # orientable : le jeu autorise-t-il a faire pivoter le pion ? Le
        # client n'affiche les zones de rotation que si c'est vrai. Seule la
        # valeur booleenne True compte : une chaine "false" ne vaut pas plus
        # qu'un absent.
        self.orientable = orientable is True
        # emplacement initial, celui du jeu : y revenir rend le rectangle vert.
        # initial/in_place sont fournis quand la session est reprise apres une
        # sauvegarde.
        if initial is None:
            initial = (x, y)
        self.initial_x = initial[0]
        self.initial_y = initial[1]
        # origin_x / origin_y : ou se dessine le fantome "transparent". C'est la
        # place que le jeu a donnee au pion a son installation, et rien d'autre ne
        # la deplace : ni le setup d'un jeu, ni "Fixe la position", ni une reprise
        # de session. A l'installation elle vaut initial_x/initial_y, puis elle s'en
        # ecarte des que la partie bouge : le fantome est un repere du plateau, la
        # case de depart est ou revient le pion. Sans fantome, ces deux champs
        # valent None : ils ne decrivent rien.
        if origin == "transparent":
            self.origin_x = origin_x if origin_x is not None else x
            self.origin_y = origin_y if origin_y is not None else y
        else:
            self.origin_x = None
            self.origin_y = None
        # border : le jeu demande-t-il une ombre sous le pion ? Ce n'est qu'un
        # rendu, fige pour toute la partie : un pion deplace garde son ombre.
        # Absent d'un game_json de jeu, il n'y a pas d'ombre.
        self.border = border is True
        # in_place : le pion est-il sur sa case de depart ? C'est ce que montre
        # le rectangle vert, et lui disparait au premier deplacement, jusqu'au
        # prochain "fixe la position". Distinct de border, qui ne bouge jamais.
        # Un pion non repositionnable n'affiche jamais le rectangle vert.
        if in_place is None:
            in_place = move_border
        # a token waiting inside a bag has no starting square to stand on
        self.in_place = bool(in_place) and move_border and self.initial_x is not None

    def leave_table(self):
        """
        The token goes into a bag: it has no position any more, and nothing of
        the square it came from. Its face and its orientation are kept.
        """
        self.x = None
        self.y = None
        self.coordinates = (None, None)
        self.initial_x = None
        self.initial_y = None
        self.in_place = False

    def enter_table(self, x, y):
        """
        The token comes out of a bag at (x, y). It gets no starting square:
        only "fix positions" gives it one, like any token moved during the game.
        """
        self.x = x
        self.y = y
        self.coordinates = (x, y)

    def rotatable(self) -> bool:
        return self.orientable

    def rotate(self, delta: int) -> int:
        """
        Fait pivoter le pion de delta degres, vers la droite si positif.
        L'angle est ramene dans [0, 360) pour ne pas drift-er indefiniment.
        """
        self.orientation = normalize_orientation(self.orientation + delta)
        return self.orientation

    def tap(self, orientation: int):
        """
        Pose directement l'orientation du pion. Utilise par rotate(), mais aussi
        utile a un jeu qui veut placer un pion a un angle choisi.
        """
        self.orientation = normalize_orientation(orientation)


    def near_initial_position(self, x, y) -> bool:
        """
Un jeton est considéré comme posé à sa case de départ s'il est à moins
        de MOVE_THRESHOLD pixels d'elle.

        Un pion dont l'origine est "transparent" affiche un fantome sur sa case
        d'origine : le lâcher sur cette image le ramène chez lui, même si le
        pointeur vise un coin du fantôme plutôt que son centre. Le fantome reste
        sur sa case d'origine pendant que le setup et "Fixe la position"
        déplacent la case de retour : c'est le repère du plateau qui bouge, pas
        le pion.
        """
        # a token drawn from a bag has no starting square to go back to
        if self.initial_x is None or self.initial_y is None:
            return False
        dx = x - self.initial_x
        dy = y - self.initial_y
        if dx * dx + dy * dy <= MOVE_THRESHOLD * MOVE_THRESHOLD:
            return True
        if self.origin_x is not None and self.origin_y is not None:
            # les deux rectangles se recouvrent : le pion est posé sur l'image
            return abs(x - self.origin_x) < self.width and abs(y - self.origin_y) < self.height
        return False

    def acquire(self, user: User):
        if self.acquired_by is not None and self.acquired_by != user:
            print(f"acquired by: {self.acquired_by}")
            print(f"user: {user}")
            print(f"Tried to acquire {self.id} by {user.id}, but {self.acquired_by.id} already has it")
            return False
        self.acquired_by = user
        if self.id not in user.acquired:
            user.acquire(self.id)
        return True

    def flippable(self) -> bool:
        """
        Un jeton ne se retourne que si son game_json lui donne une image de dos.
        Un compteur a une seule face n'a rien a reveler : il reste tel quel.
        :return: True quand le jeton a une seconde face
        """
        return self.image_src['back'] is not None

    def flip(self):
        """
        Retourne le jeton, face avant puis face arriere. Un jeton sans image de
        dos garde sa face plutot que de pointer vers une image absente.
        """
        self.set_side('back' if self.side == 'front' else 'front')

    def set_side(self, side: str) -> bool:
        """
        Pose la face affichee du jeton sans le retourner : c'est une position de
        depart, comme le setup d'un jeu, pas un coup de partie. Un jeton sans
        image de dos garde sa face, faute d'image a montrer.
        :param side: "front" ou "back"
        :return: True quand la face a pu etre posee
        """
        if not self.flippable():
            return False
        self.side = 'back' if side == 'back' else 'front'
        self.src = self.image_src[self.side]
        return True

    def move(self, x, y, user: User):
        if self.acquired_by == user:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)
            # un jeton deplace perd son rectangle jusqu'au prochain "fixe la position"
            self.in_place = False

    def place(self, x, y, user: User):
        """
        Position finale au lacher. Contrairement a move(), elle n'exige pas que
        le jeton ait bouge pendant le glisser.

        Un jeton repositionnable lache près de sa case de départ y est recadre et
        recupere son rectangle vert ; sinon il reste où il a été deposé et perd la
        bordure.
        :return: True si le jeton etait bien tenu par user
        """
        if self.acquired_by != user:
            return False
        if self.move_border and self.near_initial_position(x, y):
            x, y = self.initial_x, self.initial_y
            self.in_place = True
        else:
            self.in_place = False
        self.x = x
        self.y = y
        self.coordinates = (self.x, self.y)
        return True

    def fix_position(self):
        """
        La position courante devient la nouvelle case de départ du jeton, qui
        recupere son rectangle vert s'il est repositionnable.

        Le fantome "transparent" ne bouge pas : cette action dit ou revient le
        pion, pas ou se trouve le repere que le jeu a pose sur son plateau.
        """
        self.initial_x = self.x
        self.initial_y = self.y
        self.in_place = self.move_border

    def release(self, user: User):
        if self.acquired_by != user:
            return False
        self.acquired_by = None
        user.release(self.id)
        return True

    def return_json(self, sat_list = None) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "x": self.x,
            "y": self.y,
            "side": self.side,
            "front_src": self.image_src["front"],
            "back_src": self.image_src["back"],
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation,
            "orientable": self.orientable,
            "move_border": self.move_border,
            "border": self.border,
            "in_place": self.in_place,
            "initial_x": self.initial_x,
            "initial_y": self.initial_y,
            "origin": self.origin,
            # ou le fantome est pose. Il ne bouge jamais : ces deux champs sont la
            # seule chose qui survive telle quelle a une sauvegarde de session
            "origin_x": self.origin_x,
            "origin_y": self.origin_y
        }

import random
import time

from Components.component import Component
from user import User

# delai applique quand le game_json ne dit rien (champ roll_delay absent ou
# inexploitable). Le serveur fait autorite : c'est lui qui refuse un clic trop
# rapproche.
DEFAULT_ROLL_DELAY_SECONDS = 5.0


def read_roll_delay(value) -> float:
    """
    Lit le delai de relance d'un de. Le game_json peut porter un roll_delay en
    secondes ; une valeur absente, non numerique ou negative retombe sur le
    defaut plutot que de casser la session. Zero est valide : il signifie
    qu'un jeu veut un de qu'on peut relancer librement.
    :param value: le champ roll_delay tel que lu dans le game_json
    :return: number of seconds, never negative
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_ROLL_DELAY_SECONDS
    if value < 0:
        return DEFAULT_ROLL_DELAY_SECONDS
    return float(value)


class Dice(Component):
    # dice can be moved, rolled
    def __init__(
            self,
            component_id,
            x,
            y,
            src,
            width,
            height,
            src_list,
            origin_list='fixed',
            roll_delay=None,
            ):
        super().__init__(component_id)
        self.id = component_id
        self.kind = 'dice'
        self.x = x
        self.y = y
        # un game_json de jeu ne porte pas de src : la face de depart est alors
        # la premiere de src_list. src est en revanche renvoyee par
        # return_json(), ce qui permet de retrouver la face affichee quand la
        # session est reprise apres une sauvegarde.
        self.src = src or (src_list[0] if src_list else None)
        self.width = width
        self.height = height
        self.src_list = list(src_list)
        self.acquired_by = None
        self.coordinates = (x, y)
        # liste du game_json dont ce de vient, pour le remettre la ou il etait
        # quand la situation de la session est sauvegardee
        self.origin_list = origin_list
        # delai propre a ce de, exprime dans le game_json du jeu
        self.roll_delay = read_roll_delay(roll_delay)
        self.rolled_at = None

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "src": self.src,
            # la liste des faces permet au client de les precharger : sans cela
            # changer de face afficherait un carre vide le temps du chargement
            "src_list": self.src_list,
            "width": self.width,
            "height": self.height,
            # renvoye au client pour qu'il verrouille le de du bon nombre de
            # secondes des le premier clic, avant meme la reponse du serveur
            "roll_delay": self.roll_delay
        }

    def move(self, x, y, user: User):
        if self.acquired_by == user:
            self.x = x
            self.y = y
            self.coordinates = (self.x, self.y)

    def roll(self) -> str:
        """
        Tire une face au hasard et ouvre le delai qui interdit de relancer.
        :return: l'image de la face tiree
        """
        self.src = random.choice(self.src_list)  # NOSONAR
        self.rolled_at = time.monotonic()
        return self.src

    def cooldown_remaining(self) -> float:
        """
        Delai restant avant que le de accepte un nouveau lancer.
        :return: number of seconds, 0 when the dice can be rolled
        """
        if self.rolled_at is None:
            return 0.0
        return max(0.0, self.roll_delay - (time.monotonic() - self.rolled_at))

    def clickable(self) -> bool:
        return True

    def is_rolling_allowed(self) -> bool:
        return self.cooldown_remaining() <= 0.0

    def roll_cooldown(self) -> float:
        return self.roll_delay

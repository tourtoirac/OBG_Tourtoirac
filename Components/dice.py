import random
import time

from Components.component import Component
from user import User

# delai pendant lequel le de ne peut pas etre relance apres un lancer. Le
# serveur fait autorite : c'est lui qui refuse un clic trop rapproche.
ROLL_COOLDOWN_SECONDS = 5.0


class Dice(Component):
    # dice can be moved, rolled
    def __init__(self, component_id, x, y, src, width, height, src_list, origin_list='fixed'):
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
            "height": self.height
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
        return max(0.0, ROLL_COOLDOWN_SECONDS - (time.monotonic() - self.rolled_at))

    def clickable(self) -> bool:
        return True

    def is_rolling_allowed(self) -> bool:
        return self.cooldown_remaining() <= 0.0

    def roll_cooldown(self) -> float:
        return ROLL_COOLDOWN_SECONDS

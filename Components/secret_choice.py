from Components.component import Component, nationality_allows, read_nationality
from user import User


def read_choices(choices) -> list:
    """
    Reads the choices a secret_choice offers: the non-empty strings of the
    list, each once, in the order of the game_json. Anything else is left out
    rather than breaking the load of the game.
    :param choices: the choices field as read from the game_json
    :return: a list of strings
    """
    if not isinstance(choices, list):
        return []
    read = []
    for choice in choices:
        if isinstance(choice, str) and choice.strip() and choice.strip() not in read:
            read.append(choice.strip())
    return read


class SecretChoice(Component):
    """
    A fixed component on which the players of one nationality choose one value
    among a few, in secret, then validate it and later reveal it to everybody:
    the Shadow Points letter of War of the Ring (sections D.3, D.6 and I).

    The game lays one per game turn, under the turn record track. The chosen
    value never leaves the server for the other players and the watchers until
    it is revealed (Session.for_user); once validated, it no longer changes.
    """

    def __init__(self, component_id, x, y, width, height, choices, nationality=None,
                 choice=None, validated=False, revealed=False):
        super().__init__(component_id)
        self.kind = 'secret_choice'
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.coordinates = (x, y)
        self.choices = read_choices(choices)
        # only the players of these nationalities choose; None, any player
        self.nationality = read_nationality(nationality)
        # the state of the round: only there on a saved session
        self.choice = choice if choice in self.choices else None
        self.validated = validated is True and self.choice is not None
        self.revealed = revealed is True and self.validated

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "choices": list(self.choices),
            "nationality": self.nationality,
            "choice": self.choice,
            "validated": self.validated,
            "revealed": self.revealed,
        }

    def acquirable_by(self, user: User) -> bool:
        """Who may choose, validate and reveal: a player of its nationality, anybody when it has none."""
        return nationality_allows(self.nationality, user)

    def readable_by(self, user) -> bool:
        """
        Whether user may know the chosen value: everybody once it is revealed,
        before that only the players who choose it.
        """
        if self.revealed:
            return True
        is_player = user is not None and getattr(user, 'role', None) == 'player'
        return is_player and self.acquirable_by(user)

    def choose(self, choice) -> bool:
        """
        Chooses a value; it may change until it is validated.
        :return: False when the value is unknown or already validated
        """
        if choice not in self.choices or self.validated:
            return False
        self.choice = choice
        return True

    def validate(self) -> bool:
        """Locks the chosen value. :return: False without a choice, or when already validated"""
        if self.choice is None or self.validated:
            return False
        self.validated = True
        return True

    def reveal(self) -> bool:
        """Shows the validated value to everybody. :return: False before validation, or when already revealed"""
        if not self.validated or self.revealed:
            return False
        self.revealed = True
        return True

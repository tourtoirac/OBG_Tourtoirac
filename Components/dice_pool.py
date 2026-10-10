from Components.component import Component
from Components.dice import Dice


class DicePool(Component):
    """
    One to n dice thrown together. The pool has no picture and no place of its
    own: it is a fixed, transparent component that only says which dice belong
    together, so the client can draw one button that rolls them all.

    Each dice of the pool is still a Dice of its own: it is indexed by its id,
    moved by the setup and rolled alone by a click exactly like a dice declared
    outside a pool.
    """
    def __init__(self, component_id, dice: list[Dice]):
        super().__init__(component_id)
        self.kind = 'dice_pool'
        self.dice = list(dice)

    def cooldown_remaining(self) -> float:
        """
        Delay before the whole pool can be thrown: the longest one left on its
        dice, since the pool is always thrown as a whole.
        :return: number of seconds, 0 when every dice can be rolled
        """
        return max((dice.cooldown_remaining() for dice in self.dice), default=0.0)

    def is_rolling_allowed(self) -> bool:
        return all(dice.is_rolling_allowed() for dice in self.dice)

    def roll(self) -> list:
        """
        Rolls every dice of the pool.
        :return: one {component_id, src, cooldown_seconds} per dice, in the
            order of the pool
        """
        return [
            {
                "component_id": dice.id,
                "src": dice.roll(),
                "cooldown_seconds": dice.roll_cooldown(),
            }
            for dice in self.dice
        ]

    def return_json(self, sat_list = None) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "dice": [dice.return_json() for dice in self.dice]
        }

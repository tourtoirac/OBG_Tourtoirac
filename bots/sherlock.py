"""
Sherlock Holmes, the card game, played by simulated players.

The server knows no rule: it moves cards. Everything of the game is here:

- the rules a card must follow to be played (legal);
- Referee, which follows the game from what one seat sees of the table:
  whose turn it is, the last card, the location, the draws a player owes;
- SherlockBot, one simulated player and its strategy;
- Game, which sets the table up and lets each bot play in its turn.

Humans may sit at the same table. The server gives no way to talk, so
whatever a rule asks of a human, or a human asks of a bot, goes through the
operator of the program (see Operator).
"""
import asyncio
import random
import time
from collections import defaultdict

from bots.client import BotError

# ids of the decks in the game_json of the game
PACK = "pack"
VILLAIN_DECK = "villains"
DEAL = "deal"

HAND_SIZE = 6

MOVES = {"train", "hansom"}
LONDON = {"london", "baker_street", "scotland_yard"}
LOCATIONS = LONDON | {"country"}
VILLAINS = {"moriarty", "moran", "clay", "milverton"}
DETECTIVES = {"holmes", "watson", "mycroft"}

# what a card costs the player still holding it when the round ends
POINTS = {
    "holmes": 50, "watson": 30, "mycroft": 40, "game_afoot": 0,
    "moriarty": 50, "moran": 40, "clay": 30, "milverton": 20,
    "train": 1, "hansom": 1, "thick_fog": 5,
    "country": 1, "london": 1, "baker_street": 2, "scotland_yard": 5,
    "clue": 5, "i_suspect": 5, "telegram": 10, "disguise": 10,
    "inspector": 15, "arrest": 15, "alibi": 15,
}

# the cards that may follow each card, as printed at its bottom. A move card
# is followed by a location, which depends on where the game is: see legal().
FOLLOWERS = {
    "game_afoot": MOVES,
    "alibi": MOVES,
    "arrest": MOVES,
    "inspector": {"arrest"} | MOVES,
    "i_suspect": {"arrest", "inspector"} | MOVES,
    "telegram": MOVES,
    "clue": {"telegram", "inspector", "disguise", "i_suspect"},
    "disguise": {"clue", "i_suspect"},
    "baker_street": {"disguise", "i_suspect", "telegram"} | MOVES,
    "london": {"arrest", "clue", "disguise"} | MOVES,
    "scotland_yard": {"arrest", "inspector", "disguise"} | MOVES,
    "country": {"arrest", "clue", "telegram"} | MOVES,
}

# cards a bot never plays: what they ask of the other players (pass a card
# to the left, shuffle every hand together, swap two hands) cannot be done
# with a human at the table without talking to them
NEVER_PLAYED = {"alibi", "thick_fog", "mycroft"}

# an Arrest that fails gives the whole hand of the accused to the accuser: a
# bot only tries it against a hand this small
ARREST_MAX_HAND = 3

# seconds a human is given to show its hand to the bot that looks at it
SHOW_TIMEOUT = 120.0


def location_type(card: str) -> str | None:
    if card in LONDON:
        return "london"
    if card == "country":
        return "country"
    return None


def legal(card: str, last: str | None, location: str, hand: list) -> bool:
    """
    Whether a card may be played in turn.
    :param last: the card to follow: the last one played, a detective
        (Holmes, Watson, Mycroft) leaving the one before it in force
    :param location: "london" or "country", where the game is
    :param hand: the names of the cards of the player, the card included
    """
    if last is None:
        # nothing is played before "The game is afoot!"
        return card == "game_afoot"
    if card in VILLAINS:
        # a villain only leaves a hand that holds nothing else
        return all(held in VILLAINS for held in hand)
    if card == "alibi" or card == "game_afoot":
        # an Alibi is only played out of turn, against an accusation
        return False
    if card in ("holmes", "watson"):
        return last not in MOVES and last != "thick_fog"
    if card == "mycroft":
        return location == "london" and last in LOCATIONS
    if last == "thick_fog":
        return card in LOCATIONS
    if last == "hansom":
        return card == "thick_fog" or location_type(card) == location
    if last == "train":
        return card == "thick_fog" or (card in LOCATIONS and location_type(card) != location)
    return card in FOLLOWERS.get(last, ())


def hand_value(names: list) -> int:
    """
    What a hand costs at the end of a round. The villains are left out: the
    rules only count them when they are detected, and then for the players
    they name.
    """
    return sum(POINTS.get(name, 0) for name in names if name not in VILLAINS)


class Operator:
    """
    The person running the program. It reads what the bots do, and answers
    for the humans of the table when a bot needs to know something only a
    human can tell. This one says nothing and answers "not guilty".
    """

    def say(self, text: str) -> None:
        pass

    async def ask_guilty(self, accuser: str, target: str) -> bool:
        return False

    async def confirm(self, text: str) -> None:
        pass


class Referee:
    """
    Follows the game from the events one seat receives. It enforces nothing:
    a human plays what they want, the referee only keeps track of it.
    """

    def __init__(self, operator: Operator, bots=()):
        self.operator = operator
        # nicknames of the simulated players: they end their own turn
        self.bots = set(bots)
        self.players = []
        # whose turn it is; None until "The game is afoot!" is played
        self.current = None
        self.last = None
        self.location = "london"
        # draws each player still owes, by nickname
        self.owed = defaultdict(int)
        # points given by the Telegram cards, by nickname
        self.penalties = defaultdict(int)
        # why the round is over, or None while it goes on
        self.round_over = None
        # the human who ended a turn by keeping the card drawn, as long as
        # nobody played since: that card may still be played
        self.passed_by_keep = None
        self.turn_changed_at = time.monotonic()
        # players in a row who could neither play nor draw
        self.stuck = 0
        # number of cards played so far
        self.plays = 0
        # card id -> (nickname, forced) for the cards drawn from the pack
        self._drawn = {}
        self.changed = asyncio.Event()

    def next_player(self, nickname: str) -> str | None:
        if nickname not in self.players:
            return self.players[0] if self.players else None
        return self.players[(self.players.index(nickname) + 1) % len(self.players)]

    def observe(self, client, message: dict) -> None:
        """Takes in a message received by the seat the referee watches."""
        self.players = list(client.players)
        event = message.get("event")
        if event == "draw":
            self._on_draw(message)
        elif event == "release" and message.get("success"):
            self._on_release(message, client)
        self.changed.set()

    def _on_draw(self, message: dict) -> None:
        if message.get("component_id") != PACK or message.get("pile") != "draw":
            return
        player = message.get("nickname")
        forced = self.owed[player] > 0
        if forced:
            self.owed[player] -= 1
        self._drawn[(message.get("component_json") or {}).get("id")] = (player, forced)

    def _on_release(self, message: dict, client) -> None:
        player = message.get("nickname")
        card_id = message.get("component_id")
        if message.get("deck_id") == PACK and message.get("pile") == "discard":
            self._drawn.pop(card_id, None)
            name = client.pile_top_name(PACK, "discard")
            if name is not None:
                self.played(player, name)
        elif message.get("hand_of") and card_id in self._drawn:
            _, forced = self._drawn.pop(card_id)
            # a human who keeps the card drawn in turn passes; a bot says so
            # itself, once it has seen the card
            if not forced and player == self.current and player not in self.bots:
                self.passed_by_keep = player
                self._turn_to(self.next_player(player))

    def played(self, player: str, name: str) -> None:
        in_turn = self.current is None or player == self.current
        self.stuck = 0
        self.plays += 1
        self.operator.say(f"{player} joue {name}" + ("" if in_turn else " (hors de son tour)"))
        if name in VILLAINS:
            self.finish(f"{player} s'échappe avec {name}")
        if name not in DETECTIVES:
            # a detective leaves the previous card in force
            self.last = name
        if name in LOCATIONS:
            self.location = location_type(name)
        if not in_turn:
            # an Alibi, or the card a human drew, looked at and then played:
            # the turn already moved on
            self.passed_by_keep = None
            return
        following = self.next_player(player)
        if name == "scotland_yard" and following is not None:
            self.owe(following, 2)
        if name == "telegram" and following is not None:
            self.penalties[following] += 10
            self.operator.say(f"Télégramme : {following} ajoute 10 points à son score")
        self.passed_by_keep = None
        self._turn_to(following)

    def _turn_to(self, nickname: str | None) -> None:
        self.current = nickname
        self.turn_changed_at = time.monotonic()
        self.changed.set()

    def end_turn(self, nickname: str, stuck: bool = False) -> None:
        """
        A bot passes without playing.
        :param stuck: it had nothing to draw either. When every player in a
            row is in that case, nobody will ever play again.
        """
        self.stuck = self.stuck + 1 if stuck else 0
        if self.players and self.stuck >= len(self.players):
            self.finish("plus personne ne peut jouer ni piocher")
        if self.current == nickname:
            self._turn_to(self.next_player(nickname))

    def set_turn(self, nickname: str) -> None:
        """The operator puts the game back on its feet."""
        self.passed_by_keep = None
        self._turn_to(nickname)

    def owe(self, nickname: str, count: int) -> None:
        self.owed[nickname] += count
        if nickname not in self.bots:
            self.operator.say(f"{nickname} doit piocher {count} carte(s)")
        self.changed.set()

    def finish(self, reason: str) -> None:
        if self.round_over is None:
            self.round_over = reason
            self.operator.say(f"Fin de la manche : {reason}")
        self.changed.set()


class SherlockBot:
    """One simulated player."""

    def __init__(self, client, game):
        self.client = client
        self.game = game

    @property
    def name(self) -> str:
        return self.client.nickname

    @property
    def referee(self) -> Referee:
        return self.game.referee

    def say(self, text: str) -> None:
        self.game.operator.say(text)

    def card_ids(self, name: str) -> list:
        return [card_id for card_id in self.client.hand if self.client.cards[card_id].name == name]

    def opponents(self) -> list:
        return [nickname for nickname in self.client.players if nickname != self.name]

    def hand_count(self, nickname: str) -> int:
        return self.client.hand_counts.get(nickname, 0)

    def smallest_hand(self) -> str | None:
        """The opponent the closest to going out, among those holding cards."""
        holders = [nickname for nickname in self.opponents() if self.hand_count(nickname) > 0]
        if not holders:
            return None
        fewest = min(self.hand_count(nickname) for nickname in holders)
        return random.choice([n for n in holders if self.hand_count(n) == fewest])

    def choose(self) -> str | None:
        """
        The card to play, or None to draw. The strategy is plain: get rid of
        the card that would cost the most points, without playing the cards
        a bot cannot carry out.
        """
        names = self.client.hand_names()
        referee = self.referee
        target = self.smallest_hand()
        playable = []
        for name in set(names):
            if name is None or name in NEVER_PLAYED:
                continue
            if not legal(name, referee.last, referee.location, names):
                continue
            if name in ("arrest", "holmes", "watson", "i_suspect", "inspector") and target is None:
                continue
            if name == "arrest" and self.hand_count(target) > ARREST_MAX_HAND:
                continue
            playable.append(name)
        if not playable:
            return None
        best = max(POINTS.get(name, 0) for name in playable)
        return random.choice(sorted(name for name in playable if POINTS.get(name, 0) == best))

    async def settle_owed(self) -> None:
        """Draws the cards another player made this one take."""
        # the count is taken at once: the referee only hears of each draw
        # when the seat it watches does, which may be a moment later
        count, self.referee.owed[self.name] = self.referee.owed[self.name], 0
        if count:
            self.say(f"{self.name} pioche {count} carte(s) imposée(s)")
        for _ in range(count):
            if self.referee.round_over is not None or not await self._draw():
                return

    async def _draw(self):
        """Draws a card of the pack into the hand; None when there is none left."""
        client = self.client
        if client.pile_count(PACK, "draw") == 0:
            if client.pile_count(PACK, "discard") == 0:
                self.say("La pioche et la défausse sont vides")
                return None
            self.say(f"{self.name} remet la défausse dans la pioche et mélange")
            await client.refill(PACK)
            await client.shuffle(PACK, "draw")
        return await client.draw_to_hand(PACK, "draw")

    async def play_turn(self) -> None:
        await self.settle_owed()
        if self.referee.round_over is not None:
            return
        name = self.choose()
        if name is None:
            self.say(f"{self.name} pioche")
            drawn = await self._draw()
            # a card just drawn may be put down at once
            name = self.choose() if drawn is not None else None
            if name is None or drawn is None or drawn.name != name:
                self.referee.end_turn(self.name, stuck=drawn is None)
                return
        await self.play(name)

    async def play(self, name: str) -> None:
        names = self.client.hand_names()
        if name in VILLAINS:
            # nothing but villains left: they all escape together
            for card_id in list(self.client.hand):
                await self.discard(card_id)
            return
        await self.discard(self.card_ids(name)[0])
        await self.effect(name)
        if not self.client.hand and self.referee.round_over is None and len(names) == 1:
            await self.going_out()

    async def discard(self, card_id: str) -> None:
        """
        Puts a card of the hand on the discard pile, and waits for the
        referee to hear of it: its seat may receive the event a moment after
        this one.
        """
        plays = self.referee.plays
        await self.client.hand_to_pile(card_id, PACK, "discard")
        await self.game.wait_until(lambda: self.referee.plays > plays)

    async def effect(self, name: str) -> None:
        if name in ("i_suspect", "inspector"):
            target = self.smallest_hand()
            if target is not None:
                count = 1 if name == "i_suspect" else 2
                self.say(f"{self.name} désigne {target} : {count} carte(s) à piocher")
                self.referee.owe(target, count)
        elif name in ("holmes", "watson"):
            target = self.smallest_hand()
            if target is not None:
                self.say(f"{self.name} inspecte la main de {target}")
                await self.accuse(target, takes_hand=False)
        elif name == "arrest":
            target = self.smallest_hand()
            if target is not None:
                self.say(f"{self.name} arrête {target}")
                await self.accuse(target, takes_hand=True)

    async def accuse(self, target: str, takes_hand: bool) -> bool:
        """
        Looks at the hand of an opponent. A villain there ends the round.
        :param takes_hand: an Arrest that fails gives the hand of the accused
            to the accuser, and the accused draws as many cards again
        :return: True when the opponent was guilty
        """
        if await self.game.is_guilty(self.name, target):
            self.referee.finish(f"{self.name} arrête {target}, qui tenait un Villain")
            return True
        self.say(f"{target} n'est pas coupable")
        if takes_hand:
            await self.game.give_hand(target, self)
        return False

    async def going_out(self) -> None:
        """
        A player who discarded every card arrests an opponent; an innocent
        accused arrests somebody else in turn, until a villain is found.
        """
        self.say(f"{self.name} n'a plus de carte")
        accuser = self
        accused = set()
        while self.referee.round_over is None:
            targets = [
                nickname for nickname in accuser.opponents()
                if nickname not in accused and accuser.hand_count(nickname) > 0
            ]
            if not targets:
                self.referee.finish("personne ne tient de Villain")
                return
            target = random.choice(targets)
            accused.add(target)
            self.say(f"{accuser.name} accuse {target}")
            if await accuser.accuse(target, takes_hand=False):
                return
            following = self.game.bots.get(target)
            if following is None:
                # a human accuses next: the program cannot follow that
                self.referee.finish(f"{target} accuse à son tour : la manche se termine à la main")
                return
            accuser = following


class Game:
    """
    The simulated players of one table and what makes them play: the setup
    of the round, then each bot in its turn.

    :param clients: the seats of the bots, already at the table
    :param delay: seconds a bot waits before acting, so that the humans can
        follow what happens
    :param grace: seconds more left to a human who drew a card to play it
    """

    def __init__(self, clients: list, operator: Operator | None = None, delay: float = 1.5, grace: float = 4.0,
                 show_timeout: float = SHOW_TIMEOUT):
        self.operator = operator or Operator()
        self.clients = clients
        self.delay = delay
        self.grace = grace
        self.show_timeout = show_timeout
        self.referee = Referee(self.operator, [client.nickname for client in clients])
        self.bots = {client.nickname: SherlockBot(client, self) for client in clients}
        # the referee watches the table through the first seat
        self.watcher = clients[0]
        self.watcher.listeners.append(lambda message: self.referee.observe(self.watcher, message))
        self.referee.players = list(self.watcher.players)
        self.stopped = False

    # ------------------------------------------------------------------
    # what a rule asks of two players at once
    # ------------------------------------------------------------------

    async def is_guilty(self, accuser: str, target: str) -> bool:
        bot = self.bots.get(target)
        if bot is not None:
            return any(name in VILLAINS for name in bot.client.hand_names())
        shown = await self.read_shown_hand(accuser, target)
        if shown is not None:
            return any(name in VILLAINS for name in shown)
        return await self.operator.ask_guilty(accuser, target)

    async def read_shown_hand(self, accuser: str, target: str) -> list | None:
        """
        Has a human show its hand to the bot looking at it, when the game
        lets the players show their cards (show_hands_to_other_players).
        :return: the names of the cards of the hand, or None when the game
            has no such area or the human did not show its whole hand in time
        """
        reader = self.bots[accuser].client
        if not reader.can_show_hands():
            return None
        count = reader.hand_counts.get(target, 0)
        self.operator.say(
            f"{target} : montre ta main à {accuser} (« Show hands to ... » → {accuser}, "
            f"puis glisse tes {count} carte(s) face visible dans la zone)")

        def whole_hand() -> bool:
            names = reader.shown_names(target)
            # a card lying on its back does not tell what it is
            return len(names) >= count and all(name in POINTS for name in names)

        if not await self.wait_until(whole_hand, timeout=self.show_timeout):
            self.operator.say(f"{target} n'a pas montré toute sa main")
            return None
        names = reader.shown_names(target)
        self.operator.say(f"{accuser} a vu la main de {target} : la zone peut être fermée")
        return names

    async def show_hand(self, bot: SherlockBot, to: str) -> None:
        """A bot shows its whole hand to a player, or to "all" of them."""
        await bot.client.show_hand(to)
        self.operator.say(f"{bot.name} montre sa main à {'tous les joueurs' if to == 'all' else to}")

    async def give_hand(self, giver: str, receiver: SherlockBot) -> None:
        """
        The whole hand of a player goes to a bot, face down across the table,
        and the giver owes as many draws.
        """
        count = receiver.hand_count(giver)
        bot = self.bots.get(giver)
        if bot is not None:
            await self.dump_hand(bot)
        else:
            await self.operator.confirm(
                f"{giver} : retourne tes {count} carte(s) dans ta main (double-clic), "
                f"puis pose-les face cachée sur la table pour {receiver.name}")
        # the seat of the receiver may hear of these cards a moment later
        await self.wait_until(lambda: len(receiver.client.loose_cards()) >= count)
        for card in receiver.client.loose_cards():
            await receiver.client.table_to_hand(card)
        self.referee.owe(giver, count)

    async def dump_hand(self, bot: SherlockBot) -> int:
        """A bot lays its whole hand on the table, face down, in a row."""
        count = len(bot.client.hand)
        for index, card_id in enumerate(list(bot.client.hand)):
            await bot.client.hand_to_table(card_id, 300 + index * 60, 950, face_down=True)
        return count

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def table_is_fresh(self) -> bool:
        client = self.watcher
        return (
            client.pile_count(PACK, "draw") == 103
            and client.pile_count(PACK, "discard") == 0
            and client.pile_count(VILLAIN_DECK) == 4
            and client.pile_count(DEAL) == 0
        )

    async def setup(self) -> None:
        """
        Prepares the round as the rules say: one villain and "The game is
        afoot!" are shuffled with enough cards to deal six to each player,
        and the three other villains go into the draw pile.
        """
        dealer = self.watcher
        players = list(dealer.players)
        self.operator.say(f"Mise en place pour {len(players)} joueurs : {', '.join(players)}")
        await dealer.shuffle(VILLAIN_DECK)
        await dealer.draw_to_pile(VILLAIN_DECK, "draw", DEAL)
        # "The game is afoot!" is the only card lying on the table
        for card in dealer.loose_cards():
            await dealer.table_to_pile(card, DEAL)
        await dealer.shuffle(PACK)
        await dealer.draw_to_pile(
            PACK, "draw", DEAL, count=HAND_SIZE * len(players) - dealer.pile_count(DEAL))
        await dealer.shuffle(DEAL)
        if dealer.is_stack(DEAL) and dealer.private_hand() is not None:
            # a stack deals by itself: one card to every player, humans
            # included, straight into the private hands
            for _ in range(HAND_SIZE):
                await dealer.deal(DEAL)
            await self.wait_until(
                lambda: all(len(bot.client.hand) >= HAND_SIZE for bot in self.bots.values()))
        else:
            await self._deal_by_hand(players)
        while dealer.pile_count(VILLAIN_DECK) > 0:
            await dealer.draw_to_pile(VILLAIN_DECK, "draw", PACK)
        await dealer.shuffle(PACK)
        self.operator.say("Mise en place terminée : celui qui a « The game is afoot! » la pose")

    async def _deal_by_hand(self, players: list) -> None:
        """
        The deal when "Donne" is a plain deck: each bot draws its cards, and
        the humans are asked to draw theirs.
        """
        dealer = self.watcher
        for bot in self.bots.values():
            for _ in range(HAND_SIZE):
                await bot.client.draw_to_hand(DEAL)
        humans = [nickname for nickname in players if nickname not in self.bots]
        if humans:
            self.operator.say(
                f"{', '.join(humans)} : piochez {HAND_SIZE} cartes chacun dans le paquet « Donne »")
            while dealer.pile_count(DEAL) > 0 and not self.stopped:
                await self._wait_change()

    # ------------------------------------------------------------------
    # the game
    # ------------------------------------------------------------------

    async def wait_until(self, condition, timeout: float = 5.0) -> bool:
        """
        Waits for something the seats learn from the server.
        :return: False when it did not happen in time
        """
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    async def _wait_change(self, timeout: float = 1.0) -> None:
        self.referee.changed.clear()
        try:
            await asyncio.wait_for(self.referee.changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def step(self) -> bool:
        """
        Does the next thing a bot has to do, if any.
        :return: False when there was nothing to do
        """
        referee = self.referee
        if not self.watcher.ready or referee.round_over is not None:
            return False
        for bot in self.bots.values():
            if referee.owed[bot.name] > 0:
                await bot.settle_owed()
                return True
        if referee.current is None:
            # the round starts with whoever holds "The game is afoot!"
            for bot in self.bots.values():
                if bot.card_ids("game_afoot"):
                    await asyncio.sleep(self.delay)
                    await bot.discard(bot.card_ids("game_afoot")[0])
                    return True
            return False
        bot = self.bots.get(referee.current)
        if bot is None:
            return False
        # a human who kept the card drawn may still put it down
        wait = self.delay + (self.grace if referee.passed_by_keep else 0)
        remaining = referee.turn_changed_at + wait - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)
            return True
        await bot.play_turn()
        return True

    async def run(self) -> None:
        """Plays until the round is over or the game is stopped."""
        while not self.stopped and self.referee.round_over is None:
            try:
                if not await self.step():
                    await self._wait_change()
            except BotError as error:
                # a refusal of the server: say it and let the table settle
                self.operator.say(f"Action refusée ({error})")
                await asyncio.sleep(max(self.delay, 0.01))
        if self.referee.round_over is not None:
            self.report()

    def report(self) -> None:
        for bot in self.bots.values():
            names = [name or "?" for name in bot.client.hand_names()]
            penalty = self.referee.penalties[bot.name]
            self.operator.say(
                f"{bot.name} : {hand_value(names)} points en main"
                + (f" + {penalty} de télégramme" if penalty else "")
                + f" ({', '.join(sorted(names)) or 'aucune carte'})")

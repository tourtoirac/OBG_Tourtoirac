"""
A player of a table, seen from its own seat: what it knows of the cards, of
the decks and of the hands, and the actions it may ask of the server.

The client knows no rule of any game. It only keeps what the server tells
this seat, and nothing more: a card it never saw the front of has no name.
"""
import asyncio
import itertools
import os

# seconds an action waits for the answer of the server
ACTION_TIMEOUT = 10


class BotError(Exception):
    """The server refused an action, or did not answer it."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def card_name(front_src) -> str | None:
    """
    What a card is, read from the address of its front: "/x/train.png" is a
    "train". None while the server keeps the front back.
    """
    if not isinstance(front_src, str) or not front_src:
        return None
    return os.path.splitext(os.path.basename(front_src))[0]


class Card:
    """A card as this seat knows it."""

    def __init__(self, description: dict):
        self.id = description["id"]
        self.name = None
        self.side = "front"
        self.x = None
        self.y = None
        self.width = description.get("width", 0)
        self.height = description.get("height", 0)
        # lying on the table, held or not; False inside a deck or a hand
        self.on_table = False
        # nickname of the player holding it with the mouse
        self.held_by = None
        self.update(description)

    def update(self, description: dict) -> None:
        name = card_name(description.get("front_src"))
        if name is not None:
            self.name = name
        if description.get("side") in ("front", "back"):
            self.side = description["side"]
        self.x = description.get("x", self.x)
        self.y = description.get("y", self.y)


class TableClient:
    """
    One seat at a table. Messages of the server go through handle(), which
    keeps the picture of the table up to date; the actions are coroutines
    that send a request and wait for the event answering it.

    :param nickname: the nickname of the seat
    :param send: coroutine function taking the message (a dict) to send
    """

    def __init__(self, nickname: str, send):
        self.nickname = nickname
        self._send = send
        self.joined = False
        self.started = False
        self.missing_players = []
        # nicknames of the seats, in the order the server lists them
        self.players = []
        self.options = {}
        self.cards = {}
        # ids of the cards of the private hand of this seat, in order
        self.hand = []
        # number of cards each player keeps in hand, by nickname
        self.hand_counts = {}
        # deck id -> description of the deck (its piles, without the cards)
        self.decks = {}
        # called with every message, once the state has taken it in
        self.listeners = []
        self._waiters = []
        self._request_ids = itertools.count(1)

    # ------------------------------------------------------------------
    # what the server says
    # ------------------------------------------------------------------

    def handle(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        event = message.get("event")
        handler = getattr(self, f"_on_{event}", None)
        if handler is not None:
            handler(message)
        for listener in list(self.listeners):
            listener(message)
        for waiter in list(self._waiters):
            predicate, future = waiter
            if future.done():
                self._waiters.remove(waiter)
            elif event == "error":
                self._waiters.remove(waiter)
                error = message.get("error") or {}
                future.set_exception(BotError(error.get("code", "error"), error.get("message", "")))
            elif predicate(message):
                self._waiters.remove(waiter)
                future.set_result(message)

    def _on_session_joined(self, message: dict) -> None:
        session = message.get("session") or {}
        self.joined = True
        self.options = session.get("options") or {}
        self._read_status(session)
        components = session.get("components") or {}
        for item in components.get("fixed") or []:
            if item.get("kind") == "deck":
                self.decks[item["id"]] = item
        for item in components.get("movable") or []:
            if item.get("kind") == "card":
                self._card(item).on_table = True
        self.hand = []
        for item in session.get("hand") or []:
            card = self._card(item)
            card.on_table = False
            self.hand.append(card.id)
        self._read_hands(session.get("hands"))

    def _on_session_status(self, message: dict) -> None:
        self._read_status(message)

    def _read_status(self, source: dict) -> None:
        if "started" in source:
            self.started = bool(source["started"])
        if isinstance(source.get("missing_players"), list):
            self.missing_players = list(source["missing_players"])
        participants = source.get("participants")
        if isinstance(participants, list):
            self.players = [
                entry["nickname"] for entry in participants if entry.get("role") == "player"
            ]

    def _read_hands(self, hands) -> None:
        if isinstance(hands, dict):
            self.hand_counts = {nickname: len(held) for nickname, held in hands.items()}

    def _read_deck(self, deck_json) -> None:
        if isinstance(deck_json, dict) and "id" in deck_json:
            self.decks[deck_json["id"]] = deck_json

    def _card(self, description: dict) -> Card:
        card = self.cards.get(description["id"])
        if card is None:
            card = self.cards[description["id"]] = Card(description)
        else:
            card.update(description)
        return card

    def _on_acquire(self, message: dict) -> None:
        card = self.cards.get(message.get("component_id"))
        if card is not None and message.get("success"):
            card.held_by = message.get("nickname")

    def _on_release(self, message: dict) -> None:
        if not message.get("success"):
            return
        self._read_hands(message.get("hands"))
        self._read_deck(message.get("deck_json"))
        description = message.get("component_json") or {}
        if description.get("kind") != "card":
            return
        card = self._card(description)
        card.held_by = None
        if message.get("hand_of"):
            card.on_table = False
            if message["hand_of"] == self.nickname and card.id not in self.hand:
                self.hand.append(card.id)
        elif message.get("deck_id"):
            card.on_table = False
            if message.get("pile") == "draw":
                # a card that went back into a draw pile is unknown again
                card.name = None
        else:
            card.on_table = True

    def _on_draw(self, message: dict) -> None:
        self._read_deck(message.get("deck_json"))
        self._came_out(message)

    def _on_take(self, message: dict) -> None:
        self._read_hands(message.get("hands"))
        self._came_out(message)

    def _came_out(self, message: dict) -> None:
        description = message.get("component_json") or {}
        if description.get("kind") != "card":
            return
        card = self._card(description)
        card.on_table = True
        card.held_by = message.get("nickname")
        if card.id in self.hand:
            self.hand.remove(card.id)

    def _on_flip(self, message: dict) -> None:
        card = self.cards.get(message.get("component_id"))
        if card is not None:
            card.update(message)

    def _on_shuffle(self, message: dict) -> None:
        self._read_deck(message.get("deck_json"))

    def _on_refill(self, message: dict) -> None:
        self._read_deck(message.get("deck_json"))
        # the cards of the discard pile went back into the draw pile: what
        # this seat knew of them is forgotten
        for card in self.cards.values():
            if not card.on_table and card.id not in self.hand:
                card.name = None

    # ------------------------------------------------------------------
    # what this seat reads
    # ------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """Whether the table lets a player touch a card right now."""
        return self.joined and self.started and not self.missing_players

    def hand_names(self) -> list:
        return [self.cards[card_id].name for card_id in self.hand]

    def pile(self, deck_id: str, pile: str = "draw") -> dict:
        return ((self.decks.get(deck_id) or {}).get("piles") or {}).get(pile) or {}

    def pile_count(self, deck_id: str, pile: str = "draw") -> int:
        return self.pile(deck_id, pile).get("count", 0)

    def pile_top_name(self, deck_id: str, pile: str = "discard") -> str | None:
        return card_name((self.pile(deck_id, pile).get("top") or {}).get("src"))

    def loose_cards(self) -> list:
        """The cards lying on the table that nobody holds."""
        return [card for card in self.cards.values() if card.on_table and card.held_by is None]

    # ------------------------------------------------------------------
    # what this seat asks
    # ------------------------------------------------------------------

    async def _request(self, message: dict, predicate) -> dict:
        future = asyncio.get_running_loop().create_future()
        # registered before sending: the answer may come back at once
        self._waiters.append((predicate, future))
        await self._send(message)
        try:
            return await asyncio.wait_for(future, ACTION_TIMEOUT)
        except asyncio.TimeoutError:
            raise BotError("timeout", f"no answer to {message.get('action')}") from None

    def _mine(self, event: str, component_id: str):
        return lambda message: (
            message.get("event") == event
            and message.get("component_id") == component_id
            and message.get("nickname") == self.nickname
        )

    async def join(self, session_code: str, key: str = "", access_key: str = "") -> None:
        await self._request(
            {"action": "join_session", "session_code": session_code, "nickname": self.nickname,
             "role": "player", "key": key, "access_key": access_key},
            lambda message: message.get("event") == "session_joined",
        )

    async def create(self, game_name: str, key: str = "", access_key: str | None = None) -> str:
        """
        Creates a session of a game and takes its first seat.
        :return: the code the other players join it with
        """
        answer = await self._request(
            {"action": "create_session", "game_name": game_name, "nickname": self.nickname,
             "key": key, "access_key": access_key, "allows_watchers": True},
            lambda message: message.get("event") == "session_joined",
        )
        return answer["session"]["code"]

    async def start(self) -> None:
        await self._request(
            {"action": "start_session"},
            lambda message: message.get("event") == "session_status" and message.get("started"),
        )

    async def acquire(self, card_id: str) -> None:
        answer = await self._request(
            {"action": "acquire", "component_id": card_id}, self._mine("acquire", card_id))
        if not answer.get("success"):
            raise BotError("acquire_refused", card_id)

    async def release(self, card_id: str, x, y, hand: bool = False) -> dict:
        message = {"action": "release", "component_id": card_id, "x": x, "y": y}
        if hand:
            message["hand"] = True
        answer = await self._request(message, self._mine("release", card_id))
        if not answer.get("success"):
            raise BotError("release_refused", card_id)
        return answer

    async def draw(self, deck_id: str, pile: str = "draw") -> Card:
        """Takes the top card of a pile: it comes out held by this seat."""
        request_id = f"{self.nickname}-{next(self._request_ids)}"
        position = self.pile(deck_id, pile)
        answer = await self._request(
            {"action": "draw", "component_id": deck_id, "pile": pile, "request_id": request_id,
             "x": position.get("x", 0), "y": position.get("y", 0)},
            lambda message: message.get("event") == "draw" and message.get("request_id") == request_id,
        )
        return self.cards[answer["component_json"]["id"]]

    async def take(self, card_id: str, x, y) -> Card:
        """Plays a card of the private hand: it comes out held by this seat."""
        request_id = f"{self.nickname}-{next(self._request_ids)}"
        await self._request(
            {"action": "take", "component_id": card_id, "x": x, "y": y, "request_id": request_id},
            lambda message: message.get("event") == "take" and message.get("request_id") == request_id,
        )
        return self.cards[card_id]

    async def shuffle(self, deck_id: str, pile: str = "draw") -> None:
        await self._request(
            {"action": "shuffle", "component_id": deck_id, "pile": pile}, self._mine("shuffle", deck_id))

    async def refill(self, deck_id: str) -> None:
        await self._request(
            {"action": "refill", "component_id": deck_id}, self._mine("refill", deck_id))

    # ------------------------------------------------------------------
    # gestures made of several actions
    # ------------------------------------------------------------------

    def _pile_corner(self, deck_id: str, pile: str, card: Card) -> tuple:
        """Where to release a card so that its center lies on a pile."""
        deck = self.decks[deck_id]
        position = self.pile(deck_id, pile)
        return (
            position["x"] + (deck["width"] - card.width) / 2,
            position["y"] + (deck["height"] - card.height) / 2,
        )

    async def keep(self, card: Card) -> Card:
        """Puts a card this seat holds into its private hand."""
        await self.release(card.id, card.x or 0, card.y or 0, hand=True)
        return card

    async def draw_to_hand(self, deck_id: str, pile: str = "draw") -> Card:
        return await self.keep(await self.draw(deck_id, pile))

    async def draw_to_pile(self, deck_id: str, pile: str, to_deck: str, to_pile: str = "draw") -> None:
        """Moves the top card of a pile onto another pile, unseen."""
        card = await self.draw(deck_id, pile)
        await self.release(card.id, *self._pile_corner(to_deck, to_pile, card))

    async def table_to_pile(self, card: Card, deck_id: str, pile: str = "draw") -> None:
        await self.acquire(card.id)
        await self.release(card.id, *self._pile_corner(deck_id, pile, card))

    async def table_to_hand(self, card: Card) -> None:
        await self.acquire(card.id)
        await self.keep(card)

    async def hand_to_pile(self, card_id: str, deck_id: str, pile: str = "discard") -> None:
        card = self.cards[card_id]
        x, y = self._pile_corner(deck_id, pile, card)
        await self.take(card_id, x + card.width / 2, y + card.height / 2)
        await self.release(card_id, x, y)

    async def hand_to_table(self, card_id: str, x, y) -> None:
        card = self.cards[card_id]
        await self.take(card_id, x + card.width / 2, y + card.height / 2)
        await self.release(card_id, x, y)

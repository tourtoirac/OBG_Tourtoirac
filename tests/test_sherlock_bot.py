"""
Tests of the simulated players of Sherlock Holmes (bots/). The bots are
clients: they are run here against the real session and the real message
handlers, through a table that hands their messages over in memory instead of
a WebSocket.
"""
import asyncio
import json

import pytest

from bots.client import BotError, TableClient
from bots.sherlock import DEAL, PACK, VILLAIN_DECK, VILLAINS, Game, Operator, Referee, legal
from handle_message import (
    acquire, deal, draw, flip, refill, release, show_add, show_close, show_open, shuffle, take)
from session import Session
from tests.test_bag import LOGGER, FakeProtocol, FakeUser

# the 103 cards of the pack, as the quick reference guide of the game counts them
PACK_CARDS = {
    "holmes": 1, "watson": 1, "mycroft": 1,
    "train": 12, "hansom": 12, "thick_fog": 3,
    "country": 12, "london": 6, "scotland_yard": 3, "baker_street": 3,
    "i_suspect": 12, "clue": 12, "telegram": 4, "disguise": 4,
    "arrest": 6, "inspector": 5, "alibi": 6,
}


def card(name, copies=1, **fields):
    described = {"kind": "card", "id": name, "front_src": f"/cards/{name}.png", **fields}
    if copies > 1:
        described["copies"] = copies
    return described


def sherlock_game_json(stack: bool = True) -> dict:
    """
    The table of the game, laid out as its game_json in Chabanas.
    :param stack: False gives the game as it was first written, where "Donne"
        was a plain deck and the players only had a private hand
    """
    size = {"width": 240, "height": 383, "back_src": "/cards/back.png"}
    return {
        "game": {"max_players": 5, "max_watchers": 5},
        "options": {"anonymous_id": True,
                    "private_hand": {"show_hands_to_other_players": True} if stack else {}},
        "fixed": [
            {"kind": "deck", "id": VILLAIN_DECK, "type": "single", "x": 300, "y": 300, **size,
             "shuffle_at_startup": True, "components": [card(name) for name in sorted(VILLAINS)]},
            {"kind": "deck", "id": PACK, "type": "duo", "x": 900, "y": 300, **size,
             "discard_x": 1200, "discard_y": 300, "shuffle_at_startup": True,
             "components": [card(name, copies) for name, copies in PACK_CARDS.items()]},
            {"kind": "stack" if stack else "deck", "id": DEAL, "x": 1860, "y": 300, **size, "components": []},
        ],
        "movable": [card("game_afoot", x=300, y=900, side="back", **size)],
    }

HANDLERS = {"acquire": acquire, "release": release, "take": take, "draw": draw,
            "shuffle": shuffle, "refill": refill, "flip": flip, "deal": deal,
            "show_open": show_open, "show_add": show_add, "show_close": show_close}


class Seat(FakeUser):
    """A player whose messages go straight to its client."""

    client = None

    def send(self, message):
        # through JSON, as on the wire
        self.client.handle(json.loads(json.dumps(message)))


class SeatProtocol(FakeProtocol):
    def send_error(self, code, message):
        self.user.send({"event": "error", "error": {"code": code, "message": message}})


class Table:
    """A session of the game and the seats around it."""

    def __init__(self, nicknames, stack=True):
        game_json = sherlock_game_json(stack)
        self.session = Session(
            user=None, name="SherlockHolmes", key="KEY1", code="CODE1", active=True,
            variant="std", game_json=game_json, started=True, player_names=list(nicknames),
        )
        self.clients = {}
        for nickname in nicknames:
            seat = Seat(nickname, session=self.session)
            protocol = SeatProtocol(seat)
            seat.client = TableClient(nickname, self._sender(protocol))
            self.session.players.append(seat)
            self.clients[nickname] = seat.client
        for seat in self.session.players:
            seat.send({"event": "session_joined", "session": self.session.return_session_json(seat)})

    @staticmethod
    def _sender(protocol):
        async def send(message):
            HANDLERS[message["action"]](protocol, LOGGER, message)
        return send

    def card_count(self) -> int:
        session = self.session
        in_decks = sum(
            len(cards) for deck in session.components_lists["fixed"] if deck.kind in ("deck", "stack")
            for cards in deck.piles.values())
        in_hands = sum(len(held) for held in session.hands.values())
        return in_decks + in_hands + len(session.components_lists["movable"])


class Recorder(Operator):
    def __init__(self, guilty=False):
        self.lines = []
        self.guilty = guilty

    def say(self, text):
        self.lines.append(text)

    async def ask_guilty(self, accuser, target):
        return self.guilty


def bots_game(table, bots, **options):
    return Game([table.clients[name] for name in bots], operator=Recorder(), delay=0, grace=0, **options)


class TestRules:
    def test_only_the_game_is_afoot_opens_the_round(self):
        assert legal("game_afoot", None, "london", ["game_afoot"])
        assert not legal("train", None, "london", ["train"])

    def test_a_move_follows_the_opening_and_a_location_follows_a_move(self):
        assert legal("train", "game_afoot", "london", [])
        assert not legal("clue", "game_afoot", "london", [])
        # a hansom stays in the same kind of location, a train changes it
        assert legal("scotland_yard", "hansom", "london", [])
        assert not legal("country", "hansom", "london", [])
        assert legal("country", "train", "london", [])
        assert not legal("london", "train", "london", [])
        assert legal("thick_fog", "train", "london", [])
        assert legal("country", "thick_fog", "london", [])

    def test_the_cards_printed_at_the_bottom_of_a_card_may_follow_it(self):
        assert legal("clue", "country", "country", [])
        assert not legal("clue", "baker_street", "london", [])
        assert legal("i_suspect", "baker_street", "london", [])
        assert legal("inspector", "clue", "london", [])
        assert legal("arrest", "inspector", "london", [])

    def test_a_detective_follows_anything_but_a_move(self):
        assert legal("holmes", "clue", "london", [])
        assert not legal("watson", "hansom", "london", [])

    def test_a_villain_only_leaves_a_hand_of_villains(self):
        assert legal("moran", "clue", "london", ["moran", "clay"])
        assert not legal("moran", "clue", "london", ["moran", "train"])

    def test_an_alibi_is_never_played_in_turn(self):
        assert not legal("alibi", "arrest", "london", ["alibi"])


class TestSetup:
    def test_the_bots_deal_the_round_as_the_rules_say(self):
        table = Table(["holmes", "watson", "lestrade"])
        game = bots_game(table, ["holmes", "watson", "lestrade"])
        assert game.table_is_fresh()
        asyncio.run(game.setup())
        client = table.clients["holmes"]
        assert [len(c.hand) for c in table.clients.values()] == [6, 6, 6]
        assert client.pile_count(DEAL) == 0
        assert client.pile_count(VILLAIN_DECK) == 0
        # 103 cards, less the 16 dealt with the opening card and one villain,
        # plus the three other villains
        assert client.pile_count(PACK) == 103 - 16 + 3
        dealt = [name for c in table.clients.values() for name in c.hand_names()]
        assert dealt.count("game_afoot") == 1
        assert len([name for name in dealt if name in VILLAINS]) == 1
        assert table.card_count() == 108

    def test_a_seat_only_knows_the_cards_of_its_own_hand(self):
        table = Table(["holmes", "watson"])
        asyncio.run(bots_game(table, ["holmes", "watson"]).setup())
        holmes, watson = table.clients["holmes"], table.clients["watson"]
        assert all(name is not None for name in holmes.hand_names())
        assert all(holmes.cards[card_id].name is None for card_id in watson.hand if card_id in holmes.cards)


class TestRound:
    @pytest.mark.parametrize("seed", range(8))
    def test_bots_play_a_whole_round_without_a_refusal(self, seed):
        import random
        random.seed(seed)
        table = Table(["holmes", "watson", "lestrade"])
        game = bots_game(table, ["holmes", "watson", "lestrade"])

        async def play():
            await game.setup()
            await asyncio.wait_for(game.run(), 60)

        asyncio.run(play())
        lines = game.operator.lines
        assert game.referee.round_over is not None
        assert not [line for line in lines if line.startswith("Action refusée")], lines
        assert table.card_count() == 108
        assert any("joue game_afoot" in line for line in lines)

    def test_every_card_a_bot_plays_follows_the_rules(self):
        import random
        random.seed(3)
        table = Table(["holmes", "watson", "lestrade"])
        game = bots_game(table, ["holmes", "watson", "lestrade"])
        referee = game.referee
        illegal = []
        played = referee.played

        def checked(player, name):
            hand = table.clients[player].hand_names() + [name]
            if not legal(name, referee.last, referee.location, hand):
                illegal.append((player, name, referee.last, referee.location))
            played(player, name)

        referee.played = checked

        async def play():
            await game.setup()
            await asyncio.wait_for(game.run(), 60)

        asyncio.run(play())
        assert illegal == []


class TestWithAHuman:
    def test_the_bots_wait_for_the_human_and_follow_its_turn(self):
        # the game as it was first written: "Donne" is a plain deck
        table = Table(["holmes", "alice"], stack=False)
        game = bots_game(table, ["holmes"])
        alice = table.clients["alice"]

        async def play():
            setup = asyncio.ensure_future(game.setup())
            # the human draws its six cards while the setup waits for them
            await asyncio.sleep(0.05)
            for _ in range(6):
                await alice.draw_to_hand(DEAL)
            await asyncio.wait_for(setup, 5)
            # whoever holds the opening card plays it
            if "game_afoot" in alice.hand_names():
                await alice.hand_to_pile(alice.hand[alice.hand_names().index("game_afoot")], PACK)
            else:
                await game.step()
            return game.referee.current

        current = asyncio.run(play())
        assert len(alice.hand) + len(table.clients["holmes"].hand) == 11
        assert current in ("holmes", "alice")
        assert game.referee.last == "game_afoot"

    def test_a_human_who_keeps_the_card_drawn_passes(self):
        referee = Referee(Operator(), bots=["holmes"])
        table = Table(["holmes", "alice"])
        alice = table.clients["alice"]
        table.clients["holmes"].listeners.append(
            lambda message: referee.observe(table.clients["holmes"], message))
        referee.players = ["holmes", "alice"]
        referee.last, referee.current = "game_afoot", "alice"
        asyncio.run(alice.draw_to_hand(PACK))
        assert referee.current == "holmes"
        assert referee.passed_by_keep == "alice"

    def test_a_draw_that_was_owed_does_not_end_the_turn(self):
        referee = Referee(Operator(), bots=["holmes"])
        table = Table(["holmes", "alice"])
        alice = table.clients["alice"]
        table.clients["holmes"].listeners.append(
            lambda message: referee.observe(table.clients["holmes"], message))
        referee.players = ["holmes", "alice"]
        referee.last, referee.current = "scotland_yard", "alice"
        referee.owe("alice", 2)
        asyncio.run(alice.draw_to_hand(PACK))
        asyncio.run(alice.draw_to_hand(PACK))
        assert referee.current == "alice"
        assert referee.owed["alice"] == 0


class TestClient:
    def test_a_refusal_of_the_server_is_raised(self):
        table = Table(["holmes", "watson"])
        with pytest.raises(BotError) as refusal:
            asyncio.run(table.clients["holmes"].draw(PACK, "discard"))
        assert refusal.value.code == "pile_empty"


class TestNewFeatures:
    """What the server learnt after the bots were first written."""

    def test_a_stack_deals_to_the_humans_too(self):
        table = Table(["holmes", "alice"])
        game = bots_game(table, ["holmes"])
        asyncio.run(asyncio.wait_for(game.setup(), 5))
        assert len(table.clients["alice"].hand) == 6
        assert len(table.clients["holmes"].hand) == 6
        assert table.clients["holmes"].pile_count(DEAL) == 0
        assert not [line for line in game.operator.lines if "piochez" in line]

    def test_several_cards_are_drawn_at_once(self):
        table = Table(["holmes", "watson"])
        holmes = table.clients["holmes"]
        cards = asyncio.run(holmes.draw_many(PACK, 5))
        assert len(cards) == 5
        assert all(card.on_table and card.held_by == "holmes" for card in cards)
        assert holmes.pile_count(PACK) == 98

    def test_cards_move_from_a_pile_to_another_by_tens(self):
        table = Table(["holmes", "watson"])
        holmes = table.clients["holmes"]
        assert asyncio.run(holmes.draw_to_pile(PACK, "draw", DEAL, count=23)) == 23
        assert holmes.pile_count(DEAL) == 23
        # only the opening card is left lying on the table
        assert len(holmes.loose_cards()) == 1

    def test_a_bot_lays_its_hand_face_down(self):
        table = Table(["holmes", "watson"])
        game = bots_game(table, ["holmes", "watson"])

        async def play():
            await game.setup()
            return await game.dump_hand(game.bots["holmes"])

        assert asyncio.run(play()) == 6
        laid = [card for card in table.session.components_lists["movable"] if card.id != "game_afoot"]
        assert len(laid) == 6
        assert all(card.side == "back" for card in laid)
        # the other seat learnt nothing of them
        watson = table.clients["watson"]
        assert all(watson.cards[card.id].name is None for card in laid)

    def test_a_bot_reads_the_hand_a_human_shows_it(self):
        table = Table(["holmes", "alice"])
        game = bots_game(table, ["holmes"], show_timeout=2)
        alice = table.clients["alice"]

        async def play():
            await game.setup()
            verdict = asyncio.ensure_future(game.is_guilty("holmes", "alice"))
            await asyncio.sleep(0.05)
            await alice.show_hand("holmes")
            return await asyncio.wait_for(verdict, 5)

        guilty = asyncio.run(play())
        assert guilty == any(name in VILLAINS for name in alice.hand_names())
        assert any("montre ta main à holmes" in line for line in game.operator.lines)

    def test_a_hand_not_shown_in_time_is_asked_to_the_operator(self):
        table = Table(["holmes", "alice"])
        game = bots_game(table, ["holmes"], show_timeout=0.05)
        game.operator.guilty = True

        async def play():
            await game.setup()
            return await game.is_guilty("holmes", "alice")

        assert asyncio.run(play()) is True

    def test_a_bot_shows_its_hand_to_a_human_and_hides_it(self):
        table = Table(["holmes", "alice", "bob"])
        game = bots_game(table, ["holmes"])
        holmes, alice, bob = (table.clients[name] for name in ("holmes", "alice", "bob"))

        async def play():
            await game.setup()
            await game.show_hand(game.bots["holmes"], "alice")
            seen = (alice.shown_names("holmes"), bob.shown_names("holmes"))
            await holmes.hide_hand()
            return seen

        seen_by_alice, seen_by_bob = asyncio.run(play())
        assert sorted(seen_by_alice) == sorted(holmes.hand_names())
        assert seen_by_bob == []
        assert alice.shown_names("holmes") == []

    def test_the_first_game_json_still_plays(self):
        table = Table(["holmes", "watson"], stack=False)
        game = bots_game(table, ["holmes", "watson"])
        asyncio.run(game.setup())
        assert [len(c.hand) for c in table.clients.values()] == [6, 6]

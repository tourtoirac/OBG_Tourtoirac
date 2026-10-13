"""
Simulated players for Sherlock Holmes, the card game.

    python3 sherlock_bot.py --create --bots 3          # a table of bots, to watch
    python3 sherlock_bot.py --code ABCD1234 --bots 2   # bots joining humans

The bots take a seat each, deal the round when the table is untouched, then
play in their turn. With humans at the table, read what the program prints:
it is the only voice the bots have. Type "aide" for the commands.

When "Donne" is a stack, the bots deal with its Deal action: every player,
humans included, gets its six cards in its private hand. When the game has
the show_hands_to_other_players option, a hand that must be looked at is
shown on screen instead of being told at the console.
"""
import argparse
import asyncio
import json
import sys
import threading

import websockets

from bots.client import BotError, TableClient
from bots.sherlock import Game, Operator

GAME_NAME = "SherlockHolmes"
DEFAULT_URL = "ws://localhost:12201"
DEFAULT_NAMES = ["Lestrade", "Gregson", "Hopkins", "Mrs Hudson"]

HELP = """Commandes :
  etat               où en est la partie
  start              démarre la partie (si un bot l'a créée)
  setup              refait la mise en place (table intacte seulement)
  tour <pseudo>      dit à qui c'est de jouer
  pioche <pseudo> n  fait piocher n cartes (I Suspect, Inspector joués par un humain)
  main <bot>         écrit ici la main d'un bot
  montre <bot> <pseudo|tous>
                     le bot montre sa main à l'écran, à un joueur ou à tous
                     (Clue, Disguise, Arrest, Holmes, Watson joués par un humain)
  cache <bot>        le bot referme la zone où il montrait sa main
  donne <bot>        le bot pose sa main face cachée sur la table et repioche autant
                     (Arrest d'un humain contre un bot innocent)
  quit               quitte"""


class Console(Operator):
    """The operator at the terminal: what the bots say, what the humans answer."""

    def __init__(self):
        self.lines = asyncio.Queue()
        self._question = None
        loop = asyncio.get_running_loop()
        # stdin is read by a thread: a terminal cannot be awaited everywhere
        threading.Thread(target=self._read, args=(loop,), daemon=True).start()

    def _read(self, loop) -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(self._line, line.strip())
        loop.call_soon_threadsafe(self._line, "quit")

    def _line(self, line: str) -> None:
        # a pending question takes the line; otherwise it is a command
        if self._question is not None and not self._question.done():
            self._question.set_result(line)
        else:
            self.lines.put_nowait(line)

    def say(self, text: str) -> None:
        print(text, flush=True)

    async def _ask(self, text: str) -> str:
        print(text, flush=True)
        self._question = asyncio.get_running_loop().create_future()
        return await self._question

    async def ask_guilty(self, accuser: str, target: str) -> bool:
        while True:
            answer = (await self._ask(
                f">>> {accuser} regarde la main de {target} : y a-t-il un Villain ? (o/n)")).lower()
            if answer in ("o", "oui", "n", "non"):
                return answer.startswith("o")

    async def confirm(self, text: str) -> None:
        await self._ask(f">>> {text} (Entrée quand c'est fait)")


async def connect(url: str, nickname: str) -> TableClient:
    """Opens the connection of one seat and keeps reading it."""
    socket = await websockets.connect(url)

    async def send(message: dict) -> None:
        await socket.send(json.dumps(message))

    client = TableClient(nickname, send)

    async def read() -> None:
        async for raw in socket:
            client.handle(json.loads(raw))

    client.reader = asyncio.ensure_future(read())
    return client


def status(game: Game) -> str:
    referee = game.referee
    watcher = game.watcher
    lines = [
        f"Joueurs : {', '.join(watcher.players)}",
        f"Partie démarrée : {'oui' if watcher.started else 'non'}"
        + (f" ; absents : {', '.join(watcher.missing_players)}" if watcher.missing_players else ""),
        f"Tour : {referee.current or '(personne)'} ; dernière carte : {referee.last or '-'}"
        f" ; lieu : {referee.location}",
        "Cartes en main : " + ", ".join(
            f"{nickname} {watcher.hand_counts.get(nickname, 0)}" for nickname in watcher.players),
        f"Pioche : {watcher.pile_count('pack')} ; défausse : {watcher.pile_count('pack', 'discard')}",
    ]
    owed = {nickname: count for nickname, count in referee.owed.items() if count > 0}
    if owed:
        lines.append("À piocher : " + ", ".join(f"{n} {c}" for n, c in owed.items()))
    if referee.round_over:
        lines.append(f"Manche terminée : {referee.round_over}")
    return "\n".join(lines)


async def command(game: Game, console: Console, line: str) -> bool:
    """
    Runs a command of the operator.
    :return: False to leave the program
    """
    words = line.split()
    if not words:
        return True
    name, arguments = words[0].lower(), words[1:]
    target = " ".join(arguments)
    if name in ("quit", "exit", "q"):
        return False
    if name in ("aide", "help", "?"):
        console.say(HELP)
    elif name == "etat":
        console.say(status(game))
    elif name == "start":
        await game.watcher.start()
    elif name == "setup":
        if game.table_is_fresh():
            await game.setup()
        else:
            console.say("La table n'est plus intacte : la mise en place se fait à la main")
    elif name == "tour" and target in game.watcher.players:
        game.referee.set_turn(target)
        console.say(f"C'est à {target} de jouer")
    elif name == "pioche" and len(arguments) >= 2 and arguments[-1].isdigit():
        game.referee.owe(" ".join(arguments[:-1]), int(arguments[-1]))
    elif name == "main" and target in game.bots:
        console.say(f"{target} : {', '.join(sorted(n or '?' for n in game.bots[target].client.hand_names()))}")
    elif name == "montre" and len(arguments) >= 2:
        # the nicknames may hold spaces: the bot is the longest one that fits
        shown = False
        for cut in range(len(arguments) - 1, 0, -1):
            bot_name, to = " ".join(arguments[:cut]), " ".join(arguments[cut:])
            to = "all" if to.lower() in ("tous", "all") else to
            if bot_name in game.bots and (to == "all" or to in game.watcher.players) and to != bot_name:
                if not game.watcher.can_show_hands():
                    console.say("Ce jeu n'a pas l'option show_hands_to_other_players : utilisez « main <bot> »")
                else:
                    await game.show_hand(game.bots[bot_name], to)
                shown = True
                break
        if not shown:
            console.say("Usage : montre <bot> <pseudo|tous>")
    elif name == "cache" and target in game.bots:
        await game.bots[target].client.hide_hand()
    elif name == "donne" and target in game.bots:
        count = await game.dump_hand(game.bots[target])
        game.referee.owe(target, count)
    else:
        console.say("Commande inconnue ou pseudo introuvable. Tapez « aide ».")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket of Tourtoirac (default {DEFAULT_URL})")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--code", help="code of the session to join")
    where.add_argument("--create", action="store_true", help="create a new session")
    parser.add_argument("--bots", type=int, default=2, help="number of simulated players (default 2)")
    parser.add_argument("--names", help="nicknames of the bots, separated by commas")
    parser.add_argument("--access-key", default="", help="access key of the session, if it has one")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds a bot waits before acting")
    parser.add_argument("--grace", type=float, default=4.0,
                        help="seconds more left to a human who drew a card to play it")
    arguments = parser.parse_args()

    names = [name.strip() for name in (arguments.names or "").split(",") if name.strip()]
    names = (names + [name for name in DEFAULT_NAMES if name not in names])[:arguments.bots]
    if len(names) < arguments.bots:
        parser.error("not enough nicknames: give them with --names")

    console = Console()
    clients = []
    code = arguments.code
    for nickname in names:
        client = await connect(arguments.url, nickname)
        # the key is what lets a nickname take its seat back after a restart
        key = f"bot-{nickname}"
        if code is None:
            code = await client.create(GAME_NAME, key, arguments.access_key or None)
            console.say(f"Session créée : code {code}")
        else:
            await client.join(code, key, arguments.access_key)
        console.say(f"{nickname} est à la table")
        clients.append(client)

    game = Game(clients, console, delay=arguments.delay, grace=arguments.grace)
    if game.watcher.private_hand() is None:
        console.say("Attention : ce jeu n'a pas l'option private_hand, les bots ne peuvent pas tenir de cartes")
    elif not game.watcher.can_show_hands():
        console.say("Ce jeu n'a pas l'option show_hands_to_other_players : "
                    "les mains regardées passent par les questions de cette console")

    async def play() -> None:
        if not game.watcher.ready:
            console.say("En attente du démarrage de la partie et de tous les joueurs (« start » pour démarrer)")
        await game.wait_until(lambda: game.watcher.ready, timeout=float("inf"))
        if game.table_is_fresh():
            await game.setup()
        else:
            console.say("Partie en cours : dites à qui c'est de jouer avec « tour <pseudo> »")
        await game.run()
        console.say("Les bots ne jouent plus. « quit » pour quitter.")

    playing = asyncio.ensure_future(play())
    try:
        while True:
            line = await console.lines.get()
            try:
                if not await command(game, console, line):
                    break
            except BotError as error:
                console.say(f"Action refusée ({error})")
    finally:
        game.stopped = True
        playing.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

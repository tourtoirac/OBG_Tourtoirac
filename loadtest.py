"""
Load test of a Tourtoirac server: how many tables it holds at once.

    python3 loadtest.py                                   # local server
    python3 loadtest.py --url wss://www.chabanas.ovh/obg/ws --stats ssh

Simulated tables are added step by step (--steps). At each table, bots take
the seats like browsers do, then play at a human pace: they take a token or a
card, drag it around (one "move" message per frame, as the page sends while
the mouse moves), put it down, draw cards, roll the dice, turn counters
over, chat. Every action waits for the event that answers it: its latency is
what a player feels. A step is held when the 95th percentile of the latencies,
the errors and the disconnections stay under the limits; the test stops at
the first step that is not held.

Everything the test creates is recognisable: the nicknames start with
--prefix ("loadtest_" by default), and the tables are closed at the end
(close_session: the sessions are archived, not deleted, in Chabanas).

The server's CPU and memory are read along the way, read-only: `docker stats`
on this machine (--stats local) or on the server through ssh (--stats ssh).
"""
import argparse
import asyncio
import itertools
import json
import math
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field

import websockets

DEFAULT_URL = "ws://localhost:12201"
SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/chabanas_deploy"), "-o", "BatchMode=yes", "pchignaguet@www.chabanas.ovh"]
# the games played, with the nationality of each seat (empty: none)
GAMES = {
    "Waterloo": ["french", "angloallies"],
    "Sherlock_Holmes": [],
}
# seconds an action waits for the event answering it
ANSWER_TIMEOUT = 10.0
# errors of the server that are part of normal play, not failures
BENIGN_ERRORS = {"dice_cooling_down", "component_held", "component_not_held", "acquire_refused", "pile_empty"}


# ----------------------------------------------------------------------
# Measures
# ----------------------------------------------------------------------

@dataclass
class Measures:
    latencies: dict[str, list[float]] = field(default_factory=dict)
    timeouts: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    refused: int = 0
    disconnections: int = 0
    received: int = 0
    sent: int = 0

    def latency(self, action: str, seconds: float) -> None:
        self.latencies.setdefault(action, []).append(seconds)

    def error(self, code: str) -> None:
        self.errors[code] = self.errors.get(code, 0) + 1

    def all_latencies(self) -> list[float]:
        return [s for values in self.latencies.values() for s in values]

    def actions(self) -> int:
        return len(self.all_latencies()) + self.timeouts

    def failures(self) -> int:
        return self.timeouts + sum(self.errors.values())


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, math.ceil(p / 100 * len(values)) - 1)]


# ----------------------------------------------------------------------
# A seat
# ----------------------------------------------------------------------

class Failure(Exception):
    pass


class Bot:
    """One seat, played by a program: it keeps what its seat hears."""

    def __init__(self, url: str, nickname: str, test: "LoadTest"):
        self.url = url
        self.nickname = nickname
        self.test = test
        self.socket = None
        self.reader = None
        self.session: dict = {}
        self.started = False
        self.missing: list = []
        # component id -> what the bot knows of it (position, kind, holder)
        self.movable: dict[str, dict] = {}
        self.decks: dict[str, dict] = {}
        self.dice: list[str] = []
        self.hand: list[str] = []
        self.waiters: list = []
        self.closed = False
        self.ids = itertools.count(1)
        # the nationality of the seat: a token of another one is not touched
        self.nationality: str | None = None

    # -- connection ------------------------------------------------------

    async def connect(self) -> None:
        self.socket = await websockets.connect(self.url, max_size=None, open_timeout=30, ping_interval=20)
        self.reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.socket:
                self.test.measures.received += 1
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                self._handle(message)
        except websockets.ConnectionClosed:
            pass
        finally:
            if not self.closed:
                self.test.measures.disconnections += 1
                self.test.log(f"{self.nickname}: connexion perdue")
            for _, future in self.waiters:
                if not future.done():
                    future.set_exception(Failure("disconnected"))

    async def send(self, message: dict) -> None:
        self.test.measures.sent += 1
        await self.socket.send(json.dumps(message))

    async def close(self) -> None:
        self.closed = True
        if self.socket is not None:
            await self.socket.close()

    # -- what the server says --------------------------------------------

    def _handle(self, message: dict) -> None:
        event = message.get("event")
        if event == "session_joined":
            self._read_session(message.get("session") or {})
        elif event == "session_status":
            self.started = bool(message.get("started", self.started))
            self.missing = message.get("missing_players") or []
        elif event in ("acquire", "release", "move", "draw", "take"):
            self._track(event, message)
        for waiter in list(self.waiters):
            predicate, future = waiter
            if future.done():
                self.waiters.remove(waiter)
            elif event == "error" and predicate(message):
                self.waiters.remove(waiter)
                future.set_exception(Failure((message.get("error") or {}).get("code", "error")))
            elif event != "error" and predicate(message):
                self.waiters.remove(waiter)
                future.set_result(message)
        if event == "error":
            code = (message.get("error") or {}).get("code", "error")
            if code in BENIGN_ERRORS:
                self.test.measures.refused += 1

    def _read_session(self, session: dict) -> None:
        self.session = session
        self.started = bool(session.get("started"))
        self.missing = session.get("missing_players") or []
        components = session.get("components") or {}
        for item in components.get("movable") or []:
            if item.get("kind") in ("token", "card"):
                self.movable[item["id"]] = {"kind": item["kind"], "x": item.get("x", 0), "y": item.get("y", 0),
                                            "held": None, "flippable": bool(item.get("back_src")),
                                            "nationality": item.get("nationality")}
        for item in components.get("fixed") or []:
            if item.get("kind") in ("deck", "stack"):
                self.decks[item["id"]] = item
        self.dice = [item["id"] for item in components.get("dice") or []]
        self.hand = [item["id"] for item in session.get("hand") or []]

    def _track(self, event: str, message: dict) -> None:
        component = message.get("component_json") or {}
        component_id = message.get("component_id") or component.get("id")
        if component_id is None:
            return
        known = self.movable.setdefault(component_id, {"kind": component.get("kind", "card"), "x": 0, "y": 0,
                                                       "held": None, "flippable": True})
        if event == "acquire" and message.get("success"):
            known["held"] = message.get("nickname")
        elif event in ("draw", "take"):
            known["held"] = message.get("nickname")
            known.update(x=component.get("x", known["x"]), y=component.get("y", known["y"]))
        elif event == "release":
            known["held"] = None
            # a card released onto a pile leaves the table
            if message.get("deck_id") or message.get("bag_id"):
                known["held"] = "pile"
            json_ = message.get("component_json") or {}
            known.update(x=json_.get("x", known["x"]), y=json_.get("y", known["y"]))
        elif event == "move":
            coordinates = message.get("coordinates") or {}
            known.update(x=coordinates.get("x", known["x"]), y=coordinates.get("y", known["y"]))

    # -- actions, each one timed -----------------------------------------

    async def request(self, action: str, message: dict, predicate, timed: bool = True) -> dict:
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((predicate, future))
        start = time.perf_counter()
        await self.send(message)
        try:
            answer = await asyncio.wait_for(future, ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            self.test.measures.timeouts += 1
            raise Failure("timeout") from None
        except Failure as failure:
            code = str(failure)
            if code in BENIGN_ERRORS:
                raise
            self.test.measures.error(code)
            raise
        if timed:
            self.test.measures.latency(action, time.perf_counter() - start)
        return answer

    def _mine(self, event: str, component_id: str):
        return lambda m: (m.get("event") == event and m.get("component_id") == component_id
                          and m.get("nickname") == self.nickname) or self._refusal(m)

    @staticmethod
    def _refusal(message: dict) -> bool:
        return message.get("event") == "error"

    async def create(self, game: str, nationality: str | None, key: str) -> str:
        message = {"action": "create_session", "game_name": game, "variant_name": "default",
                   "nickname": self.nickname, "key": key, "access_key": "", "allows_watchers": True}
        if nationality:
            message["nationality"] = nationality
        answer = await self.request("create", message, lambda m: m.get("event") == "session_joined"
                                    or self._refusal(m))
        return answer["session"]["code"]

    async def join(self, code: str, nationality: str | None, key: str) -> None:
        message = {"action": "join_session", "session_code": code, "nickname": self.nickname, "role": "player",
                   "key": key, "access_key": ""}
        if nationality:
            message["nationality"] = nationality
        await self.request("join", message, lambda m: m.get("event") == "session_joined" or self._refusal(m))

    async def start(self) -> None:
        if not self.started:
            await self.request("start", {"action": "start_session"},
                               lambda m: (m.get("event") == "session_status" and m.get("started"))
                               or self._refusal(m))

    async def close_table(self) -> None:
        await self.request("close", {"action": "close_session"},
                           lambda m: m.get("event") == "session_closed" or self._refusal(m), timed=False)

    # -- the gestures of a player ------------------------------------------

    def free_components(self) -> list[str]:
        return [cid for cid, c in self.movable.items()
                if c["held"] is None and cid not in self.hand and c.get("nationality") in (None, self.nationality)]

    async def drag(self) -> None:
        """Takes a token or a card lying on the table, drags it, puts it down."""
        free = self.free_components()
        if not free:
            return await self.draw()
        component_id = random.choice(free)
        known = self.movable[component_id]
        answer = await self.request("acquire", {"action": "acquire", "component_id": component_id},
                                    self._mine("acquire", component_id))
        if not answer.get("success"):
            self.test.measures.refused += 1
            return
        await self._drag_and_release(component_id, known["x"], known["y"])

    async def _drag_and_release(self, component_id: str, x: float, y: float) -> None:
        test = self.test
        angle = random.uniform(0, 2 * math.pi)
        distance = random.uniform(40, 300)
        frames = max(1, int(test.drag_seconds * test.drag_hz))
        # the echo of one move of the drag is timed: what the others see
        timed_frame = random.randrange(frames)
        for frame in range(1, frames + 1):
            fx = x + math.cos(angle) * distance * frame / frames
            fy = y + math.sin(angle) * distance * frame / frames
            message = {"action": "move", "component_id": component_id, "x": round(fx, 1), "y": round(fy, 1)}
            if frame - 1 == timed_frame:
                try:
                    await self.request("move", message,
                                       lambda m, c=component_id: (m.get("event") == "move"
                                                                  and m.get("component_id") == c)
                                       or self._refusal(m))
                except Failure:
                    pass
            else:
                await self.send(message)
            await asyncio.sleep(1 / test.drag_hz)
        end_x = round(x + math.cos(angle) * distance, 1)
        end_y = round(y + math.sin(angle) * distance, 1)
        await self.request("release", {"action": "release", "component_id": component_id, "x": end_x, "y": end_y},
                           self._mine("release", component_id))

    async def draw(self) -> None:
        """Draws the top card of a pile and lays it on the table."""
        decks = [d for d in self.decks.values()
                 if ((d.get("piles") or {}).get("draw") or {}).get("count", 1) > 0]
        if not decks:
            return await self.chat()
        deck = random.choice(decks)
        position = ((deck.get("piles") or {}).get("draw") or {})
        request_id = f"{self.nickname}-{next(self.ids)}"
        try:
            answer = await self.request(
                    "draw", {"action": "draw", "component_id": deck["id"], "pile": "draw", "request_id": request_id,
                         "x": position.get("x", 0), "y": position.get("y", 0)},
                lambda m: (m.get("event") == "draw" and m.get("request_id") == request_id) or self._refusal(m))
        except Failure as failure:
            if str(failure) == "pile_empty":
                # drawn empty by the others: not drawn from any more
                deck.setdefault("piles", {}).setdefault("draw", {})["count"] = 0
            raise
        card = answer.get("component_json") or {}
        if card.get("id"):
            await self._drag_and_release(card["id"], card.get("x", 0), card.get("y", 0) + 450)

    async def roll(self) -> None:
        if not self.dice:
            return await self.drag()
        dice = random.choice(self.dice)
        await self.request("roll", {"action": "roll", "component_id": dice},
                           lambda m: (m.get("event") == "roll" and m.get("component_id") == dice)
                           or self._refusal(m))

    async def flip(self) -> None:
        free = [cid for cid in self.free_components() if self.movable[cid]["flippable"]]
        if not free:
            return await self.drag()
        component_id = random.choice(free)
        await self.request("flip", {"action": "flip", "component_id": component_id},
                           lambda m: (m.get("event") == "flip" and m.get("component_id") == component_id)
                           or self._refusal(m))

    async def chat(self) -> None:
        text = f"{self.nickname} {next(self.ids)}"
        await self.request("chat", {"action": "chat_send", "text": text},
                           lambda m: (m.get("event") == "chat" and (m.get("message") or {}).get("text") == text)
                           or self._refusal(m))


GESTURES = (("drag", 55), ("draw", 15), ("roll", 10), ("flip", 10), ("chat", 10))


# ----------------------------------------------------------------------
# A table
# ----------------------------------------------------------------------

class Table:
    def __init__(self, test: "LoadTest", number: int, game: str):
        self.test = test
        self.number = number
        self.game = game
        self.bots: list[Bot] = []
        self.code = None
        self.tasks: list[asyncio.Task] = []

    async def open(self) -> None:
        test = self.test
        nationalities = GAMES[self.game]
        seats = len(nationalities) or test.players
        key = test.prefix.rstrip("_")
        for seat in range(seats):
            bot = Bot(test.url, f"{test.prefix}{self.number:03d}_{seat + 1}", test)
            await bot.connect()
            nationality = nationalities[seat] if nationalities else None
            bot.nationality = nationality
            if seat == 0:
                self.code = await bot.create(self.game, nationality, key)
            else:
                await bot.join(self.code, nationality, key)
            self.bots.append(bot)
        await self.bots[0].start()
        test.log(f"table {self.number} ({self.game}, {self.code}) : {seats} joueurs")
        self.tasks = [asyncio.ensure_future(self.play(bot)) for bot in self.bots]

    async def play(self, bot: Bot) -> None:
        names, weights = zip(*GESTURES)
        while not self.test.stopping:
            await asyncio.sleep(random.uniform(*self.test.think))
            if self.test.stopping or bot.missing:
                continue
            gesture = random.choices(names, weights)[0]
            try:
                await getattr(bot, gesture)()
            except Failure:
                pass
            except websockets.ConnectionClosed:
                return

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.test.keep:
            pass
        elif self.bots:
            try:
                await self.bots[0].close_table()
            except Failure as failure:
                self.test.log(f"table {self.number} ({self.code}) : fermeture refusée ({failure})")
        for bot in self.bots:
            await bot.close()


# ----------------------------------------------------------------------
# The test
# ----------------------------------------------------------------------

class LoadTest:
    def __init__(self, args):
        self.url = args.url
        self.steps = [int(s) for s in args.steps.split(",")]
        self.step_seconds = args.step_seconds
        self.players = args.players
        self.games = args.games.split(",")
        self.think = (args.think_min, args.think_max)
        self.drag_seconds = args.drag_seconds
        self.drag_hz = args.drag_hz
        self.prefix = args.prefix
        self.max_p95 = args.max_p95
        self.max_failures = args.max_failures
        self.stats = args.stats
        self.keep = args.keep
        self.report = args.report
        self.measures = Measures()
        self.tables: list[Table] = []
        self.stopping = False
        self.started = time.perf_counter()
        self.results: list[dict] = []

    def log(self, text: str) -> None:
        print(f"[{time.perf_counter() - self.started:7.1f} s] {text}", flush=True)

    async def server_stats(self) -> list[str]:
        """CPU and memory of the containers, and the load of the machine."""
        if self.stats == "none":
            return []
        command = "docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}'"
        if self.stats == "ssh":
            argv = SSH + [f"sudo {command}; cat /proc/loadavg"]
        else:
            argv = ["bash", "-c", f"{command}; cat /proc/loadavg"]
        process = await asyncio.create_subprocess_exec(*argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out, _ = await process.communicate()
        lines = out.decode().splitlines()
        return [line for line in lines if line.startswith("obg-") or line[:1].isdigit()]

    async def run(self) -> None:
        self.log(f"test de charge de {self.url} : paliers {self.steps}, {self.step_seconds} s chacun")
        number = itertools.count(1)
        try:
            for step in self.steps:
                while len(self.tables) < step:
                    table = Table(self, next(number), self.games[len(self.tables) % len(self.games)])
                    try:
                        await table.open()
                    except (Failure, OSError, websockets.WebSocketException) as error:
                        self.log(f"table {table.number} : ouverture impossible ({error})")
                        self.measures.error(f"open:{error}")
                        for bot in table.bots:
                            await bot.close()
                        break
                    self.tables.append(table)
                held = await self.hold(step)
                if not held:
                    break
        finally:
            self.stopping = True
            self.log("fermeture des tables")
            await asyncio.gather(*(table.close() for table in self.tables), return_exceptions=True)
        self.summary()

    async def hold(self, step: int) -> bool:
        """Plays a step for step_seconds and tells whether it is held."""
        self.measures = Measures()
        start = time.perf_counter()
        stats = []
        while time.perf_counter() - start < self.step_seconds:
            stats.append(await self.server_stats())
            await asyncio.sleep(max(0.0, min(10.0, self.step_seconds - (time.perf_counter() - start))))
        elapsed = time.perf_counter() - start
        m = self.measures
        latencies = m.all_latencies()
        actions = m.actions()
        failure_rate = m.failures() / actions if actions else 0.0
        p95 = percentile(latencies, 95)
        result = {
            "tables": len(self.tables), "bots": sum(len(t.bots) for t in self.tables),
            "actions": actions, "actions_per_s": actions / elapsed,
            "received_per_s": m.received / elapsed, "sent_per_s": m.sent / elapsed,
            "p50": percentile(latencies, 50), "p95": p95, "max": max(latencies, default=0.0),
            "by_action": {a: {"n": len(v), "p50": percentile(v, 50), "p95": percentile(v, 95)}
                          for a, v in sorted(m.latencies.items())},
            "timeouts": m.timeouts, "errors": dict(m.errors), "refused": m.refused,
            "disconnections": m.disconnections, "failure_rate": failure_rate,
            "server": stats[-1] if stats else [],
        }
        held = (len(self.tables) >= step and p95 <= self.max_p95 and failure_rate <= self.max_failures
                and m.disconnections == 0)
        result["held"] = held
        self.results.append(result)
        self.log(f"palier {step} tables ({result['bots']} bots) : {actions} actions "
                 f"({result['actions_per_s']:.1f}/s), latence médiane {result['p50'] * 1000:.0f} ms, "
                 f"95 % {p95 * 1000:.0f} ms, max {result['max'] * 1000:.0f} ms, échecs {failure_rate:.1%}, "
                 f"déconnexions {m.disconnections}, messages reçus {result['received_per_s']:.0f}/s "
                 f"→ {'tenu' if held else 'NON TENU'}")
        for line in result["server"]:
            self.log(f"   serveur : {line}")
        if m.errors:
            self.log(f"   erreurs : {m.errors}")
        return held

    def summary(self) -> None:
        held = [r for r in self.results if r["held"]]
        print()
        print("palier  bots  actions/s  médiane  95 %     max      échecs  déconnexions  reçus/s")
        for r in self.results:
            print(f"{r['tables']:6} {r['bots']:5} {r['actions_per_s']:9.1f} {r['p50'] * 1000:6.0f} ms"
                  f" {r['p95'] * 1000:6.0f} ms {r['max'] * 1000:6.0f} ms {r['failure_rate']:7.1%}"
                  f" {r['disconnections']:12} {r['received_per_s']:8.0f}  {'tenu' if r['held'] else 'NON TENU'}")
        if held:
            print(f"\nDernier palier tenu : {held[-1]['tables']} tables ({held[-1]['bots']} bots)")
        else:
            print("\nAucun palier tenu")
        if self.report:
            with open(self.report, "w", encoding="utf-8") as file:
                json.dump({"url": self.url, "steps": self.results}, file, indent=2, ensure_ascii=False)
            print(f"Rapport : {self.report}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"WebSocket of Tourtoirac (default {DEFAULT_URL})")
    parser.add_argument("--steps", default="1,2,5,10,20,40", help="numbers of tables, step by step")
    parser.add_argument("--step-seconds", type=float, default=120, help="how long each step is held")
    parser.add_argument("--games", default=",".join(GAMES), help="games played, in turn, by the tables")
    parser.add_argument("--players", type=int, default=3, help="seats of a game without nationalities")
    parser.add_argument("--think-min", type=float, default=2.0, help="least seconds between two gestures")
    parser.add_argument("--think-max", type=float, default=5.0, help="most seconds between two gestures")
    parser.add_argument("--drag-seconds", type=float, default=1.0, help="how long a drag lasts")
    parser.add_argument("--drag-hz", type=float, default=60, help="move messages per second while dragging")
    parser.add_argument("--max-p95", type=float, default=1.0, help="most seconds for 95 %% of the answers")
    parser.add_argument("--max-failures", type=float, default=0.02, help="most share of failed actions")
    parser.add_argument("--prefix", default="loadtest_", help="start of the nicknames of the bots")
    parser.add_argument("--stats", choices=("none", "local", "ssh"), default="local",
                        help="where to read the CPU and memory of the containers")
    parser.add_argument("--keep", action="store_true", help="leave the tables open at the end")
    parser.add_argument("--report", help="JSON file the results are written to")
    parser.add_argument("--seed", type=int, help="seed of the random gestures")
    args = parser.parse_args()
    for game in args.games.split(","):
        if game not in GAMES:
            parser.error(f"unknown game {game}: {', '.join(GAMES)}")
    random.seed(args.seed)
    try:
        asyncio.run(LoadTest(args).run())
    except KeyboardInterrupt:
        print("interrompu", file=sys.stderr)


if __name__ == "__main__":
    main()

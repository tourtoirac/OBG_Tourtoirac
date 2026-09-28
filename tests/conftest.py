import threading

import pytest
from twisted.internet import reactor, threads
from twisted.python.failure import Failure


@pytest.fixture(scope="session", autouse=True)
def twisted_reactor():
    """
    Runs the Twisted reactor once for the whole test session, in a background
    thread. The reactor cannot be restarted, so it must not be started/stopped
    per test.
    """
    if not reactor.running:
        started = threading.Event()

        def run():
            reactor.callWhenRunning(started.set)
            reactor.run()

        thread = threading.Thread(target=run, daemon=True, name="twisted-reactor")
        thread.start()
        assert started.wait(10), "the Twisted reactor did not start"

    yield

    if reactor.running:
        reactor.callFromThread(reactor.stop)


@pytest.fixture(scope="session")
def sync():
    """
    Returns a helper that blocks the calling thread until a Deferred fires,
    then returns its result. A Failure is re-raised so tests fail loudly.
    """
    def _sync(deferred, timeout=15.0):
        done = threading.Event()
        box = {}

        def store(result):
            box["result"] = result
            done.set()

        deferred.addBoth(store)
        if not done.wait(timeout):
            raise AssertionError(f"le Deferred n'a pas ete resolu en {timeout}s")
        result = box["result"]
        if isinstance(result, Failure):
            result.raiseException()
        return result

    return _sync


@pytest.fixture(scope="session")
def on_reactor():
    """
    Calls a function on the reactor thread from another thread.
    Required because the API is not thread-safe.
    """
    def _on_reactor(func, *args, **kwargs):
        return threads.blockingCallFromThread(
            reactor, lambda: func(*args, **kwargs)
        )

    return _on_reactor


SESSION_INFO = {
    "name": "Waterloo",
    "key": "KEY1",
    "code": "CODE1",
    "active": True,
    "variant": "std",
    "game_json": {
        "game": {"max_players": 2, "max_watchers": 1},
        "fixed": [
            {
                "kind": "board", "id": "b1", "x": 0, "y": 0,
                "src": "board.png", "width": 800, "height": 600,
            }
        ],
        "movable": [
            {
                "kind": "token", "id": "t1", "x": 1, "y": 2,
                "front_src": "front.png", "back_src": "back.png",
                "width": 32, "height": 32,
            }
        ],
    },
}


@pytest.fixture
def session_info():
    """Fresh deep copy, so a test mutating it cannot leak into the next one."""
    import copy
    return copy.deepcopy(SESSION_INFO)

# OBG_Tourtoirac
Logical server for Online Boardgames

## Tests

The suite uses `pytest` (already a dependency) and runs against the real
Twisted reactor, which is started once for the whole session in a background
thread (the reactor cannot be restarted, see `tests/conftest.py`).

```bash
poetry install
poetry run pytest
```

Useful variations:

```bash
poetry run pytest tests/test_chabanas.py        # a single file
poetry run pytest -k keep_alive                 # by keyword
poetry run pytest -q                            # quiet
```

`tests/test_chabanas.py` starts a fake back-end on a random localhost port, so
no network access and no running service are required.

`tests/test_websocket.py` needs `autobahn[twisted]` to import `main`; it is
skipped automatically when the dependency is missing.

If pytest cannot write its cache directory (this happens on a Windows drive
mounted from WSL, which reports a bogus "File exists"), disable the cache:

```bash
poetry run pytest -p no:cacheprovider
```

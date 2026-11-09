"""
Tests of the debug_state action: on a development machine (OBG_DEBUG_STATE=1)
a reader of the table gets the game_json the session would store if everybody
left at once; elsewhere the action does not exist.
"""
from handle_message import chat_send, debug_state
from tests.test_bag import FakeProtocol, error_codes
from tests.test_chat import build_session, table
from tests.test_voice import ask, events


def test_the_action_does_not_exist_without_the_setting(monkeypatch):
    monkeypatch.delenv("OBG_DEBUG_STATE", raising=False)
    session = build_session()
    (alice, _), (a, _), _ = table(session)
    ask(debug_state, alice)
    assert error_codes(alice) == ["unknown_action"]
    assert events(a, "debug_state") == []


def test_the_state_stored_on_leaving_is_sent_to_the_asker_only(monkeypatch):
    monkeypatch.setenv("OBG_DEBUG_STATE", "1")
    session = build_session()
    (alice, _), (a, b), dave = table(session)
    ask(chat_send, alice, text="psst", to="bob")
    ask(debug_state, alice)
    [event] = events(a, "debug_state")
    assert event["content"] == session.game_json_state()
    # hidden information included: the private messages of the others too
    assert event["content"]["chat"]
    assert events(b, "debug_state") == [] and events(dave, "debug_state") == []


def test_a_watcher_may_ask_too(monkeypatch):
    monkeypatch.setenv("OBG_DEBUG_STATE", "1")
    session = build_session()
    _, _, dave = table(session)
    ask(debug_state, FakeProtocol(dave))
    assert len(events(dave, "debug_state")) == 1


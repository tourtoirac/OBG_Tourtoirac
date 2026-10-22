"""
Tests of the text chat: a general channel for everybody at the table, and a
private channel between each pair of players, kept with the saved state.
"""
from chat import MAX_HISTORY, MAX_TEXT_LENGTH, ChatHistory
from handle_message import chat_send
from session import Session
from tests.test_bag import LOGGER, FakeProtocol, FakeUser, error_codes
from tests.test_voice import ask, events


def build_session(chat=None):
    game_json = {
        "game": {"max_players": 3, "max_watchers": 2},
        "fixed": [],
        "movable": [],
        "options": {},
    }
    if chat is not None:
        game_json["chat"] = chat
    return Session(
        user=None, name="Waterloo", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
        # carol has a seat but is away from the table
        player_names=["alice", "bob", "carol"],
    )


def table(session):
    """Two players at the table and a watcher."""
    users = [FakeUser(name, session=session) for name in ("alice", "bob")]
    session.players = list(users)
    dave = FakeUser("dave", role="watcher", session=session)
    session.watchers = [dave]
    return [FakeProtocol(user) for user in users], users, dave


class TestGeneralChannel:
    def test_everybody_reads_a_general_message(self):
        session = build_session()
        (alice, _), (a, b), dave = table(session)
        ask(chat_send, alice, text="  hello  ")
        for user in (a, b, dave):
            message = events(user, "chat")[-1]["message"]
            assert message["from"] == "alice"
            assert message["to"] is None
            assert message["text"] == "hello"

    def test_a_watcher_does_not_write_in_the_general_channel(self):
        session = build_session()
        _, (a, _), dave = table(session)
        watcher = FakeProtocol(dave)
        ask(chat_send, watcher, text="go go")
        assert error_codes(watcher) == ["watcher_cannot_chat"]
        assert events(a, "chat") == []

    def test_an_empty_or_too_long_text_is_refused(self):
        session = build_session()
        (alice, _), (_, b), _ = table(session)
        for text in ("   ", "", None, 3, "x" * (MAX_TEXT_LENGTH + 1)):
            ask(chat_send, alice, text=text)
        assert error_codes(alice) == ["invalid_chat_text"] * 5
        assert events(b, "chat") == []

    def test_the_ids_grow(self):
        session = build_session()
        (alice, bob), (a, _), _ = table(session)
        ask(chat_send, alice, text="one")
        ask(chat_send, bob, text="two", to="alice")
        ask(chat_send, alice, text="three")
        assert [event["message"]["id"] for event in events(a, "chat")] == [1, 2, 3]


class TestPrivateChannels:
    def test_only_the_two_players_read_it(self):
        session = build_session()
        (alice, _), (a, b), dave = table(session)
        ask(chat_send, alice, text="psst", to="bob")
        assert events(a, "chat")[-1]["message"]["to"] == "bob"
        assert events(b, "chat")[-1]["message"]["text"] == "psst"
        assert events(dave, "chat") == []

    def test_a_player_away_finds_it_back(self):
        session = build_session()
        (alice, _), _, _ = table(session)
        ask(chat_send, alice, text="when you are back", to="carol")
        assert error_codes(alice) == []
        carol = FakeUser("carol", session=session)
        texts = [message["text"] for message in session.return_session_json(carol)["chat"]]
        assert texts == ["when you are back"]

    def test_a_watcher_does_not_write_privately(self):
        session = build_session()
        _, (a, _), dave = table(session)
        watcher = FakeProtocol(dave)
        ask(chat_send, watcher, text="hi", to="alice")
        assert error_codes(watcher) == ["watcher_cannot_chat"]
        assert events(a, "chat") == []

    def test_nobody_writes_to_an_unknown_player_or_to_itself(self):
        session = build_session()
        (alice, _), _, _ = table(session)
        ask(chat_send, alice, text="hi", to="dave")
        ask(chat_send, alice, text="hi", to="alice")
        ask(chat_send, alice, text="hi", to=["bob"])
        assert error_codes(alice) == ["unknown_player"] * 3

    def test_the_session_gives_each_reader_its_channels(self):
        session = build_session()
        (alice, bob), (a, b), dave = table(session)
        ask(chat_send, alice, text="all")
        ask(chat_send, alice, text="to bob", to="bob")
        ask(chat_send, bob, text="to carol", to="carol")
        read = lambda user: [m["text"] for m in session.return_session_json(user)["chat"]]
        assert read(a) == ["all", "to bob"]
        assert read(b) == ["all", "to bob", "to carol"]
        assert read(dave) == ["all"]
        assert [m["text"] for m in session.return_session_json()["chat"]] == ["all"]


class TestSaving:
    def test_the_chat_survives_a_resumed_session(self):
        session = build_session()
        (alice, bob), _, _ = table(session)
        ask(chat_send, alice, text="all")
        ask(chat_send, bob, text="secret", to="alice")
        state = session.game_json_state()
        resumed = build_session(chat=state["chat"])
        alice_again = FakeUser("alice", session=resumed)
        assert [m["text"] for m in resumed.return_session_json(alice_again)["chat"]] == ["all", "secret"]
        # the next message follows the saved ones
        resumed.players = [alice_again]
        resumed.post_chat(alice_again, "next")
        assert resumed.chat.general[-1]["id"] == 3

    def test_no_chat_is_saved_before_anybody_writes(self):
        assert "chat" not in build_session().game_json_state()

    def test_a_broken_history_is_dropped(self):
        chat = ChatHistory.from_json({
            "general": [{"id": 1, "from": "a", "to": None, "text": "ok"}, {"id": "x"}, 4],
            "private": [{"players": ["a", "a"], "messages": []}, "junk",
                        {"players": ["a", "b"], "messages": [{"id": 2, "from": "a", "to": "c", "text": "no"}]}],
        })
        assert [m["text"] for m in chat.all_messages()] == ["ok"]
        assert chat.next_id == 2
        assert ChatHistory.from_json("junk").is_empty()

    def test_old_messages_are_forgotten(self):
        chat = ChatHistory()
        for index in range(MAX_HISTORY + 5):
            chat.post("a", str(index))
        assert len(chat.general) == MAX_HISTORY
        assert chat.general[0]["text"] == "5"

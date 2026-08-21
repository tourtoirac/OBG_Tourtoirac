import json
from unittest.mock import Mock

import pytest

from main import (
    User,
    Table,
    Room,
    GameWebSocketProtocol,
    GameWebSocketFactory,
    create_room,
)


# ============================================================
# User
# ============================================================

class TestUser:

    def test_create_user(self):
        protocol = Mock()

        user = User("Alice", protocol)

        assert user.name == "Alice"
        assert user.protocol == protocol
        assert user.table is None
        assert user.id is not None

    def test_user_id_is_unique(self):
        protocol = Mock()

        user1 = User("Alice", protocol)
        user2 = User("Bob", protocol)

        assert user1.id != user2.id

    def test_join_table(self):
        user = User("Alice", Mock())
        table = Table(1)

        user.join_table(table)

        assert user.table is table

    def test_leave_table(self):
        user = User("Alice", Mock())
        table = Table(1)

        user.join_table(table)
        user.leave_table()

        assert user.table is None

    def test_send(self):
        protocol = Mock()
        user = User("Alice", protocol)

        message = {
            "event": "test",
            "value": 123,
        }

        user.send(message)

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        assert json.loads(args[0].decode("utf-8")) == message
        assert kwargs["isBinary"] is False

    def test_repr_without_table(self):
        user = User("Alice", Mock())

        result = repr(user)

        assert "Alice" in result
        assert "table=None" in result

    def test_repr_with_table(self):
        user = User("Alice", Mock())
        table = Table(42)

        user.join_table(table)

        result = repr(user)

        assert "Alice" in result
        assert "table=42" in result


# ============================================================
# Table
# ============================================================

class TestTable:

    def test_create_table(self):
        table = Table(1)

        assert table.id == 1
        assert table.max_users == 2
        assert table.users == []

    def test_create_table_with_custom_size(self):
        table = Table(10, max_users=4)

        assert table.id == 10
        assert table.max_users == 4

    def test_add_user(self):
        table = Table(1)
        user = User("Alice", Mock())

        result = table.add_user(user)

        assert result is True
        assert user in table.users

    def test_add_same_user_twice(self):
        table = Table(1)
        user = User("Alice", Mock())

        assert table.add_user(user) is True
        assert table.add_user(user) is False

        assert len(table.users) == 1

    def test_add_user_when_table_is_full(self):
        table = Table(1, max_users=2)

        user1 = User("Alice", Mock())
        user2 = User("Bob", Mock())
        user3 = User("Charlie", Mock())

        assert table.add_user(user1) is True
        assert table.add_user(user2) is True
        assert table.add_user(user3) is False

        assert len(table.users) == 2

    def test_remove_user(self):
        table = Table(1)
        user = User("Alice", Mock())

        table.add_user(user)
        table.remove_user(user)

        assert user not in table.users

    def test_remove_unknown_user(self):
        table = Table(1)
        user = User("Alice", Mock())

        table.remove_user(user)

        assert table.users == []

    def test_is_full(self):
        table = Table(1, max_users=2)

        user1 = User("Alice", Mock())
        user2 = User("Bob", Mock())

        assert table.is_full() is False

        table.add_user(user1)

        assert table.is_full() is False

        table.add_user(user2)

        assert table.is_full() is True

    def test_repr(self):
        table = Table(42, max_users=4)

        assert repr(table) == "Table(id=42, users=0/4)"


# ============================================================
# Room
# ============================================================

class TestRoom:

    def test_create_room(self):
        room = Room()

        assert room.users == []
        assert room.tables == []

    def test_add_table(self):
        room = Room()
        table = Table(1)

        room.add_table(table)

        assert table in room.tables

    def test_get_table_existing(self):
        room = Room()
        table = Table(1)

        room.add_table(table)

        assert room.get_table(1) is table

    def test_get_table_unknown(self):
        room = Room()

        assert room.get_table(999) is None

    def test_add_user(self):
        room = Room()
        user = User("Alice", Mock())

        room.add_user(user)

        assert user in room.users

    def test_remove_user(self):
        room = Room()
        user = User("Alice", Mock())

        room.add_user(user)
        room.remove_user(user)

        assert user not in room.users

    def test_remove_unknown_user(self):
        room = Room()
        user = User("Alice", Mock())

        room.remove_user(user)

        assert room.users == []

    def test_remove_user_from_room_and_table(self):
        room = Room()
        table = Table(1)

        room.add_table(table)

        user = User("Alice", Mock())
        room.add_user(user)

        success, error = room.join_table(user, 1)

        assert success is True
        assert error is None

        assert user in room.users
        assert user in table.users
        assert user.table is table

        room.remove_user(user)

        assert user not in room.users
        assert user not in table.users
        assert user.table is None

    def test_get_user_existing(self):
        room = Room()
        user = User("Alice", Mock())

        room.add_user(user)

        assert room.get_user(user.id) is user

    def test_get_user_unknown(self):
        room = Room()

        assert room.get_user("unknown-id") is None

    def test_repr(self):
        room = Room()

        assert repr(room) == "Room(users=0, tables=0)"


# ============================================================
# Room.join_table()
# ============================================================

class TestRoomJoinTable:

    def test_join_unknown_table(self):
        room = Room()
        user = User("Alice", Mock())

        room.add_user(user)

        success, error = room.join_table(user, 999)

        assert success is False
        assert error == "table_not_found"

        assert user.table is None

    def test_join_table(self):
        room = Room()
        table = Table(1)

        room.add_table(table)

        user = User("Alice", Mock())
        room.add_user(user)

        success, error = room.join_table(user, 1)

        assert success is True
        assert error is None

        assert user.table is table
        assert user in table.users

    def test_join_full_table(self):
        room = Room()
        table = Table(1, max_users=1)

        room.add_table(table)

        user1 = User("Alice", Mock())
        user2 = User("Bob", Mock())

        room.add_user(user1)
        room.add_user(user2)

        success, error = room.join_table(user1, 1)

        assert success is True
        assert error is None

        success, error = room.join_table(user2, 1)

        assert success is False
        assert error == "table_full"

        assert user2.table is None

    def test_move_user_to_another_table(self):
        room = Room()

        table1 = Table(1)
        table2 = Table(2)

        room.add_table(table1)
        room.add_table(table2)

        user = User("Alice", Mock())
        room.add_user(user)

        success, error = room.join_table(user, 1)

        assert success is True
        assert user.table is table1
        assert user in table1.users

        success, error = room.join_table(user, 2)

        assert success is True
        assert error is None

        assert user.table is table2
        assert user not in table1.users
        assert user in table2.users


# ============================================================
# GameWebSocketProtocol - fixtures
# ============================================================

@pytest.fixture
def protocol():
    protocol = GameWebSocketProtocol()

    # Attribut normalement initialisé par onConnect()
    protocol.user = None

    # Évite toute communication WebSocket réelle
    protocol.sendMessage = Mock()

    # Mock de la factory WebSocket
    protocol.factory = Mock()

    return protocol


@pytest.fixture
def room():
    return create_room()


# ============================================================
# GameWebSocketProtocol - connexion
# ============================================================

class TestGameWebSocketProtocolConnection:

    def test_on_connect(self, protocol):
        request = Mock()
        request.peer = "127.0.0.1"

        protocol.onConnect(request)

        assert protocol.user is None

    def test_on_open(self, protocol, capsys):
        protocol.onOpen()

        captured = capsys.readouterr()

        assert "[WEBSOCKET] Connexion WebSocket ouverte" in captured.out


# ============================================================
# GameWebSocketProtocol - premier message
# ============================================================

class TestGameWebSocketProtocolFirstMessage:

    def test_first_message_must_be_connect(self, protocol):
        protocol.handle_first_message({
            "action": "join",
            "table_id": 1,
        })

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "not_connected"
        assert payload["error"]["message"] == (
            "The first message must be a connect message"
        )

        assert kwargs["isBinary"] is False
        assert protocol.user is None

    def test_connect_requires_name(self, protocol):
        protocol.handle_first_message({
            "action": "connect",
        })

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "missing_name"
        assert payload["error"]["message"] == (
            "The connect message requires a name"
        )

        assert protocol.user is None

    def test_connect_with_empty_name(self, protocol):
        protocol.handle_first_message({
            "action": "connect",
            "name": "",
        })

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "missing_name"

        assert protocol.user is None

    def test_connect_success(self, protocol, room):
        protocol.factory.room = room

        protocol.handle_first_message({
            "action": "connect",
            "name": "Alice",
        })

        assert protocol.user is not None
        assert protocol.user.name == "Alice"
        assert protocol.user.protocol is protocol

        assert protocol.user in room.users

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "connected"
        assert payload["user"]["id"] == protocol.user.id
        assert payload["user"]["name"] == "Alice"

        assert kwargs["isBinary"] is False


# ============================================================
# GameWebSocketProtocol - messages
# ============================================================

class TestGameWebSocketProtocolMessages:

    def test_unknown_action(self, protocol):
        protocol.user = User("Alice", protocol)

        protocol.handle_message({
            "action": "unknown",
        })

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "unknown_action"
        assert payload["error"]["message"] == (
            "Unknown action: unknown"
        )

    def test_join_missing_table_id(self, protocol):
        protocol.user = User("Alice", protocol)

        protocol.handle_join({})

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "missing_table_id"
        assert payload["error"]["message"] == (
            "The join message requires a table_id"
        )

    def test_join_unknown_table(self, protocol, room):
        protocol.factory.room = room
        protocol.user = User("Alice", protocol)

        protocol.handle_join({
            "action": "join",
            "table_id": 999,
        })

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "error"
        assert payload["error"]["code"] == "table_not_found"
        assert payload["error"]["message"] == (
            "Unable to join table 999"
        )

        assert protocol.user.table is None

    def test_join_success(self, protocol, room):
        protocol.factory.room = room
        protocol.user = User("Alice", protocol)

        protocol.handle_join({
            "action": "join",
            "table_id": 1,
        })

        assert protocol.user.table is room.get_table(1)

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload["event"] == "joined"
        assert payload["table"]["id"] == 1

        assert len(payload["table"]["users"]) == 1
        assert payload["table"]["users"][0]["id"] == protocol.user.id
        assert payload["table"]["users"][0]["name"] == "Alice"


# ============================================================
# GameWebSocketProtocol - onMessage()
# ============================================================

class TestGameWebSocketProtocolOnMessage:

    def test_invalid_json(self, protocol):
        payload = b"this is not json"

        protocol.onMessage(
            payload,
            False,
        )

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        response = json.loads(args[0].decode("utf-8"))

        assert response["event"] == "error"
        assert response["error"]["code"] == "invalid_json"
        assert response["error"]["message"] == (
            "Invalid JSON message"
        )

    def test_binary_message(self, protocol):
        payload = b'{"action":"connect","name":"Alice"}'

        protocol.onMessage(
            payload,
            True,
        )

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        response = json.loads(args[0].decode("utf-8"))

        assert response["event"] == "error"
        assert response["error"]["code"] == "binary_not_supported"
        assert response["error"]["message"] == (
            "Only JSON text messages are supported"
        )

    def test_on_message_connect(self, protocol, room):
        protocol.factory.room = room

        payload = json.dumps({
            "action": "connect",
            "name": "Alice",
        }).encode("utf-8")

        protocol.onMessage(
            payload,
            False,
        )

        assert protocol.user is not None
        assert protocol.user.name == "Alice"

        args, kwargs = protocol.sendMessage.call_args

        response = json.loads(args[0].decode("utf-8"))

        assert response["event"] == "connected"
        assert response["user"]["name"] == "Alice"

    def test_on_message_join(self, protocol, room):
        protocol.factory.room = room

        # Connexion
        connect_payload = json.dumps({
            "action": "connect",
            "name": "Alice",
        }).encode("utf-8")

        protocol.onMessage(
            connect_payload,
            False,
        )

        protocol.sendMessage.reset_mock()

        # Join
        join_payload = json.dumps({
            "action": "join",
            "table_id": 1,
        }).encode("utf-8")

        protocol.onMessage(
            join_payload,
            False,
        )

        args, kwargs = protocol.sendMessage.call_args

        response = json.loads(args[0].decode("utf-8"))

        assert response["event"] == "joined"
        assert response["table"]["id"] == 1
        assert response["table"]["users"][0]["name"] == "Alice"


# ============================================================
# GameWebSocketProtocol - erreurs
# ============================================================

class TestGameWebSocketProtocolErrors:

    def test_send_error(self, protocol):
        protocol.send_error(
            "test_error",
            "Something went wrong",
        )

        protocol.sendMessage.assert_called_once()

        args, kwargs = protocol.sendMessage.call_args

        payload = json.loads(args[0].decode("utf-8"))

        assert payload == {
            "event": "error",
            "error": {
                "code": "test_error",
                "message": "Something went wrong",
            },
        }

        assert kwargs["isBinary"] is False


# ============================================================
# GameWebSocketProtocol - fermeture
# ============================================================

class TestGameWebSocketProtocolClose:

    def test_on_close_without_user(self, protocol):
        protocol.user = None
        protocol.factory.room = Mock()

        protocol.onClose(
            True,
            1000,
            "Normal closure",
        )

        protocol.factory.room.remove_user.assert_not_called()

    def test_on_close_with_user(self, protocol):
        room = Mock()
        user = User("Alice", protocol)

        protocol.factory.room = room
        protocol.user = user

        protocol.onClose(
            True,
            1000,
            "Normal closure",
        )

        room.remove_user.assert_called_once_with(user)

        assert protocol.user is None


# ============================================================
# GameWebSocketFactory
# ============================================================

class TestGameWebSocketFactory:

    def test_create_factory(self):
        room = Room()

        factory = GameWebSocketFactory(
            "ws://localhost:9000",
            room,
        )

        assert factory.room is room
        assert factory.protocol is GameWebSocketProtocol


# ============================================================
# create_room()
# ============================================================

class TestCreateRoom:

    def test_create_room(self):
        room = create_room()

        assert isinstance(room, Room)
        assert len(room.tables) == 3

    def test_create_room_tables(self):
        room = create_room()

        table1 = room.get_table(1)
        table2 = room.get_table(2)
        table3 = room.get_table(3)

        assert table1 is not None
        assert table2 is not None
        assert table3 is not None

        assert table1.max_users == 2
        assert table2.max_users == 2
        assert table3.max_users == 4

    def test_create_room_tables_are_empty(self):
        room = create_room()

        for table in room.tables:
            assert table.users == []
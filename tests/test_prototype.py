"""A game in prototyping: the key travels from the lobby to Chabanas, which decides."""

from twisted.internet import defer

from tests.test_lobby import connect, lobby  # noqa: F401  (fixture)


class TestPrototypeKey:

    def test_create_forwards_the_key_and_reads_the_refusal(self, lobby, sync, session_info):
        host = connect(lobby, "alice")
        lobby.chabanas.create_status = 423

        success, error = sync(lobby.create_session("Waterloo", host, "", prototype_key="s3cret"))

        assert success is False
        assert error == "prototype_key_incorrect"
        payload = dict(lobby.chabanas.payloads)["/session/create"]
        assert payload["prototype_key"] == "s3cret"

    def test_join_forwards_the_key_and_reads_the_refusal(self, lobby, sync, session_info):
        guest = connect(lobby, "bob")
        lobby.chabanas.join_status = 423

        success, error = sync(lobby.join_session(session_info["code"], guest, "", "watcher", "",
                                                 None, "s3cret"))

        assert success is False
        assert error == "prototype_key_incorrect"
        payload = dict(lobby.chabanas.payloads)["/session/join"]
        assert payload["prototype_key"] == "s3cret"

    def test_unlock(self, lobby, sync):
        statuses = {"ok": 200, "nope": 423}

        def post(path, payload, with_status=False):
            return defer.succeed((statuses[payload["prototype_key"]], {}))

        lobby.chabanas._post_json = post
        assert sync(lobby.chabanas.unlock_game("Waterloo", "ok")) == (True, None)
        assert sync(lobby.chabanas.unlock_game("Waterloo", "nope")) == (False, "prototype_key_incorrect")


class TestLobbyListingHidesSecrets:

    def test_no_session_key_nor_access_key_reaches_the_browsers(self, lobby, sync, session_info):
        lobby.chabanas.list_response = {"sessions": {"Waterloo": [{
            "code": session_info["code"], "name": "Waterloo", "key": "SESSION-UUID",
            "access_key": "AK", "players": [],
        }]}}

        result = sync(lobby.return_active_sessions([]))

        listed = result["active"]["Waterloo"][0]
        assert "key" not in listed
        assert "access_key" not in listed
        assert listed["code"] == session_info["code"]

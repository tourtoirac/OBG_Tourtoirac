"""
Tests of the "anonymous_id" option of a game_json: the ids of the tokens are
replaced by random ones when the session is first built, so that an id does
not tell what a token is. The new ids are the ones stored with the session.
"""
import copy
import json
import uuid

from session import Session, anonymize_ids

TOKEN = {"kind": "token", "id": "us_marines-1", "x": 10, "y": 20, "width": 40, "height": 40,
         "front_src": "/front.png", "back_src": "/back.png"}
BAG = {"kind": "bag", "id": "bag", "x": 0, "y": 0, "width": 100, "height": 100,
       "components": [dict(TOKEN, id="chit", x=None, y=None, copies=2)]}
GAME = {
    "game": {"max_players": 2, "max_watchers": 2},
    "options": {"anonymous_id": True},
    "fixed": [{"kind": "board", "id": "map", "x": 0, "y": 0, "width": 800, "height": 600, "src": "/map.png"}, BAG],
    "movable": [TOKEN, dict(TOKEN, id="nva-1", x=50)],
    "setup": [
        {"component_id": "nva-1", "x": 300, "y": 310, "side": "back"},
        {"component_id": "map", "x": 5, "y": 5},
        {"component_id": "unknown", "x": 1, "y": 1},
    ],
}


def build(game_json):
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def is_uuid(value):
    return str(uuid.UUID(value)) == value


class TestAnonymize:
    def test_every_token_gets_a_random_id(self):
        session = build(copy.deepcopy(GAME))
        ids = [token.id for token in session.components_lists["movable"]]
        assert len(ids) == 2 and len(set(ids)) == 2
        assert all(is_uuid(component_id) for component_id in ids)
        assert session.get_component("us_marines-1") is False
        # the order of the game_json is kept: the first token is still the first
        assert (session.components_lists["movable"][0].x, session.components_lists["movable"][1].x) == (10, 50)

    def test_the_tokens_of_a_bag_and_their_copies_too(self):
        session = build(copy.deepcopy(GAME))
        chits = session.get_component("bag").components
        assert len(chits) == 2
        assert all(is_uuid(chit.id) for chit in chits)
        assert chits[0].id != chits[1].id

    def test_the_other_components_keep_their_id(self):
        session = build(copy.deepcopy(GAME))
        assert session.get_component("map") is not False
        assert session.get_component("bag") is not False

    def test_the_setup_follows_the_new_ids(self):
        session = build(copy.deepcopy(GAME))
        nva = session.components_lists["movable"][1]
        session.apply_setup()
        assert (nva.x, nva.y, nva.side) == (300, 310, "back")
        assert (session.get_component("map").x, session.get_component("map").y) == (5, 5)

    def test_the_game_json_received_is_left_untouched(self):
        game = copy.deepcopy(GAME)
        build(game)
        assert game == GAME

    def test_two_sessions_of_the_same_game_get_different_ids(self):
        first = build(copy.deepcopy(GAME)).components_lists["movable"][0].id
        second = build(copy.deepcopy(GAME)).components_lists["movable"][0].id
        assert first != second


class TestStoredState:
    def test_the_new_ids_are_stored_and_kept_by_a_resumed_session(self):
        session = build(copy.deepcopy(GAME))
        state = json.loads(json.dumps(session.game_json_state()))
        assert state["options"] == {"anonymous_id": True, "anonymized": True}
        stored_ids = [token["id"] for token in state["movable"]]
        assert stored_ids == [token.id for token in session.components_lists["movable"]]
        # the setup was not applied yet: it is stored with the new id
        assert state["setup"][0]["component_id"] == stored_ids[1]

        resumed = build(state)
        assert [token.id for token in resumed.components_lists["movable"]] == stored_ids
        assert [chit.id for chit in resumed.get_component("bag").components] == [
            chit.id for chit in session.get_component("bag").components]


class TestOption:
    def test_without_the_option_the_ids_are_those_of_the_game(self):
        for options in ({}, {"anonymous_id": False}, {"anonymous_id": "true"}, {"anonymous_id": 1}):
            game = dict(copy.deepcopy(GAME), options=options)
            assert anonymize_ids(game) is game
            assert build(game).get_component("us_marines-1") is not False

    def test_a_game_json_without_options_is_left_alone(self):
        game = {key: value for key, value in copy.deepcopy(GAME).items() if key != "options"}
        assert anonymize_ids(game) is game

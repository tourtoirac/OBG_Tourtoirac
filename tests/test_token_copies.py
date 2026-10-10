"""
Tests of the "copies" key of a token: a token declared with "copies": n is
replaced, when the session is built, by n identical tokens whose ids are the
declared id followed by -01, -02... The declared token is not created, and
"copies" never reaches the saved state of a session.
"""
import pytest

from session import Session, duplicate_component_ids, expand_copies

# the shape of the "prep-fire" token of Squad Leader
PREP_FIRE = {
    "x": 4350,
    "y": 0,
    "id": "prep-fire",
    "kind": "token",
    "side": "front",
    "width": 110,
    "border": True,
    "copies": 20,
    "height": 110,
    "origin": None,
    "back_src": None,
    "front_src": "/Games/Squad_Leader/resources/counters/prep-fire.jpg",
    "orientable": False,
    "move_border": False,
    "orientation": 0,
}

PLAIN_TOKEN = {
    "x": 100,
    "y": 100,
    "id": "squad",
    "kind": "token",
    "front_src": "/squad.jpg",
    "back_src": None,
    "width": 110,
    "height": 110,
}


def build_session(movable, **extra):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [],
        "movable": movable,
        **extra,
    }
    return Session(
        user=None, name="Squad Leader", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class TestExpandCopies:
    def test_ids_are_numbered_on_two_digits(self):
        ids = [copy["id"] for copy in expand_copies(PREP_FIRE)]
        assert len(ids) == 20
        assert ids[0] == "prep-fire-01"
        assert ids[8] == "prep-fire-09"
        assert ids[19] == "prep-fire-20"

    def test_copies_keep_every_other_field(self):
        expected = {key: value for key, value in PREP_FIRE.items() if key not in ("id", "copies")}
        for copy in expand_copies(PREP_FIRE):
            assert "copies" not in copy
            assert {key: value for key, value in copy.items() if key != "id"} == expected

    def test_declared_token_is_left_untouched(self):
        expand_copies(PREP_FIRE)
        assert PREP_FIRE["id"] == "prep-fire"
        assert PREP_FIRE["copies"] == 20

    def test_hundred_copies_take_three_digits(self):
        ids = [copy["id"] for copy in expand_copies(dict(PREP_FIRE, copies=100))]
        assert ids[0] == "prep-fire-001"
        assert ids[-1] == "prep-fire-100"

    def test_token_without_copies_is_returned_alone(self):
        assert expand_copies(PLAIN_TOKEN) == [PLAIN_TOKEN]

    @pytest.mark.parametrize("copies", [0, -3, "20", 2.0, True, None])
    def test_invalid_copies_is_ignored(self, copies):
        token = dict(PREP_FIRE, copies=copies)
        assert expand_copies(token) == [token]

    def test_only_tokens_are_copied(self):
        board = {"id": "map", "kind": "board", "copies": 3}
        assert expand_copies(board) == [board]


class TestSessionWithCopies:
    def test_copies_replace_the_declared_token(self):
        session = build_session([PLAIN_TOKEN, PREP_FIRE])
        ids = [component.id for component in session.components_lists["movable"]]
        assert ids == ["squad"] + [f"prep-fire-{number:02d}" for number in range(1, 21)]
        assert "prep-fire" not in session.components_dict
        assert session.get_component("prep-fire-07") is session.components_lists["movable"][7]

    def test_copies_have_the_features_of_the_declared_token(self):
        session = build_session([PREP_FIRE])
        for component in session.components_lists["movable"]:
            assert (component.x, component.y) == (4350, 0)
            assert (component.width, component.height) == (110, 110)
            assert component.image_src["front"] == PREP_FIRE["front_src"]
            assert component.border is True
            assert component.move_border is False

    def test_copies_are_independent_components(self):
        session = build_session([PREP_FIRE])
        first, second = session.components_lists["movable"][:2]
        first.x = 10
        assert second.x == 4350

    def test_saved_state_does_not_store_copies(self):
        session = build_session([PREP_FIRE])
        state = session.game_json_state()
        assert len(state["movable"]) == 20
        assert all("copies" not in token for token in state["movable"])

    def test_resumed_session_does_not_copy_again(self):
        state = build_session([PREP_FIRE]).game_json_state()
        resumed = Session(
            user=None, name="Squad Leader", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=state,
        )
        ids = [component.id for component in resumed.components_lists["movable"]]
        assert ids == [f"prep-fire-{number:02d}" for number in range(1, 21)]

    def test_setup_reaches_a_copy_by_its_own_id(self):
        session = build_session(
            [PREP_FIRE],
            setup=[
                {"component_id": "prep-fire-02", "x": 500, "y": 600},
                # the declared token does not exist: this entry is ignored
                {"component_id": "prep-fire", "x": 1, "y": 2},
            ],
        )
        session.apply_setup()
        assert (session.get_component("prep-fire-02").x, session.get_component("prep-fire-02").y) == (500, 600)
        assert session.get_component("prep-fire-01").x == 4350


class TestDuplicateIdsWithCopies:
    def test_copies_are_not_duplicates(self):
        assert duplicate_component_ids({"movable": [PLAIN_TOKEN, PREP_FIRE]}) == []

    def test_copy_id_colliding_with_a_declared_id(self):
        clash = dict(PLAIN_TOKEN, id="prep-fire-03")
        assert duplicate_component_ids({"movable": [PREP_FIRE, clash]}) == ["prep-fire-03"]

    def test_declared_id_is_free_once_copied(self):
        # the declared token is not created, so its id is not taken
        other = dict(PLAIN_TOKEN, id="prep-fire")
        assert duplicate_component_ids({"movable": [PREP_FIRE, other]}) == []

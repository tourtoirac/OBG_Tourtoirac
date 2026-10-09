"""
Tests of the board_group component: one to n boards that the client turns over
together. The server only loads, indexes and saves the group; the flip itself
stays local to each player.
"""
import pytest

from Components.board import Board
from Components.board_group import BoardGroup
from session import Session, duplicate_component_ids

GRID = {"type": "hex", "orientation": "flat", "origin_x": 50, "origin_y": 60, "size": 40}

NORTH = {
    "kind": "board", "id": "north", "src": "/north.jpg",
    "x": 0, "y": 0, "width": 800, "height": 600,
}
SOUTH = {
    "kind": "board", "id": "south", "src": "/south.jpg",
    "x": 0, "y": 600, "width": 800, "height": 600,
    "grid": GRID,
}
GROUP = {"kind": "board_group", "id": "map", "flippable": True, "magnetism": True, "boards": [NORTH, SOUTH]}

TOKEN = {
    "kind": "token", "id": "token", "front_src": "/unit.png", "back_src": None,
    "x": 2000, "y": 2000, "width": 30, "height": 30,
}


def build_session(*fixed):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": list(fixed) if fixed else [GROUP],
        "movable": [TOKEN],
        "dice": [],
    }
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class TestLoading:
    def test_the_group_is_a_fixed_component(self):
        session = build_session()
        (group,) = session.components_lists["fixed"]
        assert isinstance(group, BoardGroup)
        assert group.view_flippable is True
        assert [board.id for board in group.boards] == ["north", "south"]

    def test_each_board_is_indexed_by_its_id(self):
        session = build_session()
        assert isinstance(session.get_component("north"), Board)
        assert session.get_component("south").grid is not None

    def test_the_group_itself_is_not_an_action_target(self):
        assert build_session().get_component("map") is False

    def test_flippable_defaults_to_false(self):
        group = {k: v for k, v in GROUP.items() if k != "flippable"}
        assert build_session(group).components_lists["fixed"][0].view_flippable is False

    @pytest.mark.parametrize("boards", [[], None, [{"kind": "token", "id": "x"}]])
    def test_a_group_without_board_is_ignored(self, boards):
        session = build_session(dict(GROUP, boards=boards))
        assert session.components_lists["fixed"] == []

    def test_boards_flattens_groups_in_drawing_order(self):
        alone = dict(NORTH, id="alone")
        session = build_session(alone, GROUP)
        assert [board.id for board in session.boards()] == ["alone", "north", "south"]


class TestSave:
    def test_the_saved_state_resumes_the_same_group(self):
        session = build_session()
        saved = session.game_json_state()
        (group_json,) = saved["fixed"]
        assert group_json["kind"] == "board_group"
        assert group_json["flippable"] is True
        assert [board["id"] for board in group_json["boards"]] == ["north", "south"]
        resumed = Session(
            user=None, name="Vietnam", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=saved,
        )
        assert isinstance(resumed.components_lists["fixed"][0], BoardGroup)
        assert resumed.components_lists["fixed"][0].magnetism is True
        assert resumed.get_component("south").group.magnetism is True

    def test_a_board_carries_no_flippable(self):
        board = dict(NORTH, flippable=True)
        session = build_session(dict(GROUP, boards=[board]))
        assert "flippable" not in session.get_component("north").return_json()
        assert "flippable" not in session.game_json_state()["fixed"][0]["boards"][0]

    def test_a_board_carries_no_magnetism(self):
        board = dict(SOUTH, magnetism=True)
        session = build_session(dict(GROUP, boards=[board]))
        assert "magnetism" not in session.get_component("south").return_json()

    def test_the_boards_positions_are_rounded(self):
        session = build_session()
        session.get_component("south").x = 10.6
        saved = session.game_json_state()
        assert saved["fixed"][0]["boards"][1]["x"] == 11


class TestSetupAndGrid:
    def test_the_setup_moves_a_board_of_the_group(self):
        session = build_session()
        session.setup = [{"component_id": "south", "x": 900, "y": 0}]
        applied = session.apply_setup()
        assert (session.get_component("south").x, session.get_component("south").y) == (900, 0)
        assert applied[0]["id"] == "south"

    def test_a_token_snaps_to_the_grid_of_a_board_of_the_group(self):
        session = build_session()
        token = session.get_component("token")
        # center dropped at (53, 664): hex (0, 0) of "south" is at (50, 660)
        x, y = session.snap_to_grid(token, 53 - 15, 664 - 15)
        assert (x + 15, y + 15) == pytest.approx((50, 660))


    def test_a_group_without_magnetism_does_not_snap(self):
        session = build_session(dict(GROUP, magnetism=False))
        token = session.get_component("token")
        assert session.snap_to_grid(token, 53 - 15, 664 - 15) == (53 - 15, 664 - 15)

    def test_a_board_alone_does_not_snap(self):
        session = build_session(dict(SOUTH, magnetism=True))
        token = session.get_component("token")
        assert session.snap_to_grid(token, 53 - 15, 664 - 15) == (53 - 15, 664 - 15)


class TestDuplicateIds:
    def test_a_board_id_repeated_inside_and_outside_a_group(self):
        game_json = {"fixed": [GROUP, dict(NORTH)], "movable": [], "dice": []}
        assert duplicate_component_ids(game_json) == ["north"]

    def test_a_group_id_repeated(self):
        game_json = {"fixed": [GROUP], "movable": [dict(TOKEN, id="map")], "dice": []}
        assert duplicate_component_ids(game_json) == ["map"]

    def test_unique_ids(self):
        assert duplicate_component_ids({"fixed": [GROUP], "movable": [TOKEN]}) == []

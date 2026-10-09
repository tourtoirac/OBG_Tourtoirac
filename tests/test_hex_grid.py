"""
Tests of the hex grid of a board: a token released on a board that declares a
grid in its game_json has its center pulled onto the center of the nearest hex.

The release goes through the message handler with fakes, the way test_setup.py
does, so the snap is covered even where autobahn is missing.
"""
import logging
import math

import pytest

from Components.board import Board
from Components.board_group import BoardGroup
from Components.hex_grid import HexGrid, cube_round
from handle_message import release
from session import Session
from user import User

LOGGER = logging.getLogger("tests")

GRID = {
    "type": "hex",
    "orientation": "flat",
    "origin_x": 50,
    "origin_y": 60,
    "size": 40,
}

BOARD_JSON = {
    "x": 1000,
    "y": 2000,
    "id": "map",
    "kind": "board",
    "src": "/map.jpg",
    "width": 800,
    "height": 600,
    "grid": GRID,
}

# a second board, without grid, next to the map
PLAIN_BOARD_JSON = {
    "x": 0,
    "y": 0,
    "id": "track",
    "kind": "board",
    "src": "/track.jpg",
    "width": 500,
    "height": 500,
}

TOKEN_JSON = {
    "x": 10,
    "y": 10,
    "id": "token",
    "kind": "token",
    "front_src": "/counters/unit.png",
    "back_src": None,
    "width": 30,
    "height": 30,
}


def build_session(board_json=None, magnetism=True, **token_fields):
    token = dict(TOKEN_JSON)
    token.update(token_fields)
    # the magnetism belongs to the board_group holding the map
    group = {
        "kind": "board_group", "id": "group", "magnetism": magnetism,
        "boards": [board_json or BOARD_JSON],
    }
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [PLAIN_BOARD_JSON, group],
        "movable": [token],
        "dice": [],
    }
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class FakeUser(User):
    def __init__(self, session):
        super().__init__(name="alice", protocol=None)
        self.role = "player"
        self.session = session
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeProtocol:
    def __init__(self, user):
        self.user = user
        self.sent = []
        self.factory = type("Factory", (), {"lobby": self})()

    def get_user(self, protocol):
        return self.user

    def send_error(self, code, message):
        self.sent.append({"event": "error", "error": {"code": code, "message": message}})


def release_at(drop_x, drop_y, board_json=None, magnetism=True, **token_fields):
    """Acquires the token, releases it at (drop_x, drop_y) and returns it."""
    session = build_session(board_json, magnetism, **token_fields)
    user = FakeUser(session)
    session.players = [user]
    token = session.get_component("token")
    assert token.acquire(user) is True
    release(FakeProtocol(user), LOGGER, {
        "action": "release", "component_id": "token", "x": drop_x, "y": drop_y,
    })
    return token


def center(token):
    return token.x + token.width / 2, token.y + token.height / 2


class TestCubeRound:
    def test_a_point_near_a_center_rounds_to_it(self):
        assert cube_round(0.1, -0.1) == (0, 0)
        assert cube_round(2.9, 1.05) == (3, 1)

    def test_a_point_near_a_corner_picks_the_right_hex(self):
        # rounding q and r separately would give (1, 1), a hex two steps away
        # whose cube coordinates no longer sum to zero
        assert cube_round(0.6, 0.6) == (1, 0)


class TestHexGrid:
    @pytest.mark.parametrize("orientation", ["flat", "pointy"])
    def test_a_center_snaps_to_itself(self, orientation):
        grid = HexGrid(orientation, 50, 60, 40)
        for x in range(0, 800, 37):
            for y in range(0, 600, 41):
                cx, cy = grid.nearest_center(x, y)
                assert grid.nearest_center(cx, cy) == pytest.approx((cx, cy))

    @pytest.mark.parametrize("orientation", ["flat", "pointy"])
    def test_the_nearest_center_is_inside_the_hex(self, orientation):
        """
        A point is never further from its center than the hex radius, and no
        other center is nearer: that is what "the hex that contains it" means.
        """
        grid = HexGrid(orientation, 50, 60, 40)
        for x in range(0, 800, 13):
            for y in range(0, 600, 17):
                cx, cy = grid.nearest_center(x, y)
                distance = math.hypot(cx - x, cy - y)
                assert distance <= 40 + 1e-9
                for dx in (-120, -60, 0, 60, 120):
                    for dy in (-120, -70, 0, 70, 120):
                        ox, oy = grid.nearest_center(x + dx, y + dy)
                        assert math.hypot(ox - x, oy - y) >= distance - 1e-9

    def test_the_origin_is_a_center(self):
        assert HexGrid("flat", 50, 60, 40).nearest_center(52, 57) == pytest.approx((50, 60))

    def test_flat_neighbours(self):
        """Flat top: the next column is 1.5 radius away, the next row sqrt(3)."""
        grid = HexGrid("flat", 0, 0, 40)
        assert grid.nearest_center(60, 34.64) == pytest.approx((60, 40 * math.sqrt(3) / 2))
        assert grid.nearest_center(0, 69) == pytest.approx((0, 40 * math.sqrt(3)))

    def test_pointy_neighbours(self):
        """Pointed top: the next column is sqrt(3) radius away, the next row 1.5."""
        grid = HexGrid("pointy", 0, 0, 40)
        assert grid.nearest_center(69, 0) == pytest.approx((40 * math.sqrt(3), 0))
        assert grid.nearest_center(34.64, 60) == pytest.approx((40 * math.sqrt(3) / 2, 60))

    def test_size_y_stretches_the_grid(self):
        grid = HexGrid("flat", 0, 0, 40, size_y=50)
        assert grid.nearest_center(0, 85) == pytest.approx((0, 50 * math.sqrt(3)))

    def test_snap_radius_limits_the_snap(self):
        grid = HexGrid("flat", 0, 0, 40, snap_radius=10)
        assert grid.snap(5, 5) == pytest.approx((0, 0))
        assert grid.snap(20, 5) is None


class TestReadGrid:
    def test_a_well_written_grid_is_read(self):
        grid = HexGrid.from_json(dict(GRID, size_y=42, snap_radius=25))
        assert (grid.orientation, grid.origin_x, grid.origin_y) == ("flat", 50, 60)
        assert (grid.size, grid.size_y, grid.snap_radius) == (40, 42, 25)

    def test_type_and_orientation_have_defaults(self):
        grid = HexGrid.from_json({"origin_x": 0, "origin_y": 0, "size": 10})
        assert grid.orientation == "flat"
        assert grid.size_y == 10
        assert grid.snap_radius is None

    @pytest.mark.parametrize("raw", [
        None, [], "hex",
        dict(GRID, type="square"),
        dict(GRID, orientation="diagonal"),
        dict(GRID, size=0),
        dict(GRID, size=-3),
        dict(GRID, size="40"),
        dict(GRID, size=True),
        dict(GRID, size=float("nan")),
        dict(GRID, size_y=0),
        {k: v for k, v in GRID.items() if k != "origin_x"},
    ])
    def test_a_malformed_grid_is_no_grid(self, raw):
        """A game_json is written by hand: a wrong grid must not break the game."""
        assert HexGrid.from_json(raw) is None

    def test_a_null_radius_means_no_limit(self):
        assert HexGrid.from_json(dict(GRID, snap_radius=0)).snap_radius is None

    def test_the_grid_survives_its_json(self):
        raw = dict(GRID, size_y=42, snap_radius=25)
        assert HexGrid.from_json(HexGrid.from_json(raw).return_json()).return_json() == raw

    def test_optional_fields_are_not_written_when_absent(self):
        assert HexGrid.from_json(GRID).return_json() == GRID


class TestBoard:
    def test_a_board_without_grid_does_not_snap(self):
        board = Board("b", 0, 0, "/b.jpg", 100, 100)
        assert board.grid is None
        assert board.snap_point(50, 50) is None
        assert board.return_json()["grid"] is None

    def test_the_grid_is_relative_to_the_board(self):
        board = Board("b", 1000, 2000, "/b.jpg", 800, 600, grid=GRID)
        BoardGroup("g", [board], magnetism=True)
        assert board.snap_point(1052, 2057) == pytest.approx((1050, 2060))

    @pytest.mark.parametrize("magnetism", [False, None, "true", 1])
    def test_a_grid_without_magnetism_does_not_snap(self, magnetism):
        """The grid alone is only drawn: the game turns the snap on."""
        board = Board("b", 1000, 2000, "/b.jpg", 800, 600, grid=GRID)
        group = BoardGroup("g", [board], magnetism=magnetism)
        assert board.grid is not None
        assert group.magnetism is False
        assert board.snap_point(1052, 2057) is None

    def test_a_board_alone_does_not_snap(self):
        board = Board("b", 1000, 2000, "/b.jpg", 800, 600, grid=GRID)
        assert board.snap_point(1052, 2057) is None

    def test_the_saved_state_keeps_the_grid(self):
        session = build_session()
        saved = session.game_json_state()
        group = next(item for item in saved["fixed"] if item["id"] == "group")
        board = group["boards"][0]
        assert board["grid"] == GRID
        assert group["magnetism"] is True
        resumed = Session(
            user=None, name="Vietnam", key="KEY1", code="CODE1",
            active=True, variant="std", game_json=saved,
        )
        assert resumed.get_component("map").grid is not None
        assert resumed.get_component("map").group.magnetism is True
        assert resumed.get_component("track").grid is None


class TestRelease:
    def test_a_token_dropped_on_the_grid_is_centered_on_a_hex(self):
        # center dropped at (1053, 2064): hex (0, 0) is at (1050, 2060)
        token = release_at(1053 - 15, 2064 - 15)
        assert center(token) == pytest.approx((1050, 2060))

    def test_the_broadcast_carries_the_snapped_position(self):
        session = build_session()
        user = FakeUser(session)
        session.players = [user]
        session.get_component("token").acquire(user)
        release(FakeProtocol(user), LOGGER, {
            "action": "release", "component_id": "token", "x": 1040, "y": 2050,
        })
        sent = user.sent[-1]
        assert sent["event"] == "release" and sent["success"] is True
        assert (sent["component_json"]["x"], sent["component_json"]["y"]) == pytest.approx((1035, 2045))

    def test_a_board_without_magnetism_lets_the_token_where_dropped(self):
        token = release_at(1053 - 15, 2064 - 15, magnetism=False)
        assert (token.x, token.y) == (1053 - 15, 2064 - 15)

    def test_a_token_dropped_off_the_grid_board_stays_put(self):
        token = release_at(200, 300)
        assert (token.x, token.y) == (200, 300)

    def test_a_token_dropped_outside_any_board_stays_put(self):
        token = release_at(3000, 3000)
        assert (token.x, token.y) == (3000, 3000)

    def test_the_starting_square_wins_over_the_grid(self):
        """
        A token released near its starting square goes back there and gets its
        green rectangle back, even when that square is not a hex center.
        """
        token = release_at(1110 + 5, 2110 - 5, x=1110, y=2110)
        assert (token.x, token.y) == (1110, 2110)
        assert token.in_place is True

    def test_a_token_away_from_its_square_loses_the_rectangle(self):
        token = release_at(1300, 2300, x=1110, y=2110)
        assert token.in_place is False
        cx, cy = center(token)
        assert HexGrid.from_json(GRID).nearest_center(cx - 1000, cy - 2000) == pytest.approx((cx - 1000, cy - 2000))

    def test_the_snap_radius_is_respected(self):
        board = dict(BOARD_JSON, grid=dict(GRID, snap_radius=5))
        token = release_at(1053 - 15 + 20, 2064 - 15, board_json=board)
        assert (token.x, token.y) == (1053 - 15 + 20, 2064 - 15)

    def test_the_topmost_board_decides(self):
        """A board drawn over the map, without grid, hides the map's grid."""
        session = build_session()
        session.components_lists["fixed"].append(Board("cover", 1000, 2000, "/c.jpg", 200, 200))
        token = session.get_component("token")
        assert session.snap_to_grid(token, 1040, 2050) == (1040, 2050)

    @pytest.mark.parametrize("x,y", [("1040", 2050), (None, 2050), (True, 2050)])
    def test_a_position_that_is_not_a_number_is_not_snapped(self, x, y):
        session = build_session()
        token = session.get_component("token")
        assert session.snap_to_grid(token, x, y) == (x, y)

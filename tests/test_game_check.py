"""game_check.py: what keeps a game_json from being played, told before it is stored."""
import json
import logging

import pytest

from game_check import check_game_json

BOARD = {"kind": "board", "id": "map", "x": 0, "y": 0, "width": 100, "height": 100, "src": "/Games/x/map.jpg"}
TOKEN = {"kind": "token", "id": "t1", "x": 1, "y": 1, "width": 10, "height": 10, "front_src": "/Games/x/t.png",
         "back_src": "/Games/x/b.png"}


def test_a_playable_game_has_no_problem():
    assert check_game_json({"fixed": [BOARD], "movable": [TOKEN]}) == []


def test_not_an_object():
    assert check_game_json([1]) == ["the game_json is not a JSON object"]


@pytest.mark.parametrize("game_json, expected", [
    ({"fixed": [{**BOARD, "kind": "bord"}]}, "kind 'bord', unknown here"),
    ({"fixed": [{"id": "x"}]}, "kind None"),
    ({"movable": [{**TOKEN, "kind": "bag"}]}, "only in fixed"),
    ({"fixed": [TOKEN]}, "only in movable"),
    ({"fixed": [{"kind": "bag", "id": "b", "x": 0, "y": 0, "width": 1, "height": 1,
                 "components": [{**TOKEN, "kind": "card"}]}]}, "a bag only holds token"),
    ({"fixed": "board"}, '"fixed" is not a list'),
])
def test_a_component_a_session_would_ignore(game_json, expected):
    problems = check_game_json(game_json)
    assert any(expected in problem for problem in problems), problems


def test_duplicated_ids():
    problems = check_game_json({"fixed": [BOARD], "movable": [{**TOKEN, "id": "map"}]})
    assert any("more than once: map" in problem for problem in problems), problems


def test_a_component_that_cannot_be_built_is_named():
    board = dict(BOARD)
    del board["width"]
    problems = check_game_json({"fixed": [board]})
    assert any("'map'" in problem and "width" in problem for problem in problems), problems

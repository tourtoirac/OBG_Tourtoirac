"""Exercise configured rotation through the real action and save/resume path."""
import pytest

from handle_message import rotate
from session import Session
from tests.test_bag import build_session, player_on, call, error_codes


def make_session(step=60, kind="token"):
    piece = {
        "kind": kind, "id": "ship", "x": 500, "y": 500,
        "width": 40, "height": 40, "front_src": "/ship.png", "back_src": None,
        "orientable": True, "rotation_step": step,
    }
    return build_session(movable=[piece])


@pytest.mark.parametrize("kind", ["token", "card"])
def test_six_clicks_make_a_full_turn_and_survive_resume(kind):
    session = make_session(kind=kind)
    protocol, user = player_on(session)
    angles = []
    for _ in range(6):
        call(rotate, protocol, "ship", direction="right")
        angles.append(user.sent[-1]["orientation"])
    assert error_codes(protocol) == []
    assert angles == [60, 120, 180, 240, 300, 0]
    saved = session.game_json_state()
    resumed = Session(user=None, name="Aboukir", key="K", code="C", active=True,
                      variant="aboukir", game_json=saved)
    assert resumed.get_component("ship").rotation_step == 60
    protocol, user = player_on(resumed)
    resumed.started = True
    call(rotate, protocol, "ship", direction="left")
    assert user.sent[-1]["orientation"] == 300


@pytest.mark.parametrize("step", [None, True, "60", 0, -60, 7, 361, 60.0])
def test_invalid_steps_fall_back_to_45(step):
    session = make_session(step)
    protocol, user = player_on(session)
    call(rotate, protocol, "ship", direction="right")
    assert user.sent[-1]["orientation"] == 45

"""
Checks a game_json before it is stored: Chabanas asks it of a prototype whose
developer uploads a game_json (POST /admin/check, admin_api.py).

A session ignores without a word a component of an unknown kind, or of a kind
its list does not take (a bag among "movable"): here it is a problem. Then the
game is built as a session would be, so that every error the build would hit
at the creation of a session is told now.
"""
import copy

from session import (
    InvalidComponentError,
    Session,
    duplicate_component_ids,
    expand_components,
)

# the kinds Session.load_session_components builds, by list of the game_json
LIST_KINDS = {
    "fixed": {
        "board", "board_group", "counter", "dice", "dice_pool", "bag", "generator",
        "deck", "stack", "slot", "mw_character", "secret_choice",
    },
    "movable": {"board", "board_group", "counter", "dice", "token", "card"},
    "dice": {"board", "board_group", "counter", "dice"},
}
# what the components holding others take: anything else is ignored too
CONTENT_KINDS = {"bag": ("components", {"token"})}


def _name(component) -> str:
    component_id = component.get("id") if isinstance(component, dict) else None
    return f"'{component_id}'" if component_id is not None else "without id"


def misplaced_kinds(game_json: dict) -> list:
    """The components a session would ignore: kind missing, unknown, or not taken by their list."""
    problems = []
    for list_name, kinds in LIST_KINDS.items():
        components = game_json.get(list_name, [])
        if not isinstance(components, list):
            problems.append(f'"{list_name}" is not a list')
            continue
        for component in expand_components(components):
            if not isinstance(component, dict):
                problems.append(f'list "{list_name}": a component is not a JSON object')
                continue
            kind = component.get("kind")
            if kind not in kinds:
                taken = sorted(name for name, accepted in LIST_KINDS.items() if kind in accepted)
                where = f" (only in {', '.join(taken)})" if taken else ""
                problems.append(f'list "{list_name}": component {_name(component)} has kind {kind!r}, '
                                f'unknown here{where}')
                continue
            field, accepted = CONTENT_KINDS.get(kind, (None, None))
            content = component.get(field) if field else None
            for item in expand_components(content) if isinstance(content, list) else []:
                if not isinstance(item, dict) or item.get("kind") not in accepted:
                    kind_found = item.get("kind") if isinstance(item, dict) else None
                    problems.append(f"{kind} {_name(component)}: component {_name(item)} has kind "
                                    f"{kind_found!r}, a {kind} only holds {', '.join(sorted(accepted))}")
    return problems


def check_game_json(game_json) -> list:
    """
    Everything that keeps a game_json from being played as it is written.
    :return: the problems, in English like the errors of the engine; empty when
        the game can be played
    """
    if not isinstance(game_json, dict):
        return ["the game_json is not a JSON object"]
    problems = misplaced_kinds(game_json)
    duplicates = duplicate_component_ids(game_json)
    if duplicates:
        problems.append(f"component ids declared more than once: {', '.join(map(str, duplicates))}")
    # what Chabanas adds to the game_json of a session: the seats, and the options
    # of the game (sent by Chabanas with the game_json it checks)
    built = copy.deepcopy(game_json)
    built.setdefault("game", {"max_players": 2, "max_watchers": 10})
    built.setdefault("options", {})
    try:
        Session(user=None, name="check", key="check", code="check", active=True, variant="default",
                game_json=built)
    except InvalidComponentError as error:
        problems.append(error.describe())
    except Exception as error:  # noqa: BLE001 - any failure of the build is a problem of the game
        problems.append(f"the game cannot be built: {type(error).__name__}: {error}")
    return problems

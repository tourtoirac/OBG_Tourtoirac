"""
Tests of the randomizer, a setup entry laying components drawn at random on a
list of targets. Its source is a container (bag, deck, stack) or a list of
component ids. The draw is made once, when the session is built; applying the
setup takes the drawn components out of their container.
"""
import json

from session import Session, anonymize_ids, read_setup

BOARD = {"kind": "board", "id": "board", "x": 0, "y": 0, "src": "/map.jpg", "width": 2000, "height": 2000}


def token_json(name, **fields):
    return {"kind": "token", "id": name, "x": 10, "y": 10, "width": 40, "height": 40,
            "front_src": f"/{name}.png", "back_src": f"/{name}_back.png", **fields}


def card_json(name):
    return {"kind": "card", "id": name, "front_src": f"/{name}.png", "back_src": "/back.png"}


CARDS = [card_json(f"card-{index}") for index in range(5)]
DECK = {"kind": "deck", "id": "deck", "x": 100, "y": 100, "width": 60, "height": 90, "components": CARDS}
BAG = {"kind": "bag", "id": "bag", "x": 400, "y": 100, "width": 80, "height": 80,
       "components": [token_json(f"chit-{index}", x=None, y=None) for index in range(4)]}
TOKENS = [token_json(f"unit-{index}") for index in range(4)]

TARGETS = [
    {"x": 1000, "y": 1000, "side": "back", "orientation": 0},
    {"x": 1100, "y": 1000, "side": "front", "orientation": 90},
    {"x": 1200, "y": 1000},
]


def randomizer(source_kind, source_id, targets=TARGETS):
    return {"kind": "randomizer", "source_kind": source_kind, "source_id": source_id, "targets": targets}


def build_session(setup, fixed=None, movable=None, options=None):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [BOARD, DECK, BAG] if fixed is None else fixed,
        "movable": TOKENS if movable is None else movable,
        "options": options or {},
        "setup": setup,
    }
    return Session(
        user=None, name="Game", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json, started=True,
    )


def placed(session):
    """The components the setup lays, by id: their target."""
    return {entry["component_id"]: entry for entry in session.setup}


class TestReading:
    def test_a_randomizer_is_kept_cleaned(self):
        entries = read_setup([randomizer("deck", "deck", [{"x": "1", "y": 2}, {"x": 5, "y": 6.7, "side": "up",
                                                                               "orientation": True}, "x"])])
        assert entries == [{"kind": "randomizer", "source_kind": "deck", "source_id": "deck",
                            "targets": [{"x": 5, "y": 6}]}]

    def test_an_unusable_randomizer_is_ignored(self):
        for entry in (
            randomizer("box", "deck"),
            randomizer("deck", ["deck"]),
            randomizer("id_list", "unit-0"),
            randomizer("deck", "deck", []),
            randomizer("deck", "deck", [{"x": 1}]),
        ):
            assert read_setup([entry]) == []

    def test_an_id_list_keeps_its_text_ids(self):
        entries = read_setup([randomizer("id_list", ["a", 3, "", None, "b"])])
        assert entries[0]["source_id"] == ["a", "b"]


class TestDrawing:
    def test_a_deck_gives_one_card_for_each_target(self):
        session = build_session([randomizer("deck", "deck")])
        chosen = placed(session)
        assert len(chosen) == 3
        assert set(chosen) <= {card["id"] for card in CARDS}
        assert [(entry["x"], entry["y"]) for entry in session.setup] == [(1000, 1000), (1100, 1000), (1200, 1000)]

    def test_the_draw_is_random(self):
        draws = {tuple(placed(build_session([randomizer("deck", "deck")]))) for _ in range(60)}
        assert len(draws) > 1

    def test_a_short_source_fills_the_first_targets(self):
        session = build_session([randomizer("id_list", ["unit-0", "unit-1"])])
        assert sorted(placed(session)) == ["unit-0", "unit-1"]
        assert [entry["x"] for entry in session.setup] == [1000, 1100]

    def test_unknown_ids_and_other_kinds_are_ignored(self):
        session = build_session([randomizer("id_list", ["nope", "board", "deck", "unit-2", "unit-2"])])
        assert list(placed(session)) == ["unit-2"]

    def test_a_container_of_another_kind_gives_nothing(self):
        assert build_session([randomizer("bag", "deck")]).setup == []
        assert build_session([randomizer("stack", "deck")]).setup == []

    def test_a_component_is_drawn_once_across_randomizers(self):
        ids = [token["id"] for token in TOKENS]
        session = build_session([randomizer("id_list", ids[:3]), randomizer("id_list", ids[:3])])
        assert len(placed(session)) == 3

    def test_plain_entries_keep_their_place(self):
        session = build_session([
            {"component_id": "unit-0", "x": 5, "y": 5},
            randomizer("deck", "deck", TARGETS[:1]),
            {"component_id": "unit-1", "x": 6, "y": 6},
        ])
        assert [entry["component_id"] for entry in session.setup][::2] == ["unit-0", "unit-1"]


class TestApplying:
    def test_cards_leave_the_deck_onto_their_target(self):
        session = build_session([randomizer("deck", "deck")])
        chosen = placed(session)
        components = session.apply_setup()
        deck = session.get_component("deck")
        assert len(deck.piles["draw"]) == 2
        for card_id, target in chosen.items():
            card = session.get_component(card_id)
            assert card in session.components_lists["movable"]
            assert (card.x, card.y) == (target["x"], target["y"])
            assert (card.initial_x, card.initial_y) == (target["x"], target["y"])
        # each screen reads the deck again with the cards
        by_id = {state["id"]: state for state in components}
        assert by_id["deck"]["piles"]["draw"]["count"] == 2
        assert set(chosen) <= set(by_id)

    def test_side_and_orientation_are_those_of_the_target(self):
        session = build_session([randomizer("id_list", ["unit-0", "unit-1", "unit-2"])])
        session.apply_setup()
        for entry in session.setup:
            token = session.get_component(entry["component_id"])
            assert token.side == entry.get("side", "front")
            assert token.orientation == entry.get("orientation", 0)

    def test_tokens_leave_the_bag(self):
        session = build_session([randomizer("bag", "bag", TARGETS[:2])])
        components = session.apply_setup()
        bag = session.get_component("bag")
        assert len(bag.components) == 2
        assert all(session.bag_holding(session.get_component(name)) is None for name in placed(session))
        assert len(next(state for state in components if state["id"] == "bag")["components"]) == 2

    def test_a_stack_gives_its_cards(self):
        stack = dict(DECK, kind="stack", id="pack", width=200, height=300)
        session = build_session([randomizer("stack", "pack", TARGETS[:2])], fixed=[BOARD, stack], movable=[])
        session.apply_setup()
        assert len(session.get_component("pack").piles["draw"]) == 3

    def test_applying_twice_changes_nothing(self):
        session = build_session([randomizer("deck", "deck")])
        session.apply_setup()
        movable = list(session.components_lists["movable"])
        session.apply_setup()
        assert session.components_lists["movable"] == movable
        assert len(session.get_component("deck").piles["draw"]) == 2

    def test_the_saved_state_keeps_the_cards_on_the_table(self):
        session = build_session([randomizer("deck", "deck")])
        chosen = placed(session)
        session.apply_setup()
        state = json.loads(json.dumps(session.game_json_state()))
        assert "setup" not in state
        resumed = Session(user=None, name="Game", key="KEY1", code="CODE1",
                          active=True, variant="std", game_json=state, started=True)
        assert {component.id for component in resumed.components_lists["movable"]} >= set(chosen)
        assert len(resumed.get_component("deck").piles["draw"]) == 2

    def test_a_setup_saved_before_being_applied_draws_again(self):
        session = build_session([randomizer("deck", "deck")])
        state = json.loads(json.dumps(session.game_json_state()))
        resumed = Session(user=None, name="Game", key="KEY1", code="CODE1",
                          active=True, variant="std", game_json=state, started=True)
        assert len(placed(resumed)) == 3
        assert len(resumed.get_component("deck").piles["draw"]) == 5


class TestAnonymousIds:
    def test_an_id_list_follows_the_new_ids(self):
        game_json = {
            "options": {"anonymous_id": True},
            "fixed": [BOARD],
            "movable": TOKENS,
            "setup": [randomizer("id_list", ["unit-0", "unit-1", "board"])],
        }
        result = anonymize_ids(game_json)
        new_ids = [token["id"] for token in result["movable"][:2]]
        assert result["setup"][0]["source_id"] == [*new_ids, "board"]


class TestContainerTargets:
    """A target may name a deck or a stack: the card drawn goes on one of its
    piles, as if it were released on it."""

    DUO = {"kind": "deck", "id": "duo", "type": "duo", "x": 600, "y": 100, "width": 60, "height": 90,
           "orientation": 90, "components": []}
    TABLE_CARDS = [dict(card_json(f"table-{index}"), x=10, y=10, width=60, height=90, side="front",
                        orientation=45, orientable=True) for index in range(3)]

    def session(self, targets, source_kind="id_list", source_id=None, movable=None):
        source_id = source_id if source_id is not None else [card["id"] for card in self.TABLE_CARDS]
        return build_session([randomizer(source_kind, source_id, targets)],
                             fixed=[BOARD, DECK, BAG, self.DUO],
                             movable=self.TABLE_CARDS if movable is None else movable)

    def test_a_container_target_is_read(self):
        entries = read_setup([randomizer("deck", "deck", [
            {"container_id": "duo", "pile": "discard"}, {"container_id": "duo", "pile": "top"},
            {"container_id": ""}, {"x": 1, "y": 2},
        ])])
        assert entries[0]["targets"] == [{"container_id": "duo", "pile": "discard"}, {"container_id": "duo"},
                                         {"x": 1, "y": 2}]

    def test_cards_of_the_table_go_on_the_deck_like_a_release(self):
        session = self.session([{"container_id": "duo"}, {"container_id": "duo", "pile": "discard"}])
        chosen = placed(session)
        assert [entry["pile"] for entry in session.setup] == ["draw", "discard"]
        components = session.apply_setup()
        duo = session.get_component("duo")
        draw_card, discard_card = (session.get_component(entry["component_id"]) for entry in session.setup)
        assert duo.piles["draw"] == [draw_card] and duo.piles["discard"] == [discard_card]
        # face down in the draw pile, face up on the discard, turned like the deck
        assert (draw_card.side, discard_card.side) == ("back", "front")
        assert draw_card.orientation == discard_card.orientation == 90
        assert all(session.get_component(name) not in session.components_lists["movable"] for name in chosen)
        by_id = {state["id"]: state for state in components}
        assert by_id[draw_card.id]["x"] is None
        assert by_id["duo"]["piles"]["draw"]["count"] == 1

    def test_cards_move_from_one_deck_to_another(self):
        session = self.session([{"container_id": "duo"}] * 2, source_kind="deck", source_id="deck")
        components = session.apply_setup()
        assert len(session.get_component("deck").piles["draw"]) == 3
        assert len(session.get_component("duo").piles["draw"]) == 2
        assert {"deck", "duo"} <= {state["id"] for state in components}

    def test_only_a_card_goes_in_a_deck(self):
        session = self.session([{"container_id": "duo"}, {"x": 5, "y": 5}],
                               source_id=["unit-0", "table-0"], movable=[*TOKENS, *self.TABLE_CARDS])
        assert [(entry["component_id"], "container_id" in entry) for entry in session.setup] == [
            ("table-0", True), ("unit-0", False)]

    def test_an_unknown_container_or_pile_is_skipped(self):
        session = self.session([{"container_id": "nope"}, {"container_id": "bag"},
                                {"container_id": "deck", "pile": "discard"}, {"x": 5, "y": 5}])
        assert [("container_id" in entry) for entry in session.setup] == [False]

    def test_applying_twice_changes_nothing(self):
        session = self.session([{"container_id": "duo"}] * 3)
        session.apply_setup()
        pile = list(session.get_component("duo").piles["draw"])
        session.apply_setup()
        assert session.get_component("duo").piles["draw"] == pile

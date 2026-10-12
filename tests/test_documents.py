"""
Tests of the optional documents of a game: the game_json declares a list of
PDF files, Tourtoirac carries it to the clients and keeps it in the saved state.
"""
from session import Session, read_documents

RULES = {"src": "/Games/Vietnam/rules.pdf", "name": "Rules"}
CHARTS = {"src": "/Games/Vietnam/charts.pdf", "name": "Charts"}


def build_session(**game_json):
    game_json = {
        "game": {"max_players": 2, "max_watchers": 2},
        "fixed": [],
        "movable": [],
        **game_json,
    }
    return Session(
        user=None, name="Vietnam", key="KEY1", code="CODE1",
        active=True, variant="std", game_json=game_json,
    )


class TestReading:
    def test_the_documents_of_the_game_are_read_in_order(self):
        assert read_documents({"documents": [RULES, CHARTS]}) == [RULES, CHARTS]

    def test_a_game_without_documents_has_none(self):
        assert read_documents({"fixed": []}) == []
        assert read_documents(None) == []
        assert build_session().return_session_json()["documents"] == []

    def test_a_badly_written_list_is_cleaned(self):
        declared = [RULES, "charts.pdf", {"name": "No file"}, {"src": ""}, {"src": "/a.pdf"}]
        assert read_documents({"documents": declared}) == [RULES, {"src": "/a.pdf", "name": "/a.pdf"}]
        assert read_documents({"documents": RULES}) == []

    def test_the_session_json_carries_the_documents(self):
        session = build_session(documents=[RULES, CHARTS])
        assert session.return_session_json()["documents"] == [RULES, CHARTS]

    def test_the_saved_state_keeps_the_documents(self):
        assert build_session(documents=[RULES]).game_json_state()["documents"] == [RULES]

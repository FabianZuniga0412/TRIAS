import json

from learning_history import GENERAL_PRACTICE, PRACTICE_CATALOG, LearningHistoryStore


def test_history_records_only_aggregate_focus_data_and_restores(tmp_path):
    path = tmp_path / "learning_history.json"
    store = LearningHistoryStore(path)
    store.load()
    store.record(123, "Verb tense")
    store.record(123, "Articles")
    store.record(123, "Verb tense")

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {
        "users": {
            "123": {
                "last_focus": "Verb tense",
                "focus_counts": {"Verb tense": 2, "Articles": 1},
                "practice_indexes": {},
            }
        }
    }
    assert "She don't like pizza" not in path.read_text(encoding="utf-8")

    restored = LearningHistoryStore(path)
    restored.load()
    assert restored.summary_for(123) == ("Verb tense", [("Verb tense", 2), ("Articles", 1)])


def test_practice_uses_last_focus_or_general_fallback(tmp_path):
    store = LearningHistoryStore(tmp_path / "history.json")
    assert store.practice_for(77) == (*GENERAL_PRACTICE[0], None)
    store.record(77, "Prepositions")
    phrase, note, focus = store.practice_for(77)
    assert (phrase, note) == PRACTICE_CATALOG["Prepositions"][0]
    assert focus == "Prepositions"


def test_practice_rotates_examples_for_the_same_focus(tmp_path):
    store = LearningHistoryStore(tmp_path / "history.json")
    store.record(77, "Subject-verb agreement")

    phrases = [store.practice_for(77)[0] for _ in range(4)]

    assert phrases[:3] == [example[0] for example in PRACTICE_CATALOG["Subject-verb agreement"]]
    assert phrases[3] == phrases[0]

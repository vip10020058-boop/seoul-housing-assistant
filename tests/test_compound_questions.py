import json
from pathlib import Path

def test_compound_question_dataset_is_valid():
    path = Path(__file__).parents[1] / "compound_questions.json"
    questions = json.loads(path.read_text(encoding="utf-8"))
    assert len(questions) == 15
    assert len({q["id"] for q in questions}) == 15
    for q in questions:
        assert q["question"].strip()
        assert len(q["gold_labels"]) >= 1
        assert len(q["search_queries"]) >= 1
        assert all(label.startswith("서울특별시 ") for label in q["gold_labels"])
        assert all(query.strip() for query in q["search_queries"])

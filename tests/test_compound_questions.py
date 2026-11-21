import json
from pathlib import Path

import rag


def test_compound_public_samples_retrieve_each_required_clause():
    path = Path(__file__).parents[1] / "sample_questions.json"
    questions = [item for item in json.loads(path.read_text(encoding="utf-8"))
                 if item["type"] == "여러 조항 결합"]
    assert len(questions) >= 3
    for item in questions:
        labels = {hit["label"] for hit in rag._merged_retrieve(item["question"])}
        assert item["question"].strip()
        assert len(item["gold"]) >= 2
        assert all(labels.intersection(alternatives) for alternatives in item["gold"])

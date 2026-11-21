"""Source-grounded fee/annex citation regression tests (no API key)."""
import json
import pytest

import check
import config
import rag


@pytest.fixture
def m3_case():
    samples = json.loads((config.ROOT / "sample_questions.json").read_text(encoding="utf-8"))
    sample = next(q for q in samples if q["id"] == "M3")
    return sample, rag._merged_retrieve(sample["question"])


def test_fee_claim_with_missing_table_citation_is_repaired(m3_case):
    sample, hits = m3_case
    wrong = [h for h in hits if "제8조" in h["label"]][:2]
    assert wrong
    answer = "원칙적으로 자전거주차장은 무료입니다. 다만 위탁 등 특별한 사유가 있는 경우 1시간당 200원, 1일 1,000원, 월 15,000원을 별표 범위에서 정하여 부과할 수 있습니다."
    fixed = rag._reconcile_free_fee_evidence(sample["question"], answer, wrong, hits)
    assert [h["label"] for h in fixed] == [
        "서울특별시 자전거이용 활성화에 관한 조례 제9조 제1항",
        "서울특별시 자전거이용 활성화에 관한 조례 별표",
    ]
    assert check.score_citation([h["label"] for h in fixed], sample["gold"]) == 1.0


def test_fee_claim_with_missing_principle_citation_is_repaired(m3_case):
    sample, hits = m3_case
    selected = [next(h for h in hits if h["label"].endswith("별표")),
                next(h for h in hits if "제8조" in h["label"])]
    answer = "자전거 주차요금은 무료이며, 위탁 시 1시간 200원, 1일 1,000원, 월 정기권 15,000원 범위에서 부과 가능합니다."
    fixed = rag._reconcile_free_fee_evidence(sample["question"], answer, selected, hits)
    assert check.score_citation([h["label"] for h in fixed], sample["gold"]) == 1.0


def test_fee_repair_refuses_invented_amount(m3_case):
    sample, hits = m3_case
    selected = [next(h for h in hits if "제8조" in h["label"])]
    answer = "원칙적으로 무료지만 유료인 경우 월 99,999원입니다."
    assert rag._reconcile_free_fee_evidence(sample["question"], answer, selected, hits) == selected


def test_fee_repair_is_not_triggered_by_other_sample_questions():
    qs=json.loads((config.ROOT / "sample_questions.json").read_text(encoding="utf-8"))
    for q in qs:
        if q["id"] == "M3" or not q["gold"]:
            continue
        hits=rag._merged_retrieve(q["question"])
        a="이용요금은 500원입니다" if q["id"] == "S1" else "네, 가능합니다"
        selected=hits[:2]
        assert rag._reconcile_free_fee_evidence(q["question"], a, selected, hits) == selected, q["id"]


def test_m3_full_answer_api_citation_after_model_selected_wrong_evidence(monkeypatch, m3_case):
    import upstage
    sample, hits = m3_case
    # Deliberately return management provisions and unrelated annex instead of the right pair.
    indices = [i + 1 for i, h in enumerate(hits) if "제8조" in h["label"]][:2]
    indices.append(next(i + 1 for i, h in enumerate(hits) if h["label"].endswith("별표")))
    answer = "자전거주차장은 원칙적으로 무료입니다. 민간단체 위탁 등 특별한 사유가 있으면 1시간 200원, 1일 1,000원, 월 15,000원까지 별표 범위에서 정하여 받을 수 있습니다."
    monkeypatch.setattr(rag, '_merged_retrieve', lambda _: hits)
    monkeypatch.setattr(config, 'USE_VERIFY', True)
    monkeypatch.setattr(upstage, 'chat', lambda *a, **kw: json.dumps({
        'found': True, 'answer': answer, 'evidence_ids': indices}, ensure_ascii=False))
    contexts=[]
    monkeypatch.setattr(upstage, 'groundedness', lambda ctx, ans: contexts.append(ctx) or 'grounded')
    response=rag.answer(sample['question'])
    assert response['citation'] == [
        "서울특별시 자전거이용 활성화에 관한 조례 제9조 제1항",
        "서울특별시 자전거이용 활성화에 관한 조례 별표",
    ]
    assert check.score_citation(response['citation'], sample['gold']) == 1.0
    assert '별표' in contexts[0] and '제9조 제1항' in contexts[0]
    assert set(response) == {'answer', 'citation', 'source_url', 'effective_date'}

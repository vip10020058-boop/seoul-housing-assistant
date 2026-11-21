"""Offline, reproducible regression suite for grading correctness and UI isolation."""
import json
from datetime import date

import pytest

import app
import build_index
import check
import config
import rag


def test_offline_answer_evidence_recall_public_nine():
    qs=json.loads((config.ROOT/'sample_questions.json').read_text(encoding='utf-8'))
    for q in qs:
        if not q['gold']:
            continue
        labels={h['label'] for h in rag._merged_retrieve(q['question'])}
        for alternatives in q['gold']:
            assert any(a in labels for a in alternatives), (q['id'], alternatives)


def test_index_table_splitting_preserves_row_and_headers():
    raw='요금표\n| 급지 | 요금 |\n| --- | --- |\n'+'\n'.join(f'| {i}급지 | {i*100:,}원 |' for i in range(1,18))
    chunks=build_index.split_annex_rows(raw,limit=85)
    assert len(chunks)>2
    for i in range(1,18):
        assert sum(f'| {i}급지 | {i*100:,}원 |' in c for c in chunks)==1
    assert all('| 급지 | 요금 |' in c for c in chunks[1:] if '급지 |' in c)


def test_index_long_row_never_splits_mid_cell():
    line='| 시설 A | '+'아'*250+' | 300원 |'
    assert line in '\n'.join(build_index.split_annex_rows('표\n| 구분 | 요금 |\n|---|---|\n'+line,limit=50))


def test_overage_penalty_and_retrieval_exemption():
    gold=[['서울특별시 주차장 설치 및 관리 조례 제7조 제1항']]
    preds=gold[0]+['서울특별시 주차장 설치 및 관리 조례 제1조',
                  '서울특별시 주차장 설치 및 관리 조례 제2조',
                  '서울특별시 주차장 설치 및 관리 조례 제3조']
    assert check.score_citation(preds,gold)==pytest.approx(.75)
    assert check.score_citation(preds,gold,apply_overage=False)==pytest.approx(1)


def test_unknown_json_shape_and_overcite_warning():
    assert check.validate_response(dict(rag.UNKNOWN))==[]
    r={**rag.UNKNOWN,'answer':'네, 가능','citation':['x','y','z','w']}
    problems=check.validate_response(r)
    assert 'citation_over_three' in problems
    assert 'missing_or_unofficial_source_link' in problems


def test_unsupported_groundedness_is_not_overridden_by_similarity(monkeypatch):
    import upstage
    hit={'id':1,'label':'서울특별시 금연환경 조성 및 간접흡연 피해방지조례 제10조 제1항',
         'text':'과태료 10만원','law':'서울특별시 금연환경 조성 및 간접흡연 피해방지조례',
         'source_url':'https://www.law.go.kr/ordinInfoP.do?ordinSeq=2171803','effective_date':'2026-10-01',
         'score':.91,'cos':.95}
    monkeypatch.setattr(rag,'_merged_retrieve',lambda q:[hit])
    monkeypatch.setattr(config,'USE_VERIFY',True)
    monkeypatch.setattr(upstage,'chat',lambda *a,**kw: json.dumps({'found':True,'answer':'과태료 50만원','evidence_ids':[1]}))
    monkeypatch.setattr(upstage,'groundedness',lambda ctx,ans:'notGrounded')
    assert rag.answer('과태료?')==rag.UNKNOWN


def test_output_citation_limit(monkeypatch):
    import upstage
    hits = rag._merged_retrieve("서울시 공영주차장 이용 요건은?")[:5]
    monkeypatch.setattr(rag,'_merged_retrieve',lambda q:hits)
    monkeypatch.setattr(config,'USE_VERIFY',False)
    monkeypatch.setattr(upstage,'chat',lambda *a,**kw:json.dumps({'found':True,'answer':'요건 확인','evidence_ids':[1,2,3,4,5]}))
    result=rag.answer('여러 조건이 있나요?')
    assert len(result['citation'])==3
    assert set(result)=={'answer','citation','source_url','effective_date'}


def test_borough_out_of_scope():
    assert rag._out_of_scope('서울특별시 강남구 부설주차장 관리 규칙 요금은?')
    assert rag._out_of_scope('중구의 주차장 관리 조례 요금은?')
    assert rag._out_of_scope('부산광역시 금연 조례는?')
    assert not rag._out_of_scope('서울시 주차장 조례에서 구청장이 해야 하는 일은?')
    assert not rag._out_of_scope('마포구 주민도 서울특별시 공영주차장 조례 적용을 받나요?')


def test_pinned_corpus_contains_exactly_six_ordinances_and_real_effective_dates():
    import fetch_ordinances

    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    chunks, *_ = rag._load()
    assert {item["name"] for item in laws} == set(config.TARGET_ORDINANCES)
    assert {chunk["law"] for chunk in chunks} == set(config.TARGET_ORDINANCES)
    assert all(rag.valid_citation_source(chunk) for chunk in chunks)
    for law in laws:
        raw_path = config.DATA / "raw" / f"{law['mst']}.xml"
        parsed = fetch_ordinances.parse_xml(raw_path.read_bytes(), law["name"], law["mst"])
        assert parsed["effective_date"] == law["effective_date"]
        assert date.fromisoformat(law["effective_date"]) <= date(2026, 10, 1)
        assert parsed["source_url"] == law["source_url"]


def test_http_ask_contract_on_missing_and_invalid_requests(monkeypatch):
    from fastapi.testclient import TestClient
    with TestClient(app.app) as client:
        for payload in ({'wrong':'field'}, {'question':''},{'question':'a'*2001}, 'string'):
            r=client.post('/ask',json=payload)
            assert r.status_code==200
            assert r.json()==rag.UNKNOWN
        monkeypatch.setattr(app.rag,'answer',lambda q:{'answer':'확인 불가','citation':[],'source_url':'','effective_date':''})
        r=client.post('/ask',json={'question':'테스트'})
        assert r.status_code==200 and r.json()==rag.UNKNOWN


def test_citizen_answer_uses_separate_engine(monkeypatch):
    from fastapi.testclient import TestClient
    import housing_service

    monkeypatch.setattr(housing_service, "answer", lambda request: {"answer": "시민용"})
    monkeypatch.setattr(rag, "answer", lambda question: dict(rag.UNKNOWN))
    with TestClient(app.app) as client:
        assert client.post("/ui/housing/ask", json={"question": "월세 지원?"}).json() == {"answer": "시민용"}
        assert client.post("/ask", json={"question": "월세 지원?"}).json() == rag.UNKNOWN


def test_specific_penalty_must_share_legal_basis_with_question():
    items={q['id']:q for q in json.loads((config.ROOT/'sample_questions.json').read_text(encoding='utf-8'))}
    helmet=items['T1']['question']
    smoke=items['S2']['question']
    assert not rag._specific_penalty_has_basis(helmet,rag._merged_retrieve(helmet))
    assert rag._specific_penalty_has_basis(smoke,rag._merged_retrieve(smoke))


def test_answer_key_numeric_check_does_not_confuse_law_numbers():
    got=check.content_check('네, 10만원의 과태료입니다. 제10조 제1항에 따릅니다.',
                            '10만원 (제10조 제1항)')
    assert got['checked'] and not got['missing']
    missed=check.content_check('과태료 20만원', '10만원')
    assert ('원',100000.0) in missed['missing']
    rate=check.content_check('요금 50% 할인입니다.','100분의 50 감면')
    assert not rate['missing']


def test_delegate_specific_details_and_cite_the_seoul_clause(monkeypatch):
    import upstage

    question = "서울시 반려견 놀이터는 몇 시부터 몇 시까지 운영하나요?"
    clause = "서울특별시 동물보호 조례 제29조 제3항"
    hits = rag._merged_retrieve(question)
    hit_index = next(i for i, hit in enumerate(hits, 1) if hit["label"] == clause)
    answer = "조례에 운영 시간이 정해져 있지 않습니다. 시장이 운영에 필요한 사항을 정하므로 서울시 또는 해당 놀이터에서 확인하세요."
    monkeypatch.setattr(upstage, "chat", lambda *args, **kw: json.dumps({
        "found": True, "answer": answer, "evidence_ids": [hit_index],
    }))
    monkeypatch.setattr(upstage, "groundedness", lambda context, text: "grounded")
    response = rag.answer(question)
    assert response["citation"] == [clause]
    assert check.validate_response(response) == []

"""Offline, reproducible regression suite for grading correctness and UI isolation."""
import json
from pathlib import Path

import pytest

import app
import build_index
import check
import config
import rag
import session_state
import tool_agent


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
         'source_url':'https://www.law.go.kr/x','effective_date':'2026-10-01',
         'score':.91,'cos':.95}
    monkeypatch.setattr(rag,'_merged_retrieve',lambda q:[hit])
    monkeypatch.setattr(config,'USE_VERIFY',True)
    monkeypatch.setattr(upstage,'chat',lambda *a,**kw: json.dumps({'found':True,'answer':'과태료 50만원','evidence_ids':[1]}))
    monkeypatch.setattr(upstage,'groundedness',lambda ctx,ans:'notGrounded')
    assert rag.answer('과태료?')==rag.UNKNOWN


def test_output_citation_limit(monkeypatch):
    import upstage
    hits=[{'id':i,'label':f'서울특별시 예시 조례 제{i}조', 'text':f'요건 {i} ',
           'law':'서울특별시 예시 조례','score':.8,'cos':.9,
           'source_url':'https://www.law.go.kr/x','effective_date':'2026-10-01'} for i in range(1,6)]
    monkeypatch.setattr(rag,'_merged_retrieve',lambda q:hits)
    monkeypatch.setattr(config,'USE_VERIFY',False)
    monkeypatch.setattr(upstage,'chat',lambda *a,**kw:json.dumps({'found':True,'answer':'요건 확인','evidence_ids':[1,2,3,4,5]}))
    result=rag.answer('여러 조건이 있나요?')
    assert len(result['citation'])==3
    assert set(result)=={'answer','citation','source_url','effective_date'}


def test_borough_out_of_scope():
    assert rag._out_of_scope('서울특별시 강남구 부설주차장 관리 규칙 요금은?')
    assert not rag._out_of_scope('서울시 주차장 조례에서 구청장이 해야 하는 일은?')


def test_session_rewriting_is_ephemeral_and_context_scoped():
    sid,history=session_state.get_or_create()
    assert not history
    response={'answer':'50% 감면','citation':['서울특별시 주차장 설치 및 관리 조례 제7조 제1항']}
    session_state.record(sid,'주차 감면되나요?',response)
    other_sid,other_history=session_state.get_or_create()
    assert other_sid!=sid and other_history==[]
    _, prior=session_state.get_or_create(sid)
    q=session_state.rewrite_question('그럼 제3항은?',prior)
    assert '서울특별시 주차장 설치 및 관리 조례 제7조' in q
    assert '제3항' in q
    assert session_state.rewrite_question('서울시 자전거 주차요금 알려줘',prior)=='서울시 자전거 주차요금 알려줘'
    assert session_state.clear(sid)


def test_tool_local_search_and_disabled_official(monkeypatch):
    monkeypatch.setattr(config,'ENABLE_LIVE_LAW_TOOL',False)
    assert tool_agent.official_archived_check('서울특별시 청년 기본 조례')['status']=='disabled'
    hits=tool_agent.local_search('서울 청년 몇 살')
    assert hits and all('source_url' in h for h in hits)
    assert tool_agent.local_provision('없는 조례 제999조')==[]


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


def test_ui_session_rewrite_and_delete(monkeypatch):
    from fastapi.testclient import TestClient
    def fake_answer(q):
        return {'answer':'네, 감면됩니다.','citation':['서울특별시 주차장 설치 및 관리 조례 제7조 제1항'],
                'source_url':'https://www.law.go.kr/x','effective_date':'2026-10-01',
                'verification_status':'grounded', 'evidence':[], 'checklist':[],
                'searched_laws':[], 'follow_up_questions':[], 'needs_clarification':False,'clarification_options':[]}
    monkeypatch.setattr(app.ui_service,'answer',fake_answer)
    with TestClient(app.app) as client:
        a=client.post('/ui/session',json={'question':'주차요금 할인'}).json()
        b=client.post('/ui/session',json={'question':'그럼 제3항은?','session_id':a['session_id']}).json()
        assert '제7조' in b['rewritten_question']
        assert client.delete('/ui/session/'+a['session_id']).json()=={'cleared':True}


def test_specific_penalty_must_share_legal_basis_with_question():
    items={q['id']:q for q in json.loads((config.ROOT/'sample_questions.json').read_text(encoding='utf-8'))}
    helmet=items['T1']['question']
    smoke=items['S2']['question']
    assert not rag._specific_penalty_has_basis(helmet,rag._merged_retrieve(helmet))
    assert rag._specific_penalty_has_basis(smoke,rag._merged_retrieve(smoke))


def test_evidence_graph_only_uses_existing_target_clauses():
    import legal_graph
    citation='서울특별시 주차장 설치 및 관리 조례 제6조 제1항'
    graph=legal_graph.related([citation])
    assert any(e['relation']=='별표 참조' and e['to'].endswith('별표 1') for e in graph['edges'])
    assert all(n['id'].startswith('서울특별시 주차장 설치 및 관리 조례') for n in graph['nodes'])


def test_agent_fallback_does_not_need_api_key(monkeypatch):
    monkeypatch.setattr(config,'UPSTAGE_API_KEY','')
    response=tool_agent.run_tools('서울 청년 나이 몇 살?')
    assert response['mode']=='local_fallback'
    assert response['trace'] and response['trace'][0]['tool']=='search_ordinance'


def test_answer_key_numeric_check_does_not_confuse_law_numbers():
    got=check.content_check('네, 10만원의 과태료입니다. 제10조 제1항에 따릅니다.',
                            '10만원 (제10조 제1항)')
    assert got['checked'] and not got['missing']
    missed=check.content_check('과태료 20만원', '10만원')
    assert ('원',100000.0) in missed['missing']
    rate=check.content_check('요금 50% 할인입니다.','100분의 50 감면')
    assert not rate['missing']


def test_agent_model_tool_call_flow_offline_simulation(monkeypatch):
    # Simulate SDK tool calls; proves protocol and bounded selection, not provider support.
    from types import SimpleNamespace
    import upstage
    class ToolMessage:
        def __init__(self,tool_calls):self.tool_calls=tool_calls
        def model_dump(self,exclude_none=True):return {'role':'assistant','tool_calls':[{'id':x.id,'type':'function','function':{'name':x.function.name,'arguments':x.function.arguments}} for x in self.tool_calls]}
    calls=[SimpleNamespace(id='call1',function=SimpleNamespace(name='search_ordinance',arguments=json.dumps({'query':'청년 정의'})))]
    messages=[ToolMessage(calls),ToolMessage([])]
    def fake_create(**kwargs):return SimpleNamespace(choices=[SimpleNamespace(message=messages.pop(0))])
    monkeypatch.setattr(config,'UPSTAGE_API_KEY','fake-test-only')
    monkeypatch.setattr(upstage,'client',lambda:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))))
    monkeypatch.setattr(upstage,'_retry',lambda f,tries=2:f())
    result=tool_agent.run_tools('청년 정의는?')
    assert result['mode']=='model_tool_calling'
    assert len(result['trace'])==1
    assert result['trace'][0]['tool']=='search_ordinance'

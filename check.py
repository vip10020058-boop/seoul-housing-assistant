"""셀프 채점기: 공개 예시 문항으로 대회 채점 방식을 흉내 냅니다.

  근거 정확도  항까지 일치 1.0 · 조까지만 일치 0.5 · 별표 인용도 정답 인정
  함정 문항    answer가 "확인 불가"이고 citation이 []이면 정답
  형식         source_url · effective_date가 비어 있지 않은지, 90초 안에 응답했는지

실행:
  python -X utf8 check.py --retrieval      검색만 점검 (API 키 없이도 동작)
  python -X utf8 check.py                  로컬 함수로 전체 채점
  python -X utf8 check.py --url http://localhost:8000/ask   서버로 채점 (제출 전 최종 점검)

※ 실제 채점 규칙의 세부(인용을 여러 개 냈을 때의 처리 등)는 운영진 공지가 기준입니다.
"""
import argparse
import json
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import config

CITE = re.compile(
    r"(서울특별시.*?(?:조례|규칙))\s*(제\d+조(?:의\d+)?|별표(?:\s*\d+(?:의\d+)?)?|별도\s*\d+)\s*(제\d+항)?"
)


def parse(c):
    if not isinstance(c, str):
        return None
    m = CITE.search(c.replace(" ", " "))
    if not m:
        return None
    law = re.sub(r"\s+", "", m.group(1))
    art = re.sub(r"\s+", "", m.group(2))
    para = m.group(3)
    return law, art, para


def match(pred, gold):
    p, g = parse(pred), parse(gold)
    if not p or not g or p[0] != g[0] or p[1] != g[1]:
        return 0.0
    if g[2] is None or p[2] == g[2]:
        return 1.0
    return 0.5


def score_citation(preds, gold, apply_overage=True):
    """gold: [[대안1, 대안2], [다음 필수 근거], ...]"""
    if not gold:
        return None
    total = 0.0
    for alts in gold:
        total += max((match(p, a) for p in preds for a in alts), default=0.0)
    # Organizer's citation overage rule (gold evidence count + 2, Nov-19 feedback).
    # 1 gold clause + 4 predictions: one surplus -> 3/4 of the citation score.
    over = max(0, len(preds) - len(gold) - 2) if apply_overage else 0
    factor = 1 - over / len(preds) if preds and over else 1
    return total / len(gold) * factor



def validate_response(r):
    """Detailed four-field contract and citation overage hazard detection."""
    issues = []
    wanted = {"answer", "citation", "source_url", "effective_date"}
    if not isinstance(r, dict):
        return ["not_a_json_object"]
    if set(r) != wanted:
        issues.append("wrong_fields")
    answer = r.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        issues.append("invalid_answer")
    cites = r.get("citation")
    if not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
        issues.append("invalid_citation_type")
        cites = []
    if len(cites) > 3:
        issues.append("citation_over_three")
    if any(re.search(r"서울특별시\s*[가-힣]+구\s", c) for c in cites):
        issues.append("district_reference")
    allowed = {re.sub(r"\s+", "", name) for name in config.TARGET_ORDINANCES}
    if any((parsed := parse(c)) is None or parsed[0] not in allowed for c in cites):
        issues.append("invalid_citation_scope")
    url = r.get("source_url")
    effective_date = r.get("effective_date")
    unknown = answer == "확인 불가"
    if unknown:
        if cites:
            issues.append("unknown_has_citation")
        if url != "" or effective_date != "":
            issues.append("invalid_unknown_metadata")
    else:
        if not cites:
            issues.append("answer_without_citation")
        if not isinstance(url, str) or not re.fullmatch(
            r"https://www\.law\.go\.kr/ordinInfoP\.do\?ordinSeq=\d+", url
        ):
            issues.append("missing_or_unofficial_source_link")
        try:
            effective = datetime.strptime(effective_date, "%Y-%m-%d")
            if effective.strftime("%Y-%m-%d") != effective_date or effective.strftime("%Y%m%d") > config.BASE_DATE:
                raise ValueError
        except (TypeError, ValueError):
            issues.append("invalid_effective_date")
    return issues



_ARTICLE_NUMBER = re.compile(r"제\s*\d+조(?:의\d+)?|제\s*\d+항|제\s*\d+호|별표\s*\d+")
_NUMBER_UNIT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(억|만|천)?\s*(원|세|%|퍼센트|시간|개월|일|년|회)")
_PERCENT_FRACTION = re.compile(r"100\s*분의\s*(\d+(?:\.\d+)?)")


def relevant_numbers(text):
    """Comparable numeric facts (amounts, rates, ages, durations), not article IDs."""
    text=_ARTICLE_NUMBER.sub(' ',text)
    found=set()
    for m in _PERCENT_FRACTION.finditer(text):
        found.add(('%',round(float(m.group(1)),4)))
    text=_PERCENT_FRACTION.sub(' ',text)
    for m in _NUMBER_UNIT.finditer(text):
        amount=float(m.group(1).replace(',',''))
        mult={'억':100000000,'만':10000,'천':1000}.get(m.group(2),1)
        unit=m.group(3)
        found.add(('%' if unit=='퍼센트' else unit, round(amount*mult,4)))
    return found


def content_check(answer,answer_key):
    """Only report absent expected numerical facts; cannot judge all legal semantics."""
    expected=relevant_numbers(answer_key or '')
    observed=relevant_numbers(answer or '')
    return {'expected':sorted(expected),'observed':sorted(observed),
            'missing':sorted(expected-observed),'checked':bool(expected)}


def run_retrieval(qs):
    import rag
    print(f"[검색 점검] 단위={config.CHUNK_UNIT}  top_k={config.TOP_K}\n")
    hit_n = 0
    final_n = 0
    for q in qs:
        if not q["gold"]:
            hits = rag.retrieve(q["question"])
            print(f"{q['id']:3} 함정   top1={hits[0]['label']}  (score {hits[0]['score']:.2f})")
            continue
        labels = [h["label"] for h in rag.retrieve(q["question"])]
        sc = score_citation(labels, q["gold"], apply_overage=False)
        hit_n += sc
        f_labels=[h["label"] for h in rag._merged_retrieve(q["question"])]
        final_sc=score_citation(f_labels, q["gold"], apply_overage=False)
        final_n += final_sc
        mark = "✓" if sc == 1 else ("△" if sc > 0 else "✗")
        print(f"{q['id']:3} {mark} {sc:.2f}  {q['question'][:34]}")
        if sc < 1:
            print("      찾은 것:", labels[:3])
    n = sum(1 for q in qs if q["gold"])
    print(f"\n1차 검색 top-{config.TOP_K} 근거 평균 포함률: {hit_n / n:.0%}")
    print(f"최종 후보(연관 조항 포함) 근거 평균 포함률: {final_n / n:.0%}")
    print("※ 두 수치는 검색 후보 포함률이며 정답 생성·최종 채점 정확도가 아닙니다.")


def run_full(qs, url=None, parallel=2):
    """Run actual 2-in-flight grading-like requests and print any harmful failure."""
    if url:
        import requests

        def call(question):
            response = requests.post(url, json={"question": question}, timeout=90)
            response.raise_for_status()
            return response.json()
    else:
        import rag
        call=rag.answer
    def run_one(q):
        started=time.monotonic()
        try:
            response=call(q["question"])
            error=None
        except Exception as exc:
            response, error = {}, type(exc).__name__
        sec=time.monotonic()-started
        issues=validate_response(response)
        response = response if isinstance(response, dict) else {}
        answer_text = response.get("answer") if isinstance(response.get("answer"), str) else ""
        citations = response.get("citation") if isinstance(response.get("citation"), list) else []
        unknown = answer_text == "확인 불가" and citations == []
        fatal = {"not_a_json_object","wrong_fields","invalid_answer","invalid_citation_type",
                 "district_reference","invalid_citation_scope","unknown_has_citation",
                 "invalid_unknown_metadata","answer_without_citation",
                 "missing_or_unofficial_source_link","invalid_effective_date"}
        if error or sec>90 or fatal.intersection(issues):
            sc=0.0
        elif q["gold"]:
            sc=score_citation(citations, q["gold"])
        else:
            sc=1.0 if unknown else 0.0
        numbers=content_check(answer_text,q.get('answer_key','')) if q['gold'] else {'missing':[],'checked':False}
        if numbers['missing'] and 'missing_expected_numbers' not in issues:
            issues.append('missing_expected_numbers')
        if q["gold"] and len(citations)>len(q["gold"])+2:
            if "citation_overage_penalty" not in issues:
                issues.append("citation_overage_penalty")
        if sec>90:
            issues.append("over_90_seconds")
        if error:
            issues.append("request_error")
        return {"id":q["id"],"type":q.get("type", ""),"question":q["question"],
                "score":round(sc,4),"sec":round(sec,3),"format_ok":not error and not fatal.intersection(issues),
                "error":error,"issues":issues,"content_numbers":numbers,"response":response,"citation":citations,"gold":q["gold"],
                "answer_key":q.get("answer_key", "")}

    # One or two simultaneous calls matches the organizer's actual pre-check.
    results={}
    with ThreadPoolExecutor(max_workers=max(1,min(parallel,4))) as executor:
        futures={executor.submit(run_one,q):q["id"] for q in qs}
        for fut in as_completed(futures):
            row=fut.result()
            results[row["id"]]=row
    rows=[results[q["id"]] for q in qs]
    for r in rows:
        print(f"{r['id']:3} {r['score']:.2f} {r['sec']:6.2f}s "
              f"citation={len(r['citation'])} "
              f"{'FAIL:'+','.join(r['issues']) if r['issues'] else '형식OK'} "
              f"{str(r['response'].get('answer', ''))[:55]}")
        if r['score'] < 1 or r['issues']:
            actual = r["citation"]
            print('      실제 인용:', actual)
            if r['gold']:
                missing = [alts for alts in r['gold'] if max(
                    (match(pred, gold) for pred in actual for gold in alts), default=0.0
                ) < 1.0]
                if missing:
                    print('      필수 근거 미일치:', missing)
            if r['error']:
                print('      요청 오류:', r['error'])
    normal=[r["score"] for r in rows if r["gold"]]
    trap=[r["score"] for r in rows if not r["gold"]]
    print("\n인용점수 평균:",f"{sum(normal)/max(1,len(normal)):.3f}")
    print("함정 정답률:",f"{sum(trap)/max(1,len(trap)):.1%}")
    print("최대 응답시간:",round(max((r["sec"] for r in rows),default=0),2),"초")
    numeric_checked=[r for r in rows if r['content_numbers'].get('checked')]
    if numeric_checked:
        numeric_ok=sum(not r['content_numbers']['missing'] for r in numeric_checked)
        print("정답 요지 수치 포함:",f"{numeric_ok}/{len(numeric_checked)} (정답 내용의 충분조건 아님)")
    if rows and not any(r["score"] for r in rows):
        print("[CRITICAL] 전 문항 0점: 저장된 raw response 및 서버 로그를 대조하세요.")
    out=config.ROOT/"results"
    out.mkdir(exist_ok=True)
    path=out/f"check_{datetime.now():%m%d_%H%M%S}_{config.CHUNK_UNIT}_v{int(config.USE_VERIFY)}.json"
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    print("결과 저장:",path)
    failures=[r for r in rows if r["error"] or r["score"]<1 or r["issues"]]
    if failures:
        print(f"[FAIL] {len(failures)}/{len(rows)}; 실패 문항:", ", ".join(r["id"] for r in failures))
        raise SystemExit(1)
    print(f"[PASS] {len(rows)}개 문항의 채점·형식 검사 통과 (답 내용의 법률적 정답 보증 아님)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--offline", action="store_true", help="Use only local keyword retrieval")
    ap.add_argument("--questions", default=str(config.ROOT / "sample_questions.json"))
    ap.add_argument("--parallel", type=int, default=2,
                    help="Number of parallel grading calls (default 2, like organizer)")
    a = ap.parse_args()
    if a.offline and not a.retrieval:
        ap.error("--offline requires --retrieval")
    if a.offline:
        config.UPSTAGE_API_KEY = ""
    qs = json.loads(open(a.questions, encoding="utf-8").read())
    run_retrieval(qs) if a.retrieval else run_full(qs, a.url, parallel=a.parallel)

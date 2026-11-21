"""시민용 서울 청년월세지원 안내. 대회 채점 경로(rag.answer, /ask)와 분리됩니다.

LLM: 기존 Upstage Solar API. 법령: 법제처 OPEN API에서 조례/법률을 조회합니다.
사업 공고 수치는 서울시 2026 모집공고(공고 제2026-1440호) 스냅샷을 사용하고,
새 모집 일정은 공식 공고 페이지로 다시 확인하도록 안내합니다.
"""
import json
import logging
import re
import xml.etree.ElementTree as ET

import requests

import config
import upstage

log = logging.getLogger(__name__)
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
ORDINANCE_NAME = "서울특별시 청년주거 기본 조례"
NOTICE_URL = "https://soco.seoul.go.kr/bos/cmmn/file/fileDown.do?atchFileId=d964a2a457e3421c95f8b3de69846c99&bbsId=&fileSn=1&menuNo=400018"
PROGRAM_URL = "https://soco.seoul.go.kr/youth/bbs/BMSR00174/view.do?boardId=6734&menuNo=400043&pageIndex=1"

# 서울시 공고 제2026-1440호, 2026-04-30. 2026 회차는 2026-05-19 마감.
NOTICE_CONTEXT = """서울시 청년월세지원 2026년 모집공고(서울특별시 공고 제2026-1440호, 2026-04-30)
- 모집 기간: 2026-05-06 10:00 ~ 2026-05-19 18:00, 현재 마감.
- 신청 기준: 신청일 기준 서울시에 주민등록이 되어 있고 실제 거주하는 19~39세 청년(의무복무 제대군인은 최대 42세까지 연장 가능).
- 주택 기준: 임차보증금 8천만원 이하이면서 월세 60만원 이하. 월세가 60만원을 넘으면 보증금 월세 환산액(보증금×4.5%÷12)과 월세 합계가 90만원 이하인 경우 신청 가능. 천원 단위 절사.
- 소득 기준: 가구당 기준 중위소득 48% 초과~150% 이하. 2026 공고 기준 가구원 수별 금액을 확인해야 함.
- 재산 기준: 신청자 일반재산 총액 1억3천만원 이하, 자동차 차량시가표준액 2,500만원 미만.
- 제외 예: 주택 소유, 공공임대 거주, 기초생활수급, 서울시 청년월세지원 기수혜, 국토부/자치구 등 유사 사업 중복 수혜, 부모 또는 배우자인 임대인 등. 전체 제외사항 확인 필요.
- 지원: 실제 월세 범위에서 월 최대 20만원, 최대 12개월(생애 1회).
- 필수 서류: 확정일자 날인 임대차계약서 전체, 최근 3개월 월세 이체확인증, 본인 기준 가족관계증명서(상세), 행정정보 공동이용 사전동의서. 개인 식별번호 뒷자리는 삭제하고 제출.
- 신청은 서울주거포털 온라인 접수.
- 서울시 공식 안내 페이지(2026-11-12 게시)는 2026 모집 마감, 2027년 공고 예정이라고 안내하지만 구체 일정은 아직 공고 후 안내 상태. 2027 신청일이나 기준은 예측하지 말 것.
"""

INCOME_LIMITS = {
    1: (1_230_834, 3_846_357), 2: (2_015_660, 6_298_938),
    3: (2_572_337, 8_038_554), 4: (3_117_474, 9_742_107),
    5: (3_627_225, 11_335_079), 6: (4_106_857, 12_833_928),
    7: (4_567_272, 14_272_725),
}

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "housing_assistance",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "documents": {"type": "array", "items": {"type": "string"}},
                "draft": {"type": "string"},
                "caution": {"type": "string"},
            },
            "required": ["summary", "next_steps", "documents", "draft", "caution"],
            "additionalProperties": False,
        },
    },
}


def _number(value, maximum=2_000_000_000):
    if value in (None, ""):
        return None
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= maximum else None


def _find_exact_law(root, names):
    for law in root.findall("law"):
        title = (law.findtext("자치법규명") or law.findtext("법령명한글") or "").strip()
        agency = (law.findtext("지자체기관명") or "").strip()
        if title in names and (not agency or agency == config.TARGET_ORG):
            return {"title": title, "mst": law.findtext("자치법규일련번호") or "",
                    "id": law.findtext("자치법규ID") or "", "effective": law.findtext("시행일자") or "",
                    "url": f"https://www.law.go.kr/ordinInfoP.do?ordinSeq={law.findtext('자치법규일련번호') or ''}"}
    return None


def _law_search(target, query, exact_names=()):
    params = {"OC": config.LAW_OC or "test", "target": target, "type": "XML", "query": query,
              "display": 100 if target == "ordin" else 20, "search": 1}
    if target == "ordin":
        params["org"] = "6110000"  # 서울특별시
        params["nw"] = 1  # 현행 자치법규만 검색
    response = requests.get(SEARCH_URL, params=params, timeout=(4, 14))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    if exact_names:
        return _find_exact_law(root, exact_names)
    for law in root.findall("law"):
        title = (law.findtext("법령명한글") or law.findtext("자치법규명") or "").strip()
        if query in title or title == query:
            return {"title": title, "mst": law.findtext("법령일련번호") or "",
                    "id": law.findtext("법령ID") or "", "effective": law.findtext("시행일자") or "",
                    "url": "https://www.law.go.kr/법령/" + title}
    return None


def _fetch_text(target, law):
    if not law or not (law.get("mst") or law.get("id")):
        return ""
    params = {"OC": config.LAW_OC or "test", "target": target, "type": "XML"}
    params["MST" if law.get("mst") else "ID"] = law.get("mst") or law.get("id")
    response = requests.get(SERVICE_URL, params=params, timeout=(4, 18))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    chunks = []
    for element in root.iter():
        if element.tag not in ("조", "조문", "조문단위"):
            continue
        title = (element.findtext("조제목") or element.findtext("조문제목") or "").strip()
        text = (element.findtext("조내용") or element.findtext("조문내용") or "").strip()
        relevant = r"주거|지원|사업|월세|정의|목적" if target == "ordin" else r"주택|임대차|보증금|차임|월세|전환"
        if text and (not title or re.search(relevant, title + text[:240])):
            chunks.append((title + " " + text).strip())
    return "\n".join(chunks)[:8500]


def _law_references():
    refs = []
    errors = []
    try:
        ordinance = _law_search("ordin", ORDINANCE_NAME, {ORDINANCE_NAME})
        if ordinance:
            ordinance["kind"] = "법제처 자치법규 API"
            ordinance["excerpt"] = _fetch_text("ordin", ordinance)
            refs.append(ordinance)
        else:
            errors.append("서울특별시 청년주거 기본 조례 검색 결과 없음")
    except Exception as exc:  # API 장애 시 시민 UI에 출처와 상태 표시
        log.warning("housing ordinance API lookup failed: %s", type(exc).__name__)
        errors.append("법제처 자치법규 API 조회에 실패했습니다")
    try:
        act = _law_search("law", "주택임대차보호법")
        if act:
            act["kind"] = "법제처 현행법령 API"
            act["excerpt"] = _fetch_text("law", act)
            refs.append(act)
    except Exception as exc:
        log.warning("housing act API lookup failed: %s", type(exc).__name__)
        errors.append("법제처 현행법령 API 조회에 실패했습니다")
    return refs, errors


def _compare_terms(body):
    stage = "after" if body.get("stage") == "after" else "before"
    facts = {}
    for key, maximum in (("age", 100), ("deposit", 200_000_000), ("rent", 10_000_000),
                         ("income", 200_000_000), ("household", 7)):
        facts[key] = _number(body.get(key), maximum)
    facts["seoul_resident"] = body.get("seoul_resident") is True
    facts["question"] = str(body.get("question") or "").strip()[:1800]

    checks = []
    if facts["age"] is not None:
        checks.append(f"연령 {facts['age']}세: {'2026 공고 연령(19~39세) 범위 안' if 19 <= facts['age'] <= 39 else '2026 공고 연령(19~39세) 범위 밖 또는 군복무 연령연장 추가 확인 필요'}")
    if facts["seoul_resident"]:
        checks.append("서울 주민등록·실거주: 사용자가 예로 입력한 값이며 증빙 확인 필요")
    if stage == "after" and facts["deposit"] is not None and facts["rent"] is not None:
        dep, rent = facts["deposit"], facts["rent"]
        dep_ok = dep <= 80_000_000
        converted = (dep * 45 // 12_000_000) * 1000  # 4.5% ÷ 12개월, 천원 단위 절사
        if rent <= 600_000:
            rent_ok = True
            detail = f"보증금 {dep:,}원(8천만원 이하 여부: {'해당' if dep_ok else '초과'}), 월세 {rent:,}원(60만원 이하)"
        else:
            total = converted + rent
            rent_ok = total <= 900_000
            detail = f"보증금 {dep:,}원, 월세 {rent:,}원, 보증금 월세환산액 {converted:,.0f}원(4.5%÷12), 합계 {total:,.0f}원(90만원 이하 여부: {'해당' if rent_ok else '초과'})"
        checks.append("주거비 기준 예비 대조: " + detail + ("; 8천만원 보증금 기준 초과" if not dep_ok else ""))
        checks.append("주거비 기준 예비 결과: " + ("2026 공고의 금액 기준에 들어올 가능성" if dep_ok and rent_ok else "2026 공고의 금액 기준을 충족하지 않을 가능성") + ". 최종 자격 판정은 아님.")
    if facts["income"] is not None and facts["household"] in INCOME_LIMITS:
        low, high = INCOME_LIMITS[facts["household"]]
        value = facts["income"]
        checks.append(f"월평균 가구소득 {value:,}원 / {facts['household']}인 가구 공고 기준 {low:,}원 초과~{high:,}원 이하: {'범위 안' if low < value <= high else '범위 밖 또는 추가 확인 필요'}")
    checks.append("모집 일정: 2026 회차는 2026-05-19 마감. 2027 일정은 아직 공고되지 않았습니다.")
    return stage, facts, checks


def _prompt(stage, facts, checks, law_refs, law_errors):
    laws = "\n\n".join(
        f"[{ref['kind']}] {ref['title']} / 시행일 {ref.get('effective') or 'API 미제공'}\n{ref.get('excerpt') or '조문 원문을 조회하지 못함'}"
        for ref in law_refs
    )
    mode = facts["mode"]
    mode_instructions = {
        "support": "지원 시기·자격과 다음 절차를 중심으로 답하고 draft는 빈 문자열로 둔다.",
        "application": "계약 후라면 빈칸을 포함한 신청서 초안을 draft에 작성한다. 계약 전이면 draft는 빈 문자열로 둔다.",
        "inquiry": "draft에 담당 기관에 보낼 문의 문안을 작성한다. 아직 공고되지 않은 다음 일정과 필요한 증빙을 확인하는 내용으로 한다.",
        "contract": "입력한 임대차 보증금·월세의 공고상 금액 기준 비교와 확인사항을 중심으로 답한다. draft는 빈 문자열로 둔다.",
    }.get(mode, "지원 절차 안내와 필요한 자료를 설명한다.")
    return [
        {"role": "system", "content": """너는 서울시 청년 주거지원 절차를 안내하는 시민용 AI 도우미다. 법률 자문이나 선정 확정 판정을 하지 않는다.
주거지원 공고의 기준·일정은 제공된 [서울시 2026 모집공고] 내용에만 근거한다. 2026 모집은 마감됐으며 2027 일정은 공고 후 안내 상태다. 미래 일정을 추측하지 않는다.
법령에 관한 설명은 [법제처 API 결과]에 실제로 있는 내용만 사용한다. API 결과가 없거나 원문 조회가 실패하면 법령 내용은 확인하지 못했다고 표시하고 공식 링크로 안내한다. 청년월세지원 공고에 없는 조건이나 조항번호를 만들지 않는다.
금액 대조는 제공된 입력 정보의 예비 계산이며 모든 신청 제외 요건·소득·재산·가구 유형·중복수혜를 검사하지 않았으므로 자격 확정이라고 말하지 않는다. 계약서가 아직 없는 사용자에게 임대차 계약을 서둘러 체결하라고 하지 않는다.
draft는 선택 기능의 지침에 따라 필요한 경우에만 작성한다. 신청서 초안 기능은 계약 후에만, 문의 문안 기능은 언제든 초안을 만들 수 있다. 이름·주민등록번호·주소·계좌번호를 요구하지 않고, 모르는 사실은 빈칸으로 둔다. 답변은 친절하고 간결한 한국어다."""},
        {"role": "user", "content": f"""다음 상황에 대해 선택한 기능의 요청 지침에 따라 안내해 주세요.

[선택 기능] {mode}
[요청 지침] {mode_instructions}
[계약 상태] {'계약 완료/월세 납부 중' if stage == 'after' else '계약 전'}
[입력 정보] {json.dumps(facts, ensure_ascii=False)}
[코드가 계산한 공고 기준 대조] {json.dumps(checks, ensure_ascii=False)}

[서울시 2026 모집공고 요약]
{NOTICE_CONTEXT}
[공식 안내 페이지 기준]
2026-11-12 게시 정보: 2026년 모집 마감, 2027년 공고 예정. 정확한 신청 일정은 추후 공식 공고에서 확인.

[법제처 API 결과]
{laws or 'API 검색 결과를 확보하지 못함'}
[법제처 API 상태]
{'; '.join(law_errors) if law_errors else '조회 완료'}

JSON 결과 필드 summary, next_steps, documents, draft, caution을 채워 주세요. next_steps/documents는 배열입니다. 계약 전이면 draft는 빈 문자열로 둡니다. 조문을 언급할 경우 출처 제목을 함께 적어 주세요."""},
    ]


def answer(body):
    if not config.UPSTAGE_API_KEY:
        return {"error": "UPSTAGE_API_KEY를 .env에 설정해야 LLM 상담을 사용할 수 있습니다.", "answer": "", "sources": []}
    stage, facts, checks = _compare_terms(body)
    mode = body.get("mode") if body.get("mode") in {"support", "application", "inquiry", "contract"} else "support"
    facts["mode"] = mode
    if body.get("other"):
        facts["question"] = (facts["question"] + "\n추가 질문: " + str(body["other"]).strip()[:160]).strip()
    law_refs, law_errors = _law_references()
    raw = upstage.chat(_prompt(stage, facts, checks, law_refs, law_errors), response_format=SCHEMA, temperature=0.0)
    data = json.loads(raw)
    sources = [{"type": "서울시 모집공고", "title": "2026년 서울시 청년월세지원 모집 공고 제2026-1440호", "url": NOTICE_URL, "updated": "2026-04-30"},
               {"type": "서울시 안내", "title": "서울시 청년월세지원 안내 (2026년 모집 마감·2027년 공고 예정)", "url": PROGRAM_URL, "updated": "2026-11-12"}]
    sources.extend({"type": x["kind"], "title": x["title"], "url": x["url"], "effective": x.get("effective", ""), "excerpt": x.get("excerpt", "")[:1600]} for x in law_refs)
    return {"stage": stage, "mode": mode, "answer": data["summary"], "next_steps": data["next_steps"], "documents": data["documents"],
            "draft": data["draft"], "caution": data["caution"], "checks": checks, "sources": sources,
            "law_api_status": "ok" if law_refs else "unavailable", "law_api_warning": "; ".join(law_errors)}

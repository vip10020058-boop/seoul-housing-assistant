"""서울시 조례와 주거지원 공고를 구분해 안내하는 시민용 도우미.

LLM: 기존 Upstage Solar API. 법령: 법제처 OPEN API에서 조례/법률을 조회합니다.
사업 공고 수치는 서울시 2026 모집공고(공고 제2026-1440호) 스냅샷을 사용하고,
새 모집 일정은 공식 공고 페이지로 다시 확인하도록 안내합니다.
"""
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import config
import upstage

log = logging.getLogger(__name__)
SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
ORDINANCE_NAME = "서울특별시 청년주거 기본 조례"
SEOUL_ORDINANCE_TOPICS = {
    "parking": ("주차", "주차장", "주차요금", "주차구역", "거주자우선"),
    "animal": ("반려동물", "유기견", "동물", "맹견", "동물등록"),
    "bike": ("자전거", "자전거도로", "자전거주차"),
    "park": ("공원", "공원음주", "공원에서", "공원시설"),
    "smoking": ("흡연", "담배", "금연", "간접흡연"),
}
ORDINANCE_TITLES = {
    "parking": "서울특별시 주차장 설치 및 관리 조례",
    "animal": "서울특별시 동물보호 조례",
    "bike": "서울특별시 자전거이용 활성화에 관한 조례",
    "park": "서울특별시 도시공원 조례",
    "smoking": "서울특별시 금연환경 조성 및 간접흡연 피해방지조례",
    "youth": ORDINANCE_NAME,
}
YOUTH_ORDINANCE_TITLE = "서울특별시 청년 기본 조례"
NOTICE_URL = "https://soco.seoul.go.kr/bos/cmmn/file/fileDown.do?atchFileId=d964a2a457e3421c95f8b3de69846c99&bbsId=&fileSn=1&menuNo=400018"
PROGRAM_URL = "https://soco.seoul.go.kr/youth/bbs/BMSR00174/view.do?boardId=6734&menuNo=400043&pageIndex=1"
LH_NOTICE_URL = "https://www.data.go.kr/data/15058530/openapi.do"
MOLIT_RENT_URL = "https://www.data.go.kr/data/15126474/openapi.do"
HUG_DEFAULT_URL = "https://www.khug.or.kr/houstar/web/p03/01/p030105.jsp?articleId=38236&currentPage=1&mode=S"

SEOUL_DISTRICTS = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710", "강동구": "11740",
}


def _public_record_list(value):
    """Find likely record arrays in vendor JSON while retaining the original public fields."""
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return value
        for item in value:
            found = _public_record_list(item)
            if found:
                return found
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, list):
                found = _public_record_list(item)
                if found:
                    return found
        for item in value.values():
            found = _public_record_list(item)
            if found:
                return found
    return []


def hug_default_debtors(api_key, query=""):
    """Query HUG's public chronic-default landlord list API without retaining its key."""
    if not api_key or len(api_key) > 1024:
        return {"error": "HUG 공개명단 API 키를 설정해 주세요."}
    response = requests.get("https://www.khug.or.kr/defaultOnDebitList.do",
                            params={"API_KEY": api_key}, timeout=(5, 20))
    response.raise_for_status()
    data = response.json()
    records = _public_record_list(data)
    term = str(query or "").strip().lower()[:120]
    if not term:
        return {"error": "공개명단을 한꺼번에 열람하지 않도록 임대인 이름 또는 주소 일부를 입력해 주세요."}
    records = [row for row in records if term in " ".join(str(v) for v in row.values()).lower()]
    return {"records": records[:100], "count": len(records), "as_of": date.today().isoformat(),
            "sources": [{"type": "HUG 상습 채무불이행자 공개 API", "title": "상습 채무불이행자 명단공개", "url": HUG_DEFAULT_URL,
                         "updated": "수시 자동갱신"}],
            "caution": "공개명단은 법정 지정된 대상자의 명단입니다. 검색 결과 없음은 해당 임대인의 안전이나 보증 가능성을 보증하지 않으며, 이름·주소 표기가 다르면 일치하지 않을 수 있습니다."}


def hug_statistics():
    """Return authoritative HUG statistical file sources and their stated coverage."""
    return {"statistics": [
        {"title": "지역별 전세금반환보증 사고현황", "coverage": "시·도·구별 최근 3개월 평균 사고 건수·사고 금액·사고율", "as_of": "2026-08-31", "format": "XLSX", "url": "https://www.data.go.kr/data/15134221/fileData.do"},
        {"title": "전세관련 현황", "coverage": "보증 가입·사고 건수와 금액(2013~2024 및 2025년 5월까지), 경·공매지원 신청자의 피해주택 수(2023-07~2025-05)", "as_of": "2025-05", "format": "XLSX", "url": "https://www.data.go.kr/data/15144220/fileData.do"},
        {"title": "서울 전세보증금반환보증 사고 및 대위변제 현황", "coverage": "서울시 연도별 사고 건수·금액·대위변제 현황(2020~2023)", "as_of": "2023", "format": "XLSX", "url": "https://www.khug.or.kr/houstar/web/p03/01/p030105.jsp?articleId=37002&currentPage=1&mode=S"},
        {"title": "HUG 전세보증금반환보증 사고건수 및 금액", "coverage": "연도별 공식 경영공시 목록과 첨부 원문", "as_of": "공식 공시 페이지에서 확인", "format": "공시 자료", "url": "https://www.khug.or.kr/hug/web/mo/ib/moib000028.jsp"},
    ], "caution": "이 통계는 HUG 파일 데이터/공시자료입니다. 현재 사용자 발급 OPEN API는 상습 채무불이행자 명단 조회용이며, 이 통계를 반환하지 않습니다. 피해주택 수는 해당 자료 설명상 경·공매지원 신청자의 주택 수이므로 전체 전세사기 피해주택 수와 같지 않습니다."}


def _lh_notices(api_key, keyword="서울"):
    if not api_key:
        return [], "공공데이터포털 API 키가 없어 LH 임대공고를 조회하지 못했습니다."
    today = date.today()
    start = (today - timedelta(days=180)).strftime("%Y.%m.%d")
    end = today.strftime("%Y.%m.%d")
    url = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"
    response = requests.get(url, params={"ServiceKey": api_key, "PG_SZ": 100, "PAGE": 1,
                           "PAN_NM": keyword or "서울", "UPP_AIS_TP_CD": "06", "CNP_CD": "11",
                           "PAN_NT_ST_DT": start, "CLSG_DT": end}, timeout=(5, 20))
    response.raise_for_status()
    data = response.json()
    records = _public_record_list(data)
    return records[:50], ""


def _molit_rents(api_key, district="마포구", months=3):
    if not api_key:
        return [], "공공데이터포털 API 키가 없어 국토부 실거래를 조회하지 못했습니다."
    districts = SEOUL_DISTRICTS if district == "서울 전체(구별 비교)" else ({district: SEOUL_DISTRICTS[district]} if district in SEOUL_DISTRICTS else {})
    if not districts:
        return [], "서울시 구를 선택해 주세요."
    url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
    now = date.today()
    periods = []
    for offset in range(1 if district == "서울 전체(구별 비교)" else max(1, min(int(months), 6))):
        year, month = now.year, now.month - offset
        while month <= 0:
            year -= 1
            month += 12
        periods.append((year, month))
    def fetch(area, code, year, month):
        response = requests.get(url, params={"serviceKey": api_key, "LAWD_CD": code,
                               "DEAL_YMD": f"{year}{month:02d}", "pageNo": 1, "numOfRows": 1000},
                               timeout=(5, 20))
        response.raise_for_status()
        root = ET.fromstring(response.content)
        result_code = root.findtext(".//header/resultCode") or root.findtext(".//cmmMsgHeader/returnReasonCode")
        if result_code and result_code not in {"00", "000", "INFO-000"}:
            raise ValueError("국토부 전월세 API 응답 오류")
        result = []
        for item in root.findall(".//item"):
            row = {child.tag: (child.text or "").strip() for child in item}
            row["조회월"] = f"{year}-{month:02d}"
            row["서울 자치구"] = area
            result.append(row)
        return result
    rows = []
    jobs = [(area, code, year, month) for area, code in districts.items() for year, month in periods]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, *job) for job in jobs]
        for future in as_completed(futures):
            rows.extend(future.result())
    return rows[:25000], ""


def _rent_summary(rows):
    """Aggregate official MOLIT apartment rent records; deposit/rent values are in 만원."""
    def number(row, *keys):
        for key in keys:
            raw = str(row.get(key, "")).replace(",", "").strip()
            if raw:
                try:
                    return float(raw)
                except ValueError:
                    pass
        return None

    monthly, by_district = {}, {}
    deposits, rents = [], []
    for row in rows:
        period = str(row.get("조회월", ""))
        group = monthly.setdefault(period, {"month": period, "count": 0})
        group["count"] += 1
        deposit = number(row, "보증금액", "deposit")
        rent = number(row, "월세금액", "monthlyRent")
        if deposit is not None:
            deposits.append(deposit)
        if rent is not None:
            rents.append(rent)
        area = row.get("서울 자치구", "")
        if area:
            group = by_district.setdefault(area, {"district": area, "count": 0, "deposits": [], "rents": []})
            group["count"] += 1
            if deposit is not None: group["deposits"].append(deposit)
            if rent is not None: group["rents"].append(rent)
    avg = lambda values: round(sum(values) / len(values), 1) if values else None
    district_rows = [{"district": item["district"], "count": item["count"],
                      "average_deposit_manwon": avg(item["deposits"]),
                      "average_monthly_rent_manwon": avg(item["rents"])}
                     for item in by_district.values()]
    return {"count": len(rows), "average_deposit_manwon": avg(deposits),
            "average_monthly_rent_manwon": avg(rents),
            "months": sorted(monthly.values(), key=lambda x: x["month"]),
            "districts": sorted(district_rows, key=lambda x: x["district"]),
            "unit": "만원", "source_url": MOLIT_RENT_URL}


def connection_status():
    return {"upstage_configured": bool(config.UPSTAGE_API_KEY),
            "law_configured": bool(config.LAW_OC and config.LAW_OC != "test"),
            "public_data_configured": bool(config.PUBLIC_DATA_API_KEY),
            "hug_configured": bool(config.HUG_API_KEY)}


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


def _law_search(target, query, exact_names=(), law_oc=None):
    params = {"OC": law_oc or config.LAW_OC or "test", "target": target, "type": "XML", "query": query,
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


def _fetch_text(target, law, law_oc=None):
    if not law or not (law.get("mst") or law.get("id")):
        return ""
    params = {"OC": law_oc or config.LAW_OC or "test", "target": target, "type": "XML"}
    params["MST" if law.get("mst") else "ID"] = law.get("mst") or law.get("id")
    response = requests.get(SERVICE_URL, params=params, timeout=(4, 18))
    response.raise_for_status()
    root = ET.fromstring(response.content)
    chunks = []
    for element in root.iter():
        if element.tag not in ("조", "조문", "조문단위"):
            continue
        title = (element.findtext("조제목") or element.findtext("조문제목") or "").strip()
        number = (element.findtext("조문번호") or element.findtext("조문가지번호") or "").strip()
        text = (element.findtext("조내용") or element.findtext("조문내용") or "").strip()
        relevant = r"주거|지원|사업|월세|정의|목적" if target == "ordin" else r"주택|임대차|보증금|차임|월세|전환"
        if text and (not title or re.search(relevant, title + text[:240])):
            article_number = int(number) // 100 if number.isdigit() and int(number) >= 100 else (int(number) if number.isdigit() else None)
            sub_number = int(element.findtext("조문가지번호") or "0")
            label = f"제{article_number}조" if article_number is not None else "조문"
            if article_number is not None and sub_number:
                label += f"의{sub_number}"
            chunks.append((f"{label} {title} {text}").strip())
    return "\n".join(chunks)[:8500]


def _relevant_ordinances(mode, facts):
    text = " ".join((facts.get("question", ""), facts.get("other", ""), mode)).lower()
    selected = [ORDINANCE_TITLES[key] for key, words in SEOUL_ORDINANCE_TOPICS.items()
                if any(word in text for word in words)]
    if mode in {"support", "application", "inquiry", "recommend", "contract"}:
        selected.insert(0, ORDINANCE_NAME)
        if facts.get("age") is not None and facts["age"] <= 39:
            selected.append(YOUTH_ORDINANCE_TITLE)
    return list(dict.fromkeys(selected))[:3]


def _law_references(mode, facts, law_oc=None):
    refs = []
    errors = []
    for title in _relevant_ordinances(mode, facts):
        try:
            ordinance = _law_search("ordin", title, {title}, law_oc)
            if ordinance:
                ordinance["kind"] = "서울특별시 조례 · 법제처 자치법규 API"
                ordinance["excerpt"] = _fetch_text("ordin", ordinance, law_oc)
                refs.append(ordinance)
            else:
                errors.append(f"서울특별시 조례 검색 결과 없음: {title}")
        except Exception as exc:
            log.warning("housing ordinance API lookup failed: %s", type(exc).__name__)
            errors.append(f"법제처 자치법규 API 조회 실패: {title}")
    try:
        act = _law_search("law", "주택임대차보호법", law_oc=law_oc)
        if act:
            act["kind"] = "국가 법령 · 법제처 현행법령 API"
            act["excerpt"] = _fetch_text("law", act, law_oc)
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
    facts["district"] = str(body.get("district") or "").strip()[:20]
    facts["budget_rent"] = _number(body.get("budget_rent"), 10_000_000)
    facts["budget_deposit"] = _number(body.get("budget_deposit"), 2_000_000_000)
    facts["contract_terms"] = {
        key: str(body.get(key) or "").strip()[:180]
        for key in ("contract_start", "contract_end", "payment_day", "move_in_date", "maintenance_fee", "special_terms", "building_type")
    }

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


def _prompt(stage, facts, checks, law_refs, law_errors, public_context=""):
    laws = "\n\n".join(
        f"[{ref['kind']}] {ref['title']} / 시행일 {ref.get('effective') or 'API 미제공'}\n{ref.get('excerpt') or '조문 원문을 조회하지 못함'}"
        for ref in law_refs
    )
    mode = facts["mode"]
    mode_instructions = {
        "support": "지원 시기·자격과 다음 절차를 중심으로 답하고 draft는 빈 문자열로 둔다. 서울시 조례와 해당 연도 모집공고 기준을 구분한다.",
        "application": "계약 후라면 빈칸을 포함한 신청서 초안을 draft에 작성한다. 계약 전이면 draft는 빈 문자열로 둔다.",
        "inquiry": "draft에 담당 기관에 보낼 문의 문안을 작성한다. 아직 공고되지 않은 다음 일정과 필요한 증빙을 확인하는 내용으로 한다.",
        "contract": "주택임대차계약서 초안을 draft에 작성한다. 사용자 입력의 조건을 사용하고 미입력 정보는 [기재 필요]로 남긴다. 임대인·임차인의 성명, 주민번호, 상세 주소, 계좌정보는 묻거나 지어내지 않는다. 목적물, 계약기간, 보증금, 월세, 지급일, 관리비, 인도일, 특약, 서명란을 포함한 바로 복사할 수 있는 초안으로 만든다. 법령 API 원문에 포함된 사항만 법적 근거로 언급하며 법률 유효성을 보장하지 않는다.",
        "recommend": "입력한 예산·지역·가구 조건을 기준으로 공공 임대주택 공고와 지원제도를 추천한다. 조회된 데이터에 있는 것만 말하고, 실거래는 현재 매물로 표현하지 않는다.",
        "listings": "LH 공공임대 모집공고와 국토부 전월세 실거래 참고 자료를 구분해 안내한다. 실거래는 현재 매물이 아니며 계약 가능 매물이라고 표현하지 않는다.",
    }.get(mode, "지원 절차 안내와 필요한 자료를 설명한다.")
    return [
        {"role": "system", "content": """너는 서울시 조례를 시민의 실제 주거 상황에 연결하는 AI 도우미다. 법률 자문이나 선정 확정 판정을 하지 않는다.
사용자 상황과 직접 관련된 서울특별시 조례를 우선 찾아 조례에서 확인되는 제도 목적·시의 책무·지원 근거를 설명한다. 조례가 정하지 않은 신청 기간, 소득·재산 기준, 금액, 서류, 선정 절차는 조례에서 확인된 것처럼 말하지 않는다.
답변에서 '서울시 조례에서 확인되는 내용'과 '모집공고·사업 안내에서 확인할 내용'을 반드시 구분한다. 현재 조회된 조문 원문만 조례 근거로 설명하고 해당 조례명·조문 번호·시행일·원문 링크를 제공한다. 관련 조례를 검색하지 못했거나 원문을 받지 못하면 확인 불가라고 표시한다.
주거지원 공고의 기준·일정은 제공된 [서울시 2026 모집공고] 내용에만 근거한다. 2026 모집은 마감됐으며 2027 일정은 공고 후 안내 상태다. 미래 일정을 추측하지 않는다.
법령에 관한 설명은 [서울특별시 조례·법제처 API 결과]에 실제로 있는 내용만 사용한다. 조례/API 결과가 없거나 원문 조회가 실패하면 법령 내용을 확인하지 못했다고 표시한다. 모집공고에 없는 조건이나 조항번호를 만들지 않는다.
공공 공고·실거래 API가 실패했거나 결과가 비어 있으면 결과를 만들어 내지 말고 조회 실패 또는 결과 없음이라고 밝힌다. 국토부 실거래는 과거 신고 계약 사례이므로 현재 매물이라고 부르지 않는다. 예산/지역에 맞는 중개 매물 자체는 이 API 결과에 포함되지 않는 한 찾았다고 주장하지 않는다.
금액 대조는 제공된 입력 정보의 예비 계산이며 모든 신청 제외 요건·소득·재산·가구 유형·중복수혜를 검사하지 않았으므로 자격 확정이라고 말하지 않는다. 계약서가 아직 없는 사용자에게 임대차 계약을 서둘러 체결하라고 하지 않는다.
draft는 선택 기능의 지침에 따라 필요한 경우에만 작성한다. 계약서 초안은 사용자가 입력한 조건만 반영하고 빈 정보는 [기재 필요]로 표시한다. 법령 원문에 없는 권리·의무나 특약을 법정 필수조항처럼 꾸며내지 않는다. 이름·주민등록번호·상세주소·계좌번호를 요구하지 않는다. 답변은 친절하고 간결한 한국어다."""},
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

[서울특별시 조례·법제처 API 결과]
{laws or 'API 검색 결과를 확보하지 못함'}
[법제처 API 상태]
{'; '.join(law_errors) if law_errors else '조회 완료'}

[공공주거·실거래 API 조회 결과]
{public_context or '이번 기능에서는 조회하지 않음'}

JSON 결과 필드 summary, next_steps, documents, draft, caution을 채워 주세요. next_steps/documents는 배열입니다. 계약서 초안은 사용자가 입력한 계약 조건과 확인된 법령을 바탕으로 작성하고, 확인하지 못한 세부사항은 [기재 필요]로 남깁니다. 조례 설명은 실제 조회된 원문에 한정하고, 조례와 공고 기준을 구획별로 분리하세요."""},
    ]


def answer(body):
    api_key = config.UPSTAGE_API_KEY.strip()
    law_oc = config.LAW_OC.strip()
    mode = body.get("mode") if body.get("mode") in {"support", "application", "inquiry", "contract", "recommend", "listings", "hug", "hugstats"} else "support"
    if mode == "hugstats":
        return hug_statistics()
    if mode == "hug":
        try:
            return hug_default_debtors(config.HUG_API_KEY.strip(), body.get("landlord_query", ""))
        except Exception as exc:
            log.warning("HUG public list lookup failed: %s", type(exc).__name__)
            return {"error": "HUG 공개명단 API를 조회하지 못했습니다. HUG API 키와 사용 권한을 확인해 주세요."}
    if not api_key:
        return {"error": "서버 환경변수 UPSTAGE_API_KEY를 설정해 주세요. 배포 환경은 Vercel 프로젝트 설정에서 추가할 수 있습니다.", "answer": "", "sources": []}
    if len(api_key) > 512 or len(law_oc) > 128:
        return {"error": "서버의 API 환경변수 설정을 확인해 주세요.", "answer": "", "sources": []}
    stage, facts, checks = _compare_terms(body)
    facts["mode"] = mode
    if body.get("other"):
        facts["question"] = (facts["question"] + "\n추가 질문: " + str(body["other"]).strip()[:160]).strip()
    facts["other"] = str(body.get("other") or "").strip()[:300]
    law_refs, law_errors = _law_references(mode, facts, law_oc)
    public_context = ""
    public_sources = []
    public_data = {"lh_notices": [], "rent_records": [], "rent_summary": None}
    if mode in {"recommend", "listings"}:
        data_key = config.PUBLIC_DATA_API_KEY.strip()
        district = str(body.get("district") or "마포구").strip()
        try:
            notices, notice_error = _lh_notices(data_key)
            public_data["lh_notices"] = notices[:30]
            public_context += "[한국토지주택공사 임대주택 공고 API]\n" + json.dumps(notices[:20], ensure_ascii=False)
            public_sources.append({"type": "LH 임대공고 API", "title": "한국토지주택공사 분양임대공고문 조회 서비스", "url": LH_NOTICE_URL})
            if notice_error:
                public_context += "\n" + notice_error
        except Exception as exc:
            log.warning("LH notice lookup failed: %s", type(exc).__name__)
            public_context += "LH 임대공고 API 조회 실패. 인증키와 서비스 사용 신청 상태를 확인해야 함."
        try:
            rent_rows, rent_error = _molit_rents(data_key, district)
            public_data["rent_records"] = rent_rows[:80]
            public_data["rent_summary"] = _rent_summary(rent_rows)
            public_context += "\n[국토교통부 아파트 전월세 실거래 API] 최근 신고 실거래 참고값이며 현재 매물 목록이 아님\n" + json.dumps(rent_rows[:100], ensure_ascii=False)
            public_sources.append({"type": "국토부 실거래 API", "title": "국토교통부 아파트 전월세 실거래가 자료", "url": MOLIT_RENT_URL})
            if rent_error:
                public_context += "\n" + rent_error
        except Exception as exc:
            log.warning("MOLIT rent lookup failed: %s", type(exc).__name__)
            public_context += "\n국토부 실거래 API 조회 실패. 인증키와 서비스 사용 신청 상태를 확인해야 함."
    raw = upstage.chat(_prompt(stage, facts, checks, law_refs, law_errors, public_context), response_format=SCHEMA,
                       temperature=0.0, api_key=api_key)
    data = json.loads(raw)
    ordinance_sources = [{"type": x["kind"], "title": x["title"], "url": x["url"], "effective": x.get("effective", ""), "excerpt": x.get("excerpt", "")[:2400]} for x in law_refs if "조례" in x["kind"]]
    national_law_sources = [{"type": x["kind"], "title": x["title"], "url": x["url"], "effective": x.get("effective", ""), "excerpt": x.get("excerpt", "")[:1600]} for x in law_refs if "국가 법령" in x["kind"]]
    announcement_sources = [{"type": "서울시 모집공고", "title": "2026년 서울시 청년월세지원 모집 공고 제2026-1440호", "url": NOTICE_URL, "updated": "2026-04-30"},
                            {"type": "서울시 사업 안내", "title": "서울시 청년월세지원 안내 (2026년 모집 마감·2027년 공고 예정)", "url": PROGRAM_URL, "updated": "2026-11-12"}]
    sources = announcement_sources + ordinance_sources + national_law_sources + public_sources
    return {"stage": stage, "mode": mode, "answer": data["summary"], "next_steps": data["next_steps"], "documents": data["documents"],
            "draft": data["draft"], "caution": data["caution"], "checks": checks, "sources": sources,
            "ordinance_sources": ordinance_sources, "announcement_sources": announcement_sources,
            "national_law_sources": national_law_sources,
            "public_data": public_data,
            "law_api_status": "ok" if ordinance_sources else "unavailable", "law_api_warning": "; ".join(law_errors)}

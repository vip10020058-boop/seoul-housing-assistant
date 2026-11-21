# AI 서울 주거 도우미

서울에서 집을 구하거나 계약한 시민이 조건에 맞는 주거 지원, 공공임대 공고, 임대차 절차와 공식 서식을 한곳에서 확인하는 웹 앱입니다. 첫 화면에서 질문을 입력하거나 왼쪽 메뉴에서 필요한 작업으로 들어갈 수 있습니다.

- **상담과 제도 추천** — 서울시 조례, 국가 법령, 사업 공고를 구분해 보여줍니다. 가입이나 선정 자격을 단정하지 않고 다음 절차와 기관 링크를 제공합니다.
- **공공임대와 전월세 거래** — LH 모집공고에서 공개한 단지 주소를 지도에 표시합니다. 실거래 통계는 신고된 계약을 기준으로 집계하고 구청 주변 지도를 함께 보여줍니다. 지도에서 카카오맵으로 이동해 주변을 더 살펴볼 수 있습니다.
- **계약서·신청서·민원** — 매매·전세·월세는 당사자, 지급 조건, 특약, 서명란이 있는 PDF 초안으로 준비할 수 있습니다. 민원은 기관에 전달할 문안을 정리하고, 공공 서식은 해당 기관에서 받거나 작성할 수 있도록 연결합니다.
- **HUG 사고 통계와 공개명단** — 통계의 집계 범위·기준일과 자료 링크를 표시합니다. 공개명단은 HUG가 공개한 정보만 조회하며, 명단의 주소는 사고 물건 주소로 판단할 수 없습니다.

## 실행

Python 3.11 이상에서 아래 명령을 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# .env에 서비스 키를 등록한 뒤 실행합니다.
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

브라우저에서 [로컬 서비스](http://127.0.0.1:8000/)를 엽니다. API 키는 서버 환경변수에서 읽습니다. Vercel에서는 프로젝트의 **Settings → Environment Variables**에서 실제 사용할 환경(Production 또는 Preview)을 선택해 등록한 뒤 새로 배포합니다. `/health`와 `/ui/housing/status`는 키 값을 공개하지 않고 설정 여부를 보여줍니다. 키가 설정된 상태에서 실제 서비스 조회를 실행해 활용 승인과 응답을 확인합니다.

| 환경변수 | 사용처와 발급·승인 안내 |
|---|---|
| `UPSTAGE_API_KEY` | Solar 답변 생성·임베딩·검증, 별표 Document Parse. [Upstage Console](https://console.upstage.ai/) |
| `LAW_OC` | 현행 조례·법령 조회와 자료 수집. 배포에는 본인 발급 OC를 사용합니다. [법제처 공동활용 안내](https://open.law.go.kr/LSO/openApi/guideList.do) |
| `LH_API_KEY` | LH 분양·임대공고 API 활용 승인 후 발급받은 인증키. [LH API 명세](https://www.data.go.kr/data/15058530/openapi.do) |
| `LH_DETAIL_API_KEY` | 선택 사항. 공고의 단지 정보를 추가로 찾는 데 쓰는 상세조회 키. [LH 공고 상세 API 명세](https://www.data.go.kr/data/15057999/openapi.do) |
| `MOLIT_API_KEY` | 국토부 아파트 전월세 실거래 API 활용 승인 후 발급받은 인증키. [국토부 API 명세](https://www.data.go.kr/data/15126474/openapi.do) |
| `PUBLIC_DATA_API_KEY` | LH·국토부 전용 키가 없을 때 사용하는 호환 항목. 해당 키로 각 API 활용이 승인되어 있어야 합니다. [공공데이터포털](https://www.data.go.kr/) |
| `HUG_API_KEY` | 상습 채무불이행자 명단공개 API 인증키. [HUG Open API 안내](https://www.khug.or.kr/houstar/web/p03/01/p030105.jsp?articleId=38236&currentPage=1&mode=S) |
| `KAKAO_MAP_REST_API_KEY` | 선택 사항. 카카오맵이 활성화된 앱의 REST API 키. 기존 Vercel 변수 `kakao_map`도 사용합니다. [카카오맵 사용 설정](https://developers.kakao.com/docs/ko/kakaomap/common) |

`.env`와 로컬 인덱스 파일은 Git에서 제외됩니다. 청년월세지원의 연도별 접수일·금액·서류는 해당 회차 공고에서 확인합니다. [서울주거포털 사업 안내](https://housing.seoul.go.kr/site/main/content/sh01_060513) · [2026 모집 공고](https://housing.seoul.go.kr/site/main/board/notice/12667).

## 채점 API

`POST /ask`는 2026년 10월 1일에 시행 중인 서울특별시 조례 6종(주차장·동물보호·자전거·도시공원·청년·금연)을 검색합니다. 시민용 `POST /ui/housing/ask`가 조회하는 최신 조례·법률·사업 자료와 별개로 운영됩니다.

```http
POST /ask
Content-Type: application/json

{"question":"서울시 공영주차장 1급지 노상주차장 5분 요금은 얼마인가요?"}
```

근거 있는 응답은 `answer`, `citation`, `source_url`, `effective_date`를 반환합니다. 인용에는 답변에 사용한 조·항과 별표가 들어갑니다. 근거가 없거나 입력·상류 API 처리에 실패한 경우 같은 네 필드로 안전 응답을 반환합니다.

```json
{"answer":"확인 불가","citation":[],"source_url":"","effective_date":""}
```

운영에서 `USE_VERIFY=1`과 `CHUNK_UNIT=paragraph`를 유지합니다. 서버의 요청 대기와 응답 처리 제한은 75초이고, 대회 문항 한도는 90초입니다.

## 데이터 재현과 검증

저장소에는 여섯 조례의 기준일 원문·메타데이터, 항 단위 및 조 단위 비교용 조각, Document Parse로 정리한 별표가 포함된 검색 조각이 있습니다. 첫 실행은 이 자료로 키워드 검색을 할 수 있습니다. 혼합 검색용 Solar 임베딩은 `build_index.py`가 만드는 `vectors_<단위>.npy`와 `vectors_<단위>.manifest.json`을 해당 검색 조각과 함께 배치합니다. 둘 중 하나가 없거나 자료가 맞지 않으면 키워드 검색을 사용합니다. 운영 배포에서 자료와 벡터의 상태는 `/health`의 `corpus_ready`와 `vector_index_available`로 확인합니다.

수집과 파싱은 데이터 재구축이 필요할 때 실행합니다. Document Parse가 만든 파일은 기존 검색 조각을 재생성할 때 함께 보관해 사용합니다. 수집된 조례가 기준일에 실제 적용되는지 확인한 뒤 인덱스를 만듭니다.

```powershell
.\.venv\Scripts\python.exe -X utf8 fetch_ordinances.py
.\.venv\Scripts\python.exe -X utf8 parse_annex.py
.\.venv\Scripts\python.exe -X utf8 build_index.py
.\.venv\Scripts\python.exe -X utf8 check.py --retrieval --offline
```

실제 서버와 API 키를 설정한 뒤 공개 예시, 함정 문항, 응답 시간과 형식을 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -X utf8 check.py --url http://127.0.0.1:8000/ask --parallel 2
```

채점 기간에는 배포 주소에서 같은 점검을 실행하고 평가자가 인증 화면 없이 `POST /ask`를 호출할 수 있는지 확인합니다. [발표·녹화 대본](HOUSING_DEMO.md) · [제출 및 피드백 반영표](SUBMISSION.md).

## 공식 원본과 접수

- 전·월세 계약서의 공식 사용용과 게시용은 법무부 자료실에서 받습니다. [주택임대차 표준계약서](https://www.moj.go.kr/moj/163/subview.do).
- 임대차 신고 시에는 시행규칙의 별지 서식과 신고 절차를 확인합니다. [시행규칙 및 별지 서식](https://www.law.go.kr/lsInfoP.do?joNo=000602&lsId=012794) · [부동산거래관리시스템](https://rtms.molit.go.kr/).
- 주거급여는 정부24, 청년월세지원은 서울주거포털, 민원은 서울시 응답소 또는 국민신문고에서 실제 절차를 확인합니다. [주거급여 신청](https://www.gov.kr/mw/AA020InfoCappView.do?CappBizCD=16130000040) · [서울 청년월세지원](https://housing.seoul.go.kr/site/main/content/sh01_060513) · [서울시 응답소](https://eungdapso.seoul.go.kr/) · [국민신문고](https://www.epeople.go.kr/).
- HUG 사고현황은 지역별 최근 3개월 평균의 사고 건수·금액·사고율을 다룹니다. 피해주택 수는 경·공매지원 신청자 기준입니다. [지역별 사고현황](https://www.data.go.kr/data/15134221/fileData.do) · [전세 관련 현황](https://www.data.go.kr/data/15144220/fileData.do).

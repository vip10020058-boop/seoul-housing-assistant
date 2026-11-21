# AI 서울 주거도우미

서울의 주거지원 제도, 공공임대 공고, 임대차 조건과 민원 준비를 한 화면에서 돕는 웹 앱입니다. 기존 서울 조례 Q&A 화면은 기본 서비스 화면에서 제거했습니다.

## 서비스 기능

- **맞춤 주거 상담**: 주거 상황을 입력하면 Upstage Solar와 법제처 Open API를 바탕으로 절차와 공식 근거를 안내합니다.
- **제도·복지 추천**: 희망 지역과 예산을 입력해 서울 청년월세지원 기준과 LH 공공임대 공고를 함께 확인합니다.
- **공공임대·시세 찾기**: LH 임대공고 API와 국토부 전월세 실거래 API를 조회합니다. 실거래는 신고된 계약 사례이며 현재 중개 중인 매물은 아닙니다.
- **계약 점검·문서 작성**: 지원 조건 예비 대조, 신청서 초안, 문의·민원 문안을 준비합니다.
- **HUG 공개명단·사고 통계**: 상습 채무불이행자 명단 API 조회와 HUG 집계 파일 원문 연결을 별도 메뉴로 제공합니다.

## 실행

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://127.0.0.1:8000/`에 접속합니다. 배포 사이트는 [seoul-housing-assistant.vercel.app](https://seoul-housing-assistant.vercel.app/)입니다.

상담 API는 화면 우측 상단 `API 설정`에서 Upstage Solar 키와 법제처 OC를 입력합니다. LH·국토부 자료 조회용 공공데이터포털 키와 HUG 공개명단 키는 필요한 기능에서 선택적으로 입력합니다. 입력 키는 브라우저 탭 메모리에만 남고 서버나 저장소에 저장되지 않습니다.

## 공식 데이터 출처

- [법제처 Open API 안내](https://open.law.go.kr/LSO/openApi/guideList.do)
- [LH 임대공고 Open API](https://www.data.go.kr/data/15058530/openapi.do)
- [국토부 아파트 전월세 실거래 API](https://www.data.go.kr/data/15126474/openapi.do)
- [HUG 상습 채무불이행자 명단 API](https://www.khug.or.kr/houstar/web/p03/01/p030105.jsp?articleId=38236&currentPage=1&mode=S)
- [HUG 지역별 사고 건수·금액·사고율 파일](https://www.data.go.kr/data/15134221/fileData.do)
- [HUG 전세 관련 사고·피해주택 현황 파일](https://www.data.go.kr/data/15144220/fileData.do)

HUG 상습 채무불이행자 API는 지역별 사고 통계 API가 아닙니다. 사고 통계와 피해주택 자료는 별도 파일 데이터이며 원문 페이지에 표시된 기준일과 집계 범위를 확인하도록 연결합니다. 피해주택 수 자료는 경·공매지원 신청 기준이므로 전체 전세사기 피해주택 수와 동일하지 않습니다.

## 경진대회 채점 경로

대회 규칙을 위해 `POST /ask`는 기존 고정 조례 자료를 이용하는 채점 경로로 유지합니다. 네 필드(`answer`, `citation`, `source_url`, `effective_date`) 응답 계약과 채점용 범위는 시민용 AI 주거도우미 경로와 분리되어 있습니다. 이 경로에 필요한 검색 코드와 자료는 서비스 동작에 포함되며, 기본 사용자 화면에는 표시되지 않습니다.

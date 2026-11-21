# 서울시 조례 Q&A — 공공 법령 Q&A AI 경진대회

> **2026-11-20 로컬 고도화본**: 이 ZIP에는 아직 GitHub에 push하지 않은 검색·표 청킹·함정 방어·근거 검증·API 안정성·대화 문맥·Tool Calling UI·테스트 개선이 포함됩니다.
> [상세 변경 및 검증 기록](UPGRADE_2026_11_20.md)을 먼저 읽으세요. 이 문서의 이전 점검 기록은 과거 기록이며 새 버전의 실 API 정답률을 뜻하지 않습니다.
> 기술 검증 UI: `http://127.0.0.1:8000/agent` (시민용 `/`과 독립).
> 주거지원 데모 UI: `http://127.0.0.1:8000/housing` (채점용 `/ask`와 독립).


서울특별시 조례 6종을 대상으로, 사용자의 자연어 질문에 **실제 조문을 근거로** 답하고
조·항 단위 인용·원문 링크·시행일을 함께 제공하는 RAG 기반 Q&A API입니다.

공식 스타터 킷(baseline)을 기반으로 대회 채점 규격에 맞게 수정했습니다.

```
법제처 OPEN API ─▶ 조·항 단위 조각 ─▶ 검색(임베딩+키워드) ─▶ Solar 답변 ─▶ 검증 ─▶ 대회 JSON
       │                 ▲
       └─ 별표(요금표) ─▶ Document Parse
```

## 대회 규격 요약

- **대상**: 서울특별시 조례 6종(주차장·동물보호·자전거·도시공원·청년·금연), **2026-10-01 기준 시행 중인 버전**. 개별 조례의 실제 시행일 메타데이터는 마지막 개정 시행일에 따라 기준일보다 이전일 수 있습니다. 자치구(OO구) 조례 인용은 오답.
- **엔드포인트**: `POST /ask`, 요청 `{"question": "..."}`
- **응답 JSON 4필드**: `answer`, `citation`, `source_url`, `effective_date`
- **근거 없음**: `{"answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""}`
- **검색 결과 병합**: 원 질문·확장 질문·하위 질문의 검색 결과는 개별 청크 ID 기준 RRF로 결합합니다. 같은 별표 라벨을 가진 서로 다른 청크는 별도 근거로 유지합니다.
- **제약**: 문항당 90초 이내, 항상 유효 JSON (서버 오류·형식 깨짐 시 0점)

## 가장 쉬운 실행 (Windows, 더블클릭)

1. 저장소를 받습니다 (Code → Download ZIP 후 압축 해제, 또는 `git clone`).
2. `.env.example`을 복사해 `.env`로 만들고 `UPSTAGE_API_KEY=` 뒤에 키를 입력합니다.
   (`.env`가 없으면 `1_서버켜기.bat`이 자동으로 만들고 메모장을 열어 줍니다.)
3. `1_서버켜기.bat` 실행 → 첫 실행 시 Python 설치(없을 때)·가상환경·패키지·임베딩 색인을
   자동으로 준비한 뒤 서버를 켭니다. `Application startup complete`가 보이면 정상입니다.
4. `2_공개주소켜기.bat` 실행 → cloudflared가 없으면 자동으로 내려받고 공개 주소를 띄웁니다.
5. (선택) `3_제출전점검.bat`으로 채점 URL 셀프 점검.

## 수동 실행 (Windows PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# .env 에 키 입력 (.env.example 참고)
#   UPSTAGE_API_KEY=up-...
#   LAW_OC=본인OC            # open.law.go.kr 신청, 미설정 시 'test'

.\.venv\Scripts\python.exe -X utf8 fetch_ordinances.py   # 1. 조례 6종 수집 → data/ordinances.json (이미 포함)
.\.venv\Scripts\python.exe -X utf8 parse_annex.py        # 2. 별표(요금표)를 Document Parse로 읽기 → data/annex/
.\.venv\Scripts\python.exe -X utf8 build_index.py        # 3. 조각 생성 + 임베딩 → data/vectors_paragraph.npy
.\.venv\Scripts\python.exe -X utf8 check.py --retrieval  # 4. 검색 점검 (키 없이도 동작)
.\.venv\Scripts\python.exe -X utf8 check.py              # 5. 로컬 전체 셀프 채점

.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000   # 6. 제출용 API
.\.venv\Scripts\python.exe -X utf8 check.py --url http://localhost:8000/ask # (새 창) 서버 채점
```

> 한글이 깨지면 `python` 대신 `-X utf8`을 붙이세요.
> 이 저장소는 `data/ordinances.json`과 조각(chunks)을 포함합니다. 임베딩(`vectors_*.npy`)은 제외되어 있어 색인을 다시 생성해야 합니다. 별표 파싱 결과는 현재 `data/annex/`에 Markdown 10개가 포함되어 있고, 첫 색인 생성 시 `1_서버켜기.bat`이 `parse_annex.py`를 실행해 누락된 파일을 추가로 변환합니다. Document Parse에 실패한 별표는 색인 생성 시 원문 텍스트로 대체될 수 있으므로 로그를 확인하세요.

## 최신 자체 점검 결과 (2026-11-13)

아래 수치는 팀에서 공유한 로컬 제출 전 점검 로그 기준입니다. 공개 저장소의 코드만으로 동일 환경에서 재실행한 결과는 아닙니다.

| 항목 | 측정 결과 | 해석 |
|---|---:|---|
| 단위 테스트 | 32/32 통과* | 공유받은 별도 로컬 로그 기준 |
| 검색 근거 회수율 | 15/15 = 100% | 정답 근거가 검색 Top-6에 포함된 비율이며 최종 답변 정답률은 아님 |
| 일반 API 문항 | 인용 점수 15/15 = 100% | 셀프채점기의 조·항 인용 평가 기준 |
| 함정 문항 | 2/3 = 66.7% | T3에서 확인 불가 처리 실패 |
| JSON 계약 | 18/18 = 100% | 필수 응답 형식 통과 |
| 요청 오류 | 0건 | 해당 점검 실행 구간 기준 |
| 응답 시간 | 약 0.03~5.35초 | 18문항 실행 로그 기준 |

**남은 결함 — T3 함정 문항:** 3개 함정 질문 중 1개가 확인 불가로 처리되지 않았고, 청년 기본 조례 제10조 제8항·제20조 제2항·제10조 제7항이 인용됐습니다. 질문 원문과 답변 전문을 확인하기 전에는 원인을 단정하지 않습니다. 해당 결과 JSON과 T3 API 응답 전문을 확인해 재현한 뒤 수정해야 합니다.

> **점수 해석·재현성 주의:** `check.py`의 일반 문항 점수는 조·항 인용 점수입니다. 답변 본문의 모든 사실관계를 검증하는 지표가 아닙니다. 검색 회수율 역시 최종 답변 정확도와 다릅니다. 위 결과를 “전체 답변 100% 정답”으로 표현하지 않습니다. 또한 현재 공개 저장소의 `sample_questions.json`은 11문항(T1·T2 함정 2문항)입니다. 위 18문항 점검은 별도의 로컬 문항 세트로 실행한 결과이며, 그 문항 세트와 결과 JSON은 현재 저장소에 포함되어 있지 않아 공개 저장소만으로는 동일한 18문항 검증을 재현할 수 없습니다. 32개 단위 테스트의 테스트 코드와 결과 파일도 공개 저장소에서 확인되지 않아 공개 상태로는 재현할 수 없습니다.

## 제출 전 체크리스트

- [ ] T3 함정 질문 원문·응답 전문 및 18문항 테스트 세트 저장/공유
- [ ] 외부 공개 URL에서 `GET /health`와 `POST /ask`를 각각 확인
- [ ] 제출 환경에서 API 서버와 터널 프로세스 지속 실행 확인
- [ ] Quick Tunnel 재시작 후 공개 URL 변경 여부 확인
- [ ] `.env` 및 실제 API 키가 커밋되지 않았는지 확인
- [ ] 6종 조례 전체 질문의 조·항·시행일·원문 링크 대조

## 파일 구성

| 파일 | 역할 |
|---|---|
| `fetch_ordinances.py` | 법제처 API로 기준일 당일 또는 이전 시행본 선택, 조·항 분리, 자치구 조례 제외 |
| `parse_annex.py` | 별표(요금표)를 Upstage Document Parse로 읽어 표 구조 보존 |
| `build_index.py` | 조각마다 인용 라벨 부여 + Solar Embedding 색인 |
| `rag.py` | 검색 → Solar 답변 → **검증** → 대회 JSON 생성 |
| `upstage.py` | Upstage API (Chat / Embedding / Document Parse / 근거검증) |
| `app.py` | `POST /ask` FastAPI 서버 (90초 타임아웃·오류 방어) |
| `check.py` | 공개 예시 기반 셀프 채점 |
| `config.py` | 대상 조례·모델명·시연 스위치 |
| `connectivity_test.py` | Chat/Embedding/근거검증/법제처 API 연동 점검 (키 미노출) |

## 개발자 테스트

전체 회귀 테스트( unittest·pytest 스타일 포함)는 `pytest -q`로 실행합니다. 구문 점검은 `python -m compileall -q .`로 실행합니다.

## 베이스라인 대비 변경점

1. **근거 검증 모델 교체 (필수)** — 기존 `groundedness-check` 전용 모델은 Upstage API
   서빙이 종료되어(2026-08-12) 400 오류를 반환합니다. 그대로 두면 `USE_VERIFY=1`에서 모든
   답변이 '확인 불가'로 떨어져 **근거 정확도 0점**이 됩니다. 이를 **현행 Solar Chat(solar-pro4)에
   판정 프롬프트를 주는 LLM 기반 검증**으로 교체했습니다(`upstage.py: groundedness`).
   반환값(grounded / notGrounded / notSure)과 호출부(`rag.py`)는 그대로 호환됩니다.
2. **연동 점검 스크립트 추가** — `connectivity_test.py`로 Chat·Embedding·근거검증·법제처
   API 4종을 한 번에 점검(키 값은 출력하지 않음).

## 설계 원칙 (점수와 직결)

1. **LLM은 근거 번호(`evidence_ids`)만 고른다.** `citation` 문자열은 코드가 조각 라벨로
   생성 → 조항 표기 실수·환각 차단.
2. **`source_url`·`effective_date`는 코드가 채운다.** 시행 버전 고정 법제처 링크 + API 시행일.
3. **의심스러우면 '확인 불가'.** 검색 점수가 `MIN_SCORE` 미만이거나 검증이 `notGrounded`면
   답하지 않는다.

## 모델

- Chat: `solar-pro4`
- Embedding: `solar-embedding-2-query`(질문) / `solar-embedding-2-passage`(조문)
- 근거검증: `solar-pro4` 기반 LLM 판정
- 문서파싱: `document-parse`

## 보안

- `UPSTAGE_API_KEY`·`LAW_OC`는 `.env`에만 두며 `.gitignore`로 커밋 제외.
- 키를 화면·로그·채팅에 노출하지 말 것. 노출 시 즉시 재발급(rotate).

## 한계

- 조문이 많은 대형 법령은 응답이 느려질 수 있음(90초 제한 주의).
- 근거검증은 전용 API가 아닌 Solar Chat 기반 판정이라 완벽하지 않음.
- 법률 자문이 아닌 **법령 정보 안내**이며 법적 효력이 없음.

## API 안정성 설정

`.env`에서 아래 값을 조정할 수 있습니다. 기본값은 대회 문항의 90초 제한을 고려해 보수적으로 설정되어 있습니다.

| 설정 | 기본값 | 설명 |
|---|---:|---|
| `API_TIMEOUT_SEC` | 25 | Upstage 개별 요청 timeout (초) |
| `API_MAX_CONCURRENCY` | 4 | Upstage API 동시 호출 제한 |
| `API_RETRY_ATTEMPTS` | 3 | 429·5xx·timeout·connection 오류 재시도 횟수 |
| `API_RETRY_MAX_DELAY_SEC` | 8 | 재시도 대기시간 상한 |
| `SERVER_MAX_CONCURRENCY` | 3 | `/ask` 및 `/ui/ask` 동시 처리 제한 |

재시도는 `Retry-After` 헤더가 있으면 우선 반영하며, 400 등 비일시적 오류는 반복하지 않습니다.
`/health`에서 동시 처리 제한 설정을 확인할 수 있습니다.

## 최종 로컬 검증

- `python -m compileall -q .`
- `pytest -q`
- `python -X utf8 check.py --retrieval` (API key/vector 없이 BM25 검색만 평가)
- `python evaluate_compound.py` (15문항 수동 하위검색어 기반 검색 평가)

현재 검증은 offline retrieval/코드 단위 테스트입니다. Upstage API 실제 호출, LLM 답변 정확도,
근거 검증 품질 및 대회 채점 점수는 API key가 있는 환경에서 별도 확인해야 합니다.

## 시민용 AI 주거 민원 도우미 시연

- `/housing`은 기존 시민용 조례 화면의 색상·사이드바·질문/근거 패널 구성을 바탕으로 만든 별도 화면입니다.
- 첫 시연은 서울 청년월세지원 2026년 모집 공고입니다. 계약 전에는 모집 마감과 다음 공고 확인 흐름을, 계약 후에는 보증금·월세 기준 예비 대조와 신청서 또는 문의 문안 초안 흐름을 보여줍니다.
- 답변은 기존 Upstage Solar LLM API를 사용합니다. 법제처 Open API에서 서울특별시 자치법규와 관련 현행 법령을 별도로 조회해 LLM의 근거로 전달합니다.
- `/`는 독립형 AI 주거 도우미 화면이며, `/housing`은 호환 주소입니다. 화면 우측 상단 API 설정에서 Upstage 키와 법제처 OC를 연결할 수 있습니다. 인증정보는 로컬 서버 메모리에만 유지되고 재시작하면 사라집니다. 주거 도우미 설정과 상담은 localhost에서만 허용합니다.
- `/ui/housing/ask`는 시민용 기능입니다. 대회 채점용 `/ask`의 6종 조례, 2026-10-01 시행 기준, 4필드 JSON 응답 계약은 변경하지 않았습니다.
- 금액 계산은 2026 공고의 주거비 기준에 대한 예비 대조입니다. 모든 소득·재산·주택소유·중복수혜 요건을 판정하지 않으며, 모집은 마감된 상태입니다. 2027 일정과 기준은 새 공고를 확인해야 합니다.
- 화면은 LLM으로 전송되는 입력과 민감정보 입력 금지를 알립니다. 주거 상담 요청 본문은 서버에 저장하거나 로그에 남기지 않습니다.

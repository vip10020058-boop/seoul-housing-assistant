
"""AI 서울 주거도우미 서버.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
호출:  POST /ask  {"question": "장애인 주차요금 감면되나요?"}

채점은 운영진이 마감 직후 1회 실행합니다. 문항당 90초를 넘기거나 오류가 나면 0점입니다.
그래서 어떤 경우에도 정해진 JSON을 돌려주도록 감쌌습니다.
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from fastapi import Request
import config
import rag

app = FastAPI(title="AI 주거 도우미")
log = logging.getLogger("uvicorn.error")
_request_semaphore = asyncio.Semaphore(max(1, config.SERVER_MAX_CONCURRENCY))

WEB_INDEX = config.ROOT / "web" / "index.html"
HOUSING_INDEX = config.ROOT / "web" / "housing.html"


class Ask(BaseModel):
    question: str


def _valid_question(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= config.MAX_QUESTION_LENGTH


@app.get("/", response_class=HTMLResponse)
def home():
    """Default product UI: standalone AI housing assistant."""
    if HOUSING_INDEX.exists():
        return FileResponse(HOUSING_INDEX)
    return HTMLResponse("<h1>AI 주거 도우미</h1><p>주거지원 안내를 준비 중입니다.</p>")


@app.get("/housing", response_class=HTMLResponse)
def housing_page():
    """Compatibility URL for the standalone housing assistant."""
    return FileResponse(HOUSING_INDEX)


@app.get("/ui/housing/status")
def housing_status():
    import housing_service
    return housing_service.connection_status()


@app.post("/ui/housing/connect")
async def housing_connect(req: Request):
    try:
        body = await req.json()
        if not isinstance(body, dict):
            return {"error": "API 설정 값을 확인해 주세요."}
        import housing_service
        return await asyncio.to_thread(housing_service.connect_apis,
                                       str(body.get("upstage_api_key", "")).strip(),
                                       str(body.get("law_oc", "")).strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("housing API connection setup failed: %s", type(exc).__name__)
        return {"error": "API 연결을 확인하지 못했습니다. 키와 OC 값을 다시 확인해 주세요."}


@app.post("/ui/housing/ask")
async def housing_ask(req: Request):
    """Separate housing-support flow using Solar and live official law API references."""
    try:
        body = await req.json()
        question = body.get("question") if isinstance(body, dict) else None
        if not _valid_question(question):
            return {"error": "질문을 입력해 주세요.", "answer": ""}
        import housing_service
        async with _request_semaphore:
            return await asyncio.wait_for(
                asyncio.to_thread(housing_service.answer, body),
                timeout=config.ANSWER_TIMEOUT_SEC,
            )
    except asyncio.TimeoutError:
        return {"error": "응답 시간이 길어져 안내를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.", "answer": ""}
    except Exception as e:  # noqa: BLE001
        log.warning("housing assistant failed: %r", e)
        return {"error": "안내 생성 중 오류가 발생했습니다. 공식 공고 원문을 확인해 주세요.", "answer": ""}


@app.get("/health")
def health():
    index_exists = (config.DATA / f"vectors_{config.CHUNK_UNIT}.npy").exists()
    key_present = bool(config.UPSTAGE_API_KEY)
    return {"ok": True, "chunk_unit": config.CHUNK_UNIT, "verify": config.USE_VERIFY,
            "max_concurrency": config.SERVER_MAX_CONCURRENCY, "api_max_concurrency": config.API_MAX_CONCURRENCY,
            "api_key_configured": key_present, "vector_index_exists": index_exists,
            "law_api_configured": bool(config.LAW_OC), "law_api_test_key": config.LAW_OC == "test",
            "startup_requirements_present": bool(key_present and index_exists)}


@app.post("/ask")
async def ask(req: Request):
    """Always return exactly four competition fields, including on malformed JSON."""
    try:
        # Accept direct unit-test invocation as well as real ASGI Request objects.
        body = req.model_dump() if isinstance(req, Ask) else await req.json()
        question = body.get("question") if isinstance(body, dict) else None
        if not _valid_question(question):
            return dict(rag.UNKNOWN)
        async with _request_semaphore:
            return await asyncio.wait_for(asyncio.to_thread(rag.answer, question.strip()),
                                       timeout=config.ANSWER_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001  시간 초과·API 오류 모두 '확인 불가'로 응답
        log.warning("ask failed: %r", e)
        return dict(rag.UNKNOWN)

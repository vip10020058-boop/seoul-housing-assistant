"""Upstage API 호출을 한곳에 모았습니다. (Chat · Embedding · Document Parse · Groundedness Check)

Solar는 OpenAI SDK와 호환됩니다. base_url만 https://api.upstage.ai/v1 로 바꾸면 됩니다.
"""
import json
import time
import threading

import requests

import config

_client = None
_api_semaphore = threading.BoundedSemaphore(max(1, config.API_MAX_CONCURRENCY))
_client_lock = threading.Lock()


def client(api_key=None):
    # A housing-session key is kept in memory and must not replace the competition .env client.
    if api_key and api_key != config.UPSTAGE_API_KEY:
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url=config.UPSTAGE_BASE_URL,
                      timeout=config.API_TIMEOUT_SEC, max_retries=0)
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                if not config.UPSTAGE_API_KEY:
                    raise RuntimeError("UPSTAGE_API_KEY 미설정: 서버는 채점형 확인 불가 JSON을 반환합니다.")
                from openai import OpenAI
                _client = OpenAI(api_key=config.UPSTAGE_API_KEY, base_url=config.UPSTAGE_BASE_URL,
                                timeout=config.API_TIMEOUT_SEC, max_retries=0)
    return _client


def _retry(fn, tries=None):
    """Bounded retry for transient API errors with Retry-After support."""
    tries = max(1, tries or config.API_RETRY_ATTEMPTS)
    for attempt in range(tries):
        try:
            with _api_semaphore:
                return fn()
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            response = getattr(exc, "response", None)
            if status is None and response is not None:
                status = getattr(response, "status_code", None)
            transient = status == 429 or (isinstance(status, int) and 500 <= status <= 599)
            name = type(exc).__name__.lower()
            transient = transient or any(token in name for token in ("timeout", "connection", "temporarily"))
            if not transient or attempt >= tries - 1:
                raise
            headers = getattr(response, "headers", {}) if response is not None else {}
            retry_after = headers.get("Retry-After") if headers else None
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = 2 ** attempt
            time.sleep(max(0.0, min(delay, config.API_RETRY_MAX_DELAY_SEC)))


def chat(messages, response_format=None, temperature=0.0, reasoning_effort=None, api_key=None, max_tokens=None):
    kw = dict(model=config.CHAT_MODEL, messages=messages, temperature=temperature)
    if response_format:
        kw["response_format"] = response_format
    if reasoning_effort:                      # solar-pro4: 생략하면 추론 OFF, "low" 이상이면 ON
        kw["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        kw["max_tokens"] = max_tokens
    api_client = client(api_key=api_key)
    resp = _retry(lambda: api_client.chat.completions.create(**kw))
    return resp.choices[0].message.content


def embed(texts, model):
    out = []
    for i in range(0, len(texts), 50):        # 한 번에 너무 많이 보내지 않습니다
        batch = texts[i:i + 50]
        resp = _retry(lambda: client().embeddings.create(model=model, input=batch))
        out.extend(d.embedding for d in resp.data)
    return out


def document_parse(path):
    """Document Parse: 문서를 구조가 살아 있는 HTML/Markdown으로 바꿉니다. 표가 표로 남습니다."""
    url = f"{config.UPSTAGE_BASE_URL}/document-digitization"
    headers = {"Authorization": f"Bearer {config.UPSTAGE_API_KEY}"}
    with open(path, "rb") as f:
        resp = requests.post(url, headers=headers, timeout=300,
                             files={"document": f},
                             data={"model": "document-parse",
                                   "output_formats": json.dumps(["html", "markdown"])})
    resp.raise_for_status()
    content = resp.json().get("content", {})
    return content.get("markdown") or content.get("html") or content.get("text") or ""


_GROUND_SYSTEM = """너는 사실 검증기다. [근거]에 적힌 내용만으로 [답변]의 핵심 주장이 뒷받침되는지 판정한다.
판정 기준:
- grounded: 답변의 핵심 사실(금액·비율·나이·대상·요건·가부)이 근거에서 확인된다.
  표현이 다르거나, 일반적 배경 설명·주의문구가 덧붙어도 핵심이 근거와 일치하면 grounded로 본다.
  "중복되지 않는다"는 금지·중복배제 조건이 조문에 명확히 적힌 경우에만 인정한다.
  "그런 규정은 없다/과태료가 없다" 같은 부재 주장은 일부 검색 결과만으로 증명되지 않는다.
  질문이 묻는 특정 행위에 다른 행위의 제재 조항을 인용했다면 notSure 또는 notGrounded로 본다.
- notGrounded: 답변의 핵심 사실이 근거와 명백히 어긋난다(틀린 숫자·반대 결론 등).
- notSure: 근거가 질문의 쟁점과 무관해 참/거짓을 가릴 수 없다.
의심스러우면 notGrounded보다 notSure를 택한다.
반드시 grounded, notGrounded, notSure 중 하나의 단어만 출력한다."""

_GROUND_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "groundedness",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["grounded", "notGrounded", "notSure"]},
            },
            "required": ["verdict"],
            "additionalProperties": False,
        },
    },
}


def groundedness(context, answer):
    """답변이 근거(context)에 실제로 기반하는지 Solar Chat으로 판정합니다.

    과거 전용 모델 'groundedness-check'는 2026-08-12로 API 서빙이 종료되어,
    현행 Chat 모델(solar-pro4)에 판정 프롬프트를 주는 LLM 기반 검증으로 대체합니다.
    반환값은 기존과 동일: grounded / notGrounded / notSure
    """
    raw = chat(
        [{"role": "system", "content": _GROUND_SYSTEM},
         {"role": "user", "content": f"[근거]\n{context}\n\n[답변]\n{answer}"}],
        response_format=_GROUND_SCHEMA)
    try:
        return json.loads(raw).get("verdict", "notSure")
    except (json.JSONDecodeError, TypeError):
        # 형식이 깨져도 서버가 죽지 않도록 안전하게 보류 처리
        return "notSure"

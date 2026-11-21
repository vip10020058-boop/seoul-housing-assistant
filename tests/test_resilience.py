import asyncio
import threading

import app
import upstage


class FakeResponse:
    def __init__(self, headers=None):
        self.headers = headers or {}


class FakeApiError(Exception):
    def __init__(self, status_code, headers=None):
        super().__init__(f"API status {status_code}")
        self.status_code = status_code
        self.response = FakeResponse(headers)



def test_retry_retries_429_and_respects_retry_after(monkeypatch):
    sleeps = []
    calls = {"n": 0}
    monkeypatch.setattr(upstage.time, "sleep", lambda seconds: sleeps.append(seconds))


    def operation():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeApiError(429, {"Retry-After": "1.5"})
        return "ok"


    assert upstage._retry(operation, tries=2) == "ok"
    assert calls["n"] == 2
    assert sleeps == [1.5]



def test_retry_does_not_retry_non_transient_errors(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(upstage.time, "sleep", lambda _: None)


    def operation():
        calls["n"] += 1
        raise FakeApiError(400)


    try:
        upstage._retry(operation, tries=3)
    except FakeApiError:
        pass
    else:
        raise AssertionError("Expected API error")
    assert calls["n"] == 1



def test_retry_retries_server_error_with_bounded_backoff(monkeypatch):
    sleeps = []
    calls = {"n": 0}
    monkeypatch.setattr(upstage.time, "sleep", lambda seconds: sleeps.append(seconds))


    def operation():
        calls["n"] += 1
        if calls["n"] < 3:
            raise FakeApiError(503)
        return 7


    assert upstage._retry(operation, tries=3) == 7
    assert calls["n"] == 3
    assert sleeps == [1, 2]



def test_ask_endpoint_preserves_four_field_contract(monkeypatch):
    monkeypatch.setattr(app.rag, "answer", lambda question: {
        "answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""
    })
    response = asyncio.run(app.ask(app.Ask(question="테스트 질문")))
    assert set(response) == {"answer", "citation", "source_url", "effective_date"}


def test_two_scoring_http_requests_can_run_concurrently(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient

    barrier = threading.Barrier(2, timeout=3)
    monkeypatch.setattr(app.config, "SERVER_MAX_CONCURRENCY", 2)

    def score(question):
        barrier.wait()
        return {
            "answer": question,
            "citation": ["서울특별시 동물보호 조례 제29조 제3항"],
            "source_url": "https://www.law.go.kr/ordinInfoP.do?ordinSeq=2171829",
            "effective_date": "2026-10-01",
        }

    monkeypatch.setattr(app.rag, "answer", score)
    with TestClient(app.app) as client, ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda q: client.post("/ask", json={"question": q}), ["첫 질문", "둘째 질문"]))
    assert [response.status_code for response in responses] == [200, 200]
    assert [response.json()["answer"] for response in responses] == ["첫 질문", "둘째 질문"]
    assert all(set(response.json()) == set(app.rag.UNKNOWN) for response in responses)


def test_queue_wait_is_included_in_scoring_deadline(monkeypatch):
    monkeypatch.setattr(app.config, "ANSWER_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(app.config, "SERVER_MAX_CONCURRENCY", 1)

    async def score_with_full_queue():
        limiter = app._limiter("scoring")
        await limiter.acquire()
        try:
            return await asyncio.wait_for(app.ask(app.Ask(question="대기 중인 질문")), timeout=1)
        finally:
            limiter.release()

    assert asyncio.run(score_with_full_queue()) == app.rag.UNKNOWN


def test_api_call_semaphore_caps_concurrent_operations(monkeypatch):
    import threading
    import time

    limiter = threading.Semaphore(1)
    monkeypatch.setattr(upstage, "_api_semaphore", limiter)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def operation():
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return True

    threads = [threading.Thread(target=lambda: upstage._retry(operation, tries=1)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert state["max_active"] == 1


def test_answer_pipeline_rejects_out_of_range_evidence_ids(monkeypatch):
    import json
    import rag

    hit = {
        "label": "서울특별시 청년 기본 조례 제3조",
        "text": "예시 근거 내용입니다.",
        "law": "서울특별시 청년 기본 조례",
        "source_url": "https://www.law.go.kr/ordinInfoP.do?ordinSeq=2054497",
        "effective_date": "2025-07-14",
        "score": 0.8,
        "cos": None,
    }
    monkeypatch.setattr(rag, "_merged_retrieve", lambda question: [hit])
    monkeypatch.setattr(rag.config, "USE_VERIFY", False)
    monkeypatch.setattr(upstage, "chat", lambda *args, **kwargs: json.dumps({
        "found": True, "answer": "근거 없는 답변", "evidence_ids": [99]
    }, ensure_ascii=False))
    result = rag.answer("근거가 있나요?")
    assert result == rag.UNKNOWN

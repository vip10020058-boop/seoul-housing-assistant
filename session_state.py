"""Small ephemeral citizen-UI conversation state. Grading /ask deliberately stays stateless.

No raw personal information is persisted to disk or sent to a third-party store.
State expires and is scoped to a cryptographically random unguessable session ID.
"""
import re
import secrets
import threading
import time

_LOCK = threading.RLock()
_STATES = {}
_TTL = 30 * 60
_MAX_SESSIONS = 300


def _trim():
    now = time.monotonic()
    for k, v in list(_STATES.items()):
        if now - v["updated"] > _TTL:
            _STATES.pop(k, None)
    if len(_STATES) >= _MAX_SESSIONS:
        oldest = sorted(_STATES, key=lambda k: _STATES[k]["updated"])
        for k in oldest[:len(_STATES) - _MAX_SESSIONS + 1]:
            _STATES.pop(k, None)


def get_or_create(session_id=None):
    with _LOCK:
        _trim()
        if isinstance(session_id, str) and session_id in _STATES:
            s = _STATES[session_id]
            s["updated"] = time.monotonic()
            return session_id, s["history"][:]
        session_id = secrets.token_urlsafe(24)
        _STATES[session_id] = {"updated": time.monotonic(), "history": []}
        return session_id, []


def rewrite_question(question, history):
    """Resolve simple article references deterministically; don't fabricate unknown laws.

    This is UI conversational query rewriting, not a claim that an LLM verified the law.
    """
    question = question.strip()
    if not history or not question:
        return question
    prev = history[-1]
    labels = prev.get("citation", [])
    # A follow-up to an ungrounded answer must never inherit a guessed statute.
    if not labels or prev.get("answer", "").startswith("확인 불가"):
        return question
    cited = labels[0]
    m = re.match(r"(.+? 조례)\s+(제\d+조(?:의\d+)?)(?:\s+제\d+항)?$", cited)
    if not m:
        return question
    law, article = m.groups()
    # Explicit new domain overrides earlier context.
    if re.search(r"다른\s*(?:조례|법령)|아까와\s*다른|주제를\s*바꿔", question):
        return question
    # Replace only follow-up/deictic requests; a standalone new question is unchanged.
    has_short_reference = re.search(r"그럼|그러면|그것|그건|그 조례|해당\s*조례|그\s*조|같은\s*조", question)
    para_only = re.search(r"제\d+항", question) and not re.search(r"제\d+조", question)
    if not (has_short_reference or para_only):
        return question
    return f"{law} {article} 관련하여, {question}"


def record(session_id, question, response):
    with _LOCK:
        item = _STATES.get(session_id)
        if not item:
            return
        item["updated"] = time.monotonic()
        item["history"].append({
            "question": question[:2000],
            "answer": str(response.get("answer", ""))[:3000],
            "citation": list(response.get("citation", []))[:3],
        })
        item["history"] = item["history"][-6:]


def clear(session_id):
    with _LOCK:
        return _STATES.pop(session_id, None) is not None

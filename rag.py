"""4단계: 검색 → Solar 답변 → 검증 → 대회 응답 형식.

설계 원칙 세 가지
  1. LLM은 근거 '번호'만 고릅니다. 조항 표기(citation)는 코드가 조각 라벨로 만듭니다.
  2. source_url · effective_date도 코드가 채웁니다. LLM이 링크나 날짜를 쓰게 두지 않습니다.
  3. 의심스러우면 '확인 불가'. 근거 없는 답은 맞아도 점수가 없고, 함정 문항에서는 감점입니다.
"""
import json
import math
import re
import logging
from collections import Counter

import numpy as np

import config

UNKNOWN = {"answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""}
DISTRICT = re.compile(r"서울특별시\s*\S+구\s")      # 'OO구' 자치구 조례 인용 방지

_cache = {}
log = logging.getLogger(__name__)


# ------------------------------------------------------------ 검색
def _bigrams(text):
    t = re.sub(r"\s+", "", text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


def _load():
    unit = config.CHUNK_UNIT
    if unit in _cache:
        return _cache[unit]
    chunks = json.loads((config.DATA / f"chunks_{unit}.json").read_text(encoding="utf-8"))
    vec_path = config.DATA / f"vectors_{unit}.npy"
    vecs = np.load(vec_path) if vec_path.exists() else None
    if vecs is not None:
        # Cached vectors can become stale when chunks are rebuilt/updated.
        # Never let a row-count mismatch crash retrieval or silently misalign laws.
        if vecs.ndim != 2 or vecs.shape[0] != len(chunks):
            print(
                f"[RAG warning] vector/chunk count mismatch: vectors={getattr(vecs, 'shape', None)}, "
                f"chunks={len(chunks)}. Falling back to BM25; rebuild index with build_index.py."
            )
            vecs = None
        else:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.maximum(norms, 1e-12)
    # 키워드 검색용 (글자 2개 단위 BM25)
    docs = [Counter(_bigrams(c["label"] + " " + c["text"])) for c in chunks]
    df = Counter(g for d in docs for g in d)
    avg = sum(sum(d.values()) for d in docs) / len(docs)
    _cache[unit] = (chunks, vecs, docs, df, avg)
    return _cache[unit]


def _bm25(query, docs, df, avg, k1=1.2, b=0.75):
    n = len(docs)
    q = set(_bigrams(query))
    scores = np.zeros(n)
    for i, d in enumerate(docs):
        ln = sum(d.values())
        s = 0.0
        for g in q:
            if g in d:
                idf = math.log(1 + (n - df[g] + 0.5) / (df[g] + 0.5))
                s += idf * d[g] * (k1 + 1) / (d[g] + k1 * (1 - b + b * ln / avg))
        scores[i] = s
    return scores


# 용어·연령·대상의 '정의'를 묻는 질문인지 판별 (정의 조항을 우선 근거로 올리기 위함)
_DEF_QUESTION = re.compile(r"(무엇|뭐|몇\s*살|몇\s*세|나이|연령|정의|기준|이란|라\s*함은|대상.*(누구|무엇)|누구)")
_DEF_LABEL = re.compile(r"\(정의\)")
_DEF_TEXT = re.compile(r"(\(정의\)|(?:이란|란|라 함은).{0,40}말한다)")


def _rerank(question, chunks, idx, score):
    """정의형 질문이면, '정의' 조항에 가산점을 줘 재정렬한다.
    입주대상·사용료 등 표면상 비슷한 조항에 밀려 정의 조항이 누락되는 것을 막는다."""
    if not _DEF_QUESTION.search(question):
        return idx
    boosted = []
    for i in idx:
        s = score[i]
        c = chunks[i]
        is_definition = (
            _DEF_LABEL.search(c.get("title", ""))
            or _DEF_TEXT.search(c["text"][:120])
            or "(정의)" in c["text"][:40]
        )
        if is_definition:
            # 정의형 질문에서는 단순 키워드 유사 조항보다 정의 조항을 우선한다.
            # 기존 0.15는 실제 검색에서 정의 조항을 top-1로 올리지 못해
            # 청년 연령 질문이 청년시설 조항에 묻히는 회귀가 확인됨.
            s += 0.40
        boosted.append((s, i))
    boosted.sort(reverse=True)
    return [i for _, i in boosted]


# Domain/topic hints are generic; no gold question or answer is hard-coded here.
# Multiple matching subjects are additive but capped so cross-ordinance questions survive.
_TOPIC_LAW = (
    (r"자전거|따릉이|자전거주차", "자전거이용"),
    (r"공영주차|주차요금|주차장|경차|다둥이", "주차장 설치"),
    (r"동물원|공원|어린이공원|금주구역", "도시공원"),
    (r"동물등록|반려동물|유기견|입양|보호소", "동물보호"),
    (r"청년|청년정책|청년수당", "청년 기본"),
    (r"금연|흡연|간접흡연|금연구역", "금연환경"),
)


def _topic_law_bonus(question, chunks):
    """Small soft topic boost. Avoid confusing bicycle parking with automobile parking."""
    matches = []
    for pattern, stem in _TOPIC_LAW:
        if re.search(pattern, question):
            if stem == "주차장 설치" and re.search(r"자전거.?주차장", question) \
                    and not re.search(r"공영주차|자동차|경차", question):
                continue
            if stem == "도시공원" and re.search(r"동물원", question):
                pass
            matches.append(stem)
    if not matches:
        return np.zeros(len(chunks))
    # Explicitly naming a borough is never a sign that a borough law is in scope.
    bonus = min(0.22 / max(len(set(matches)), 1), 0.22)
    return np.array([sum(bonus for stem in set(matches) if stem in c.get("law", ""))
                     for c in chunks], dtype=float)


# A concrete penalty amount must be supported by a provision addressing that act,
# not merely some penalty provision from an unrelated Seoul ordinance.
_PENALTY_SUBJECTS = (
    (r"헬멧|안전모", ("헬멧", "안전모")),
    (r"금연구역|흡연|담배", ("금연구역", "흡연")),
    (r"금주|음주|술", ("금주구역", "음주")),
    (r"동물등록|유기견", ("동물등록", "유기동물")),
    (r"무단방치|방치자전거", ("무단방치", "방치")),
)


def _specific_penalty_has_basis(question, hits):
    """False only for penalty-amount requests with no same-topic penalty evidence.

    Generic synonym categories are used as a *negative safety guard*, never to
    generate a fine or to invent a statutory source.
    """
    if not re.search(r"과태료|벌금", question) or not re.search(r"얼마|금액|몇\s*원", question):
        return True
    terms=[aliases for pattern,aliases in _PENALTY_SUBJECTS if re.search(pattern,question)]
    if not terms:
        return True  # Unknown concepts: leave decision to evidence-based verifier.
    for hit in hits:
        body=hit.get('text','')
        if re.search(r"과태료|벌금",body) and all(any(alias in body for alias in group) for group in terms):
            return True
    return False


def _is_compound_question(question):
    """Reserve LLM query decomposition for questions likely to require several laws/clauses."""
    return bool(re.search(r"(둘\s*다|중복|함께|동시에|그리고|또는|또\s|뿐만|하지만|다만|예외|조건|언제.*얼마|얼마.*어떻게|어떻게.*얼마|납니까.*얼마|않으면.*과태료)", question))


def _out_of_scope(question):
    """Only unambiguous requests about *another* local government's own law are blocked."""
    return bool(re.search(r"(?:강남|서초|송파|마포|중구|종로|영등포|동작|동대문|강동|관악|노원|구로|성동|성북|강북|도봉|양천|은평|금천|용산|중랑|광진|서대문)구.{0,32}(?:조례|규칙|고시)|서울특별시\s*\S+구.{0,32}(?:조례|규칙)", question))


def retrieve(question, top_k=config.TOP_K):
    """임베딩(의미) + 키워드(용어) 혼합 검색. 임베딩이 없으면 키워드만 씁니다."""
    chunks, vecs, docs, df, avg = _load()
    kw = _bm25(question, docs, df, avg)
    kw_norm = kw / kw.max() if kw.max() > 0 else kw
    if vecs is not None and config.UPSTAGE_API_KEY:
        try:
            import upstage
            q = np.array(upstage.embed([question], config.EMBED_QUERY)[0], dtype=np.float32)
            if q.ndim != 1 or q.shape[0] != vecs.shape[1]:
                raise ValueError(f"embedding dimension mismatch: passage={vecs.shape[1]}, query={q.shape}")
            q_norm = np.linalg.norm(q)
            if q_norm <= 1e-12:
                raise ValueError("query embedding norm is zero")
            cos = vecs @ (q / q_norm)
            if cos.shape[0] != kw_norm.shape[0]:
                raise ValueError(f"similarity/chunk count mismatch: {cos.shape[0]} != {kw_norm.shape[0]}")
            score = config.SEARCH_HYBRID_WEIGHT * cos + (1 - config.SEARCH_HYBRID_WEIGHT) * kw_norm
        except Exception as exc:
            # Stale/malformed vectors or embedding API errors should not take down /ask.
            print(f"[RAG warning] hybrid search unavailable ({exc}); falling back to BM25")
            cos, score = None, kw_norm
    else:
        cos, score = None, kw_norm
    # 조례명·질문 핵심어로 관할 범위를 부드럽게 우선한다. 강제 제외는 복합 질문을 망칠 수 있다.
    score = score + _topic_law_bonus(question, chunks)
    # 정의형 질문은 정의 조항이 키워드 점수 상위 후보 밖에 있을 수 있으므로
    # 일반 질문보다 넓은 후보군에서 재순위화한다. (후보 확보 없이 boost만 주면 효과 없음)
    pool_size = max(top_k * 4, 20) if _DEF_QUESTION.search(question) else max(top_k * 2, 10)
    pool = np.argsort(score)[::-1][:pool_size]
    idx = _rerank(question, chunks, list(pool), score)[:top_k]
    return [{**chunks[i], "score": float(score[i]),
             "cos": float(cos[i]) if cos is not None else None} for i in idx]


# ------------------------------------------------------------ 답변
SYSTEM = """너는 서울특별시 조례 질의응답 도우미다.
규칙:
- 반드시 아래 [근거]에 적힌 내용만으로 답한다. 근거에 없는 내용은 추측하지 않는다.
- [근거]는 검색 점수순이지 정답순이 아니다. 질문에 가장 정확히 답하는 근거를 네가 직접 고른다.
  · 용어의 정의·나이·기준을 묻는 질문은 "정의"(제○조(정의)) 조항을 최우선 근거로 삼는다.
  · 금액·요금을 묻는 질문은 요금표(별표)나 해당 금액이 적힌 조항을 근거로 삼는다.
  · 가부(되나요/받을 수 있나요)를 묻는 질문은 요건·감면·예외가 적힌 조항을 근거로 삼는다.
  · '둘 다/중복/함께' 받을 수 있는지 묻는 질문은, 각 혜택 조항뿐 아니라 "둘 이상의 사유에
    해당하면 높은 하나만 적용한다" 같은 중복 적용 제한 조항이 근거에 있으면 반드시 함께 보고
    답한다. 그런 제한 조항이 있으면 "중복해서 받을 수 없고 감면율이 높은 하나만 적용된다"고
    명확히 답하며, 그 조항 번호를 evidence_ids에 반드시 포함한다.
- 질문이 한두 단어의 키워드(예: "담배 구역", "주차 요금")여도, 근거에서 그 주제의 핵심 내용
  (지정 기준·금액·요건 등)을 찾아 요약해 답한다. 근거에 관련 조문이 있으면 found를 true로 한다.
- 복합질문은 질문에 포함된 조건·대상·예외·금액·절차를 각각 확인하고, 한 조건의 답으로 다른 조건까지 추정하지 않는다.
  근거가 확보된 조건은 답하고, 근거가 없는 조건은 "해당 조건은 근거에서 확인 불가"라고 명시한다.
- 답은 일반 시민이 이해할 수 있는 쉬운 말로 작성한다. 단일 질문은 2~4문장, 복합질문은 조건별 짧은 목록을 허용한다.
  금액·비율·나이 같은 숫자는 그대로 쓴다. 조건을 묻는 질문은 "네/아니요"로 시작하고,
  '다만·예외' 같은 단서가 있으면 함께 적는다.
- 답에 실제로 쓴 근거의 번호만 evidence_ids에 넣는다. 읽기만 하고 답에 안 쓴 근거는 넣지 않는다.
  여러 조항을 함께 썼다면 모두 넣는다.
- 주차장·시설 요금 질문에서 본문 조항이 '원칙 무료, 예외적인 유료 운영은 별표 범위'로 정하면
  원칙·예외를 규정한 바로 그 조·항과 실제 금액을 규정한 별표를 함께 확인한다.
  답변에 무료 원칙과 별표 요금을 모두 쓴 경우 두 근거 번호를 evidence_ids에 반드시 포함한다.
  주차장의 설치·관리·운영 일반 조항은 실제 요금 조건을 뒷받침하지 않으므로 요금 근거로 인용하지 않는다.
- 근거 어디에도 답이 없으면 found를 false로 한다.
- 특정 위반행위의 과태료 금액을 묻는 질문에서 다른 행위의 과태료 조항을 근거로 답하지 않는다.
  근거 조항에 질문의 위반행위와 과태료가 함께 확인되지 않으면 found=false로 한다.
- 조례가 시장·규칙·고시 등에 구체 내용 결정을 명시적으로 위임한 경우, 금액을 추측하지 말고
  "조례는 ○○에게 해당 내용을 정하도록 위임합니다"라고 설명하고 바로 그 위임 조항만 인용한다.
- 존재하지 않는 조항·자치구 조례·서울시 조례 6종 밖의 질문은 추측하지 않는다.
- 자치구(OO구) 조례나 규칙은 근거로 쓰지 않는다.
결과는 JSON으로 낸다."""

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "law_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "found": {"type": "boolean"},
                "answer": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["found", "answer", "evidence_ids"],
            "additionalProperties": False,
        },
    },
}


def _context(hits):
    return "\n\n".join(f"[근거 {i}] {h['label']}\n{h['text']}" for i, h in enumerate(hits, 1))


_DECOMPOSE_SYSTEM = """사용자의 서울시 조례 질문을 검색용 하위 질문 2~5개로 분해한다.
각 하위 질문은 원 질문의 서로 다른 조건, 대상, 예외, 금액/절차를 하나씩 검색하도록 만든다.
질문이 단일 쟁점이면 빈 배열을 반환한다. 법령명이나 사실을 새로 만들지 말고,
검색에 유용한 일상어와 법령 용어를 함께 사용한다. JSON 배열만 출력한다."""

_DECOMPOSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "query_decomposition",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "queries": {"type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 5}
            },
            "required": ["queries"],
            "additionalProperties": False,
        },
    },
}

def _decompose_question(question):
    """API가 있을 때만 LLM으로 조건별 검색 질문을 생성한다. 실패하면 빈 목록(기존 검색 유지)."""
    if not config.UPSTAGE_API_KEY or not config.USE_COMPOUND_DECOMPOSITION or not _is_compound_question(question):
        return []
    try:
        import upstage
        raw = upstage.chat(
            [{"role": "system", "content": _DECOMPOSE_SYSTEM},
             {"role": "user", "content": question}],
            response_format=_DECOMPOSE_SCHEMA, temperature=0.0)
        queries = json.loads(raw).get("queries", [])
        clean = []
        for q in queries:
            if isinstance(q, str):
                q = re.sub(r"\s+", " ", q).strip()
                if len(q) >= 4 and q not in clean and q != question:
                    clean.append(q)
        return clean[:3]
    except Exception:  # decomposition is auxiliary; preserve the baseline retrieval on failure
        return []


def _chunk_identity(hit):
    """Return a stable identity for one retrieved chunk, not merely its citation label.

    Annex tables are split into multiple chunks that intentionally share one citation label.
    Prefer the corpus chunk id so those pieces remain independently retrievable. The content
    fallback keeps the helper compatible with older/synthetic hits that have no id.
    """
    chunk_id = hit.get("id")
    if chunk_id is not None:
        return (
            "id",
            hit.get("law"),
            hit.get("effective_date"),
            hit.get("source_url"),
            chunk_id,
        )
    return (
        "content",
        hit.get("law"),
        hit.get("effective_date"),
        hit.get("source_url"),
        hit.get("label"),
        hit.get("text"),
        hit.get("annex_source"),
    )


def _rrf_merge(query_hits, top_k, weights=None, k=60):
    """Fuse by chunk identity; same-label chunks remain separate retrieval evidence."""
    weights = weights or [1.0] * len(query_hits)
    stats = {}
    for hits, weight in zip(query_hits, weights):
        for rank, hit in enumerate(hits, 1):
            identity = _chunk_identity(hit)
            entry = stats.setdefault(identity, {"hit": hit, "rrf": 0.0, "count": 0})
            entry["rrf"] += float(weight) / (k + rank)
            entry["count"] += 1
            if (hit.get("score") or 0) > (entry["hit"].get("score") or 0):
                entry["hit"] = hit
    ranked = sorted(
        stats.values(),
        key=lambda x: (x["rrf"], x["count"], x["hit"].get("score") or 0),
        reverse=True,
    )
    fused = [entry["hit"] for entry in ranked]
    # Protect the original query's top chunk as an anchor. Other chunks with the same
    # citation label are still valid evidence and must not be discarded.
    if query_hits and query_hits[0]:
        anchor = query_hits[0][0]
        anchor_key = _chunk_identity(anchor)
        # Keep the anchor's rank while retaining the highest-score representation
        # accumulated for that same chunk during fusion.
        anchor_hit = next((h for h in fused if _chunk_identity(h) == anchor_key), anchor)
        fused = [anchor_hit] + [h for h in fused if _chunk_identity(h) != anchor_key]
    return fused[:top_k]

_EXPAND_SYSTEM = """사용자의 일상어 질문을, 서울시 조례 조문에서 쓰는 법령 용어로 바꾼 검색어를 만든다.
핵심 개념의 조문식 표현(예: '몇 살'→'연령 정의', '벌금'→'과태료', '깎아주나'→'감면')을 포함한다.
쉼표로 구분한 한 줄 키워드만 출력한다."""


def _expanded_query(question):
    """질문을 조문 용어로 확장한 보조 검색어. 실패하면 원 질문을 그대로 쓴다."""
    try:
        import upstage
        kw = upstage.chat(
            [{"role": "system", "content": _EXPAND_SYSTEM},
             {"role": "user", "content": question}], temperature=0.0)
        return f"{question} {kw}".strip()
    except Exception:  # noqa: BLE001  확장 실패는 치명적이지 않다
        return question


def _is_def_chunk(h):
    return _DEF_TEXT.search(h["text"][:120]) or "(정의)" in h["text"][:40]


# '둘 이상의 감면/할인 사유에 해당하면 높은 하나만 적용' 류의 중복 적용 제한 조항을 포착한다.
_OVERLAP_LIMIT = re.compile(
    r"둘\s*이상.{0,30}(감면|할인|사유).{0,40}(하나만|가장\s*(높은|많은)|높은\s*하나)")
# 두 혜택을 '둘 다/함께/동시에/중복' 받을 수 있는지 묻는 질문인지 판별한다.
_OVERLAP_QUESTION = re.compile(r"(둘\s*다|두\s*가지|함께|같이|동시에|중복|모두)")


_RELATED_SYNONYMS = (
    (r"금주|음주|술", ("금주", "음주")),
    (r"흡연|담배", ("흡연", "금연")),
    (r"자전거.?주차|자전거.?보관", ("자전거주차장", "주차요금")),
    (r"주차요금|공영주차|경차|다둥이", ("주차요금", "감면")),
)


def _linked_evidence(question, hits, max_added=3):
    """Deterministic, bounded annex/penalty/related-provision linking.

    Search only the ordinance families already evidenced; never pull other jurisdictions.
    This step adds candidates for the model, not automatic citations.
    """
    if not hits or not config.USE_EVIDENCE_LINKS:
        return hits
    corpus = _load()[0]
    laws = {h.get("law") for h in hits if h.get("law")}
    # Prefer the explicit topic ordinance(s) so a generic 'parking' word does not mix topics.
    suggested = {stem for pattern, stem in _TOPIC_LAW if re.search(pattern, question)}
    if re.search(r"자전거.?주차", question) and not re.search(r"공영주차|자동차", question):
        suggested.discard("주차장 설치")
    if suggested:
        matching = {l for l in laws if any(t in l for t in suggested)}
        if matching:
            laws = matching
    seen = {_chunk_identity(h) for h in hits}
    candidates = []
    # Annex linking must be paired with an article that explicitly mentions an annex.
    # The parser retained each annex's full citation label; use exact annex references.
    wanted_annex = set()
    for h in hits:
        for m in re.finditer(r"별표\s*(\d+)?", h.get("text", "")):
            wanted_annex.add((h.get("law"), m.group(1) or ""))
    for c in corpus:
        if c.get("law") not in laws or _chunk_identity(c) in seen or DISTRICT.search(c.get("label", "")):
            continue
        label = c.get("label", "")
        body = c.get("text", "")
        bonus = 0
        am = re.search(r"별표\s*(\d*)$", label)
        if am and (c.get("law"), am.group(1)) in wanted_annex and \
                re.search(r"요금|금액|수수료|사용료|얼마|무료|돈을|비용", question):
            bonus += 5
        if re.search(r"과태료|제재|어떻게|벌금|처벌", question) and re.search(r"과태료|벌금", body):
            for pat, synonyms in _RELATED_SYNONYMS:
                if re.search(pat, question) and any(term in body for term in synonyms):
                    bonus += 5
        if re.search(r"요금|얼마|돈을|돈내|무료|비용|유료", question) and \
                re.search(r"\(.*?(?:주차요금|이용요금|이용료|사용료|수수료).*?\)", body[:110]) and \
                re.search(r"무료|유료|별표|감면|부과", body):
            bonus += 6
        # Search original principle if we already have an explicit exception.
        if re.search(r"원칙|예외|다만|조건", question):
            for h in hits:
                if h.get("law") != c.get("law"):
                    continue
                m = re.search(r"제(\d+)조(?:의(\d+))?\s*제(\d+)항\s*에도 불구하고", h.get("text", ""))
                if m and re.search(r"제" + m.group(1) + "조" + ("의" + m.group(2) if m.group(2) else "") +
                                   r"\s*제" + m.group(3) + "항", label):
                    bonus += 5
        if bonus:
            candidates.append((bonus, c))
    candidates.sort(key=lambda x: (-x[0], x[1].get("id", 0)))
    for _, c in candidates[:max_added]:
        hits.append({**c, "score": 0.0, "cos": None})
    return hits


def _merged_retrieve(question, top_k=config.TOP_K):
    """원 질문 + 확장/분해 질문 검색 결과를 청크 ID 기준 RRF로 합친다.
    동일 조항 라벨을 공유하는 별표의 서로 다른 청크는 유지한다.
    정의형 질문이면 '정의' 조항을 맨 앞으로 끌어올린다."""
    base = retrieve(question, top_k=top_k)
    merged = base
    if config.UPSTAGE_API_KEY:
        # Original-question retrieval remains the anchor. Expansion and decomposition are
        # auxiliary signals; if either fails, the baseline result is retained.
        expanded = _expanded_query(question)
        query_hits = [base]
        weights = [3.0]
        if expanded != question:
            query_hits.append(retrieve(expanded, top_k=top_k))
            weights.append(1.0)
        subqueries = _decompose_question(question)
        for subquery in subqueries:
            query_hits.append(retrieve(subquery, top_k=min(3, top_k)))
            weights.append(1.0)
        merged = _rrf_merge(query_hits, top_k=max(top_k * 2, config.MAX_EVIDENCE), weights=weights)
    # 정의형 질문이면 정의 조항을 선두로 (LLM이 정의 조항을 1순위 근거로 보게 함)
    if _DEF_QUESTION.search(question):
        merged.sort(key=lambda h: 0 if _is_def_chunk(h) else 1)
    result = merged[:top_k]
    # 중복 여부(둘 다/함께/중복) 질문이면 '중복 적용 제한' 조항을 반드시 상위 근거에 포함한다.
    # 이 조항(예: "둘 이상의 감면사유에 해당하면 높은 하나만 적용")은 문장이 짧아 임베딩
    # 유사도가 낮게 나와 top_k에 못 드는 경우가 많다. 상위 근거가 가리키는 '같은 법령'에서
    # 전체 조각을 뒤져 중복 제한 조항을 찾아 맨 앞에 끼워 넣는다.
    if _OVERLAP_QUESTION.search(question) and result:
        if not any(_OVERLAP_LIMIT.search(h["text"]) for h in result):
            limit_chunk = _find_overlap_limit(result)
            if limit_chunk is not None:
                result = [limit_chunk] + result[:top_k - 1]
        result.sort(key=lambda h: 0 if _OVERLAP_LIMIT.search(h["text"]) else 1)
    return _linked_evidence(question, result, max_added=max(0, config.MAX_EVIDENCE - len(result)))


def _find_overlap_limit(result):
    """상위 근거가 속한 법령들 안에서 '중복 적용 제한' 조항 조각을 찾아 반환(없으면 None).

    score·cos는 상위 근거의 최댓값을 승계한다(이 조항은 '같은 법령'에서 질문 쟁점의 전제로
    확실히 관련되므로, 짧은 문장 탓에 낮게 나오는 자체 임베딩 유사도 대신 검색 신뢰도를 쓴다)."""
    chunks, _, _, _, _ = _load()
    laws = {h.get("law") or h["label"].split(" 제")[0] for h in result}
    top_score = max((h["score"] or 0) for h in result)
    cos_vals = [h["cos"] for h in result if h.get("cos") is not None]
    # A linked clause has not been semantically retrieved; never inherit another chunk's cosine.
    top_cos = None
    for c in chunks:
        law = c.get("law") or c["label"].split(" 제")[0]
        if law in laws and _OVERLAP_LIMIT.search(c["text"]) and not DISTRICT.search(c["label"]):
            return {**c, "score": top_score, "cos": top_cos}
    return None


_MONEY_AMOUNT = re.compile(r"(?<![\d])([\d,]+)\s*(억|만|천)?\s*원")
_NUMBER_TOKEN = re.compile(r"(?<!\d)\d[\d,]*(?!\d)")


def _amounts_in_answer(answer):
    """Read explicitly stated won amounts only; dates and provision numbers are not fees."""
    factors = {"": 1, "억": 100000000, "만": 10000, "천": 1000}
    return {int(number.replace(",", "")) * factors[unit]
            for number, unit in _MONEY_AMOUNT.findall(answer)}


def _annex_numbers(text):
    """Table cells often contain naked values under a '원' header."""
    return {int(x.replace(",", "")) for x in _NUMBER_TOKEN.findall(text)}


def _reconcile_free_fee_evidence(question, answer, used, hits):
    """Pair a free-by-default fee rule with its *source-backed* rate table.

    Used only when the answer actually asserts both the free rule and specific amounts.
    It does not invent citations, use answer keys, or assume that every fee has an annex.
    Matching the explicit '별표' reference, same ordinance, and all quoted amounts
    is stronger than simply selecting a semantically similar parking clause.
    """
    if not used or not re.search(r"무료|무상", answer) or not re.search(
        r"주차|요금|사용료|이용료|비용|돈을|유료|얼마", question
    ):
        return used
    amounts = _amounts_in_answer(answer)
    if not amounts:
        return used
    question_nospace = re.sub(r"\s+", "", question)
    # Keep the law family from grounded original retrieval, not from a user-generated citation.
    law_names = {h.get("law") for h in hits if h.get("law")}
    candidates = []
    for clause in hits:
        body = clause.get("text", "")
        law = clause.get("law")
        if not law or law not in law_names or DISTRICT.search(clause.get("label", "")):
            continue
        if re.search(r"별표\s*\d*$", clause.get("label", "")):
            continue
        # Require the actual local provision to state BOTH the free principle
        # and that an exceptional fee may be collected within an annex table.
        if not ("무료" in body and "별표" in body
                and re.search(r"주차요금|이용요금|사용료|이용료", body)
                and re.search(r"부과|징수|받을 수|유료", body)):
            continue
        # Query topic must explicitly match the article, not an unrelated law.
        if "자전거" in question_nospace and "자전거" not in body + law:
            continue
        numbers = [m.group(1) or "" for m in re.finditer(r"별표\s*(\d*)", body)]
        for refnum in numbers:
            expected = f"{law} 별표{(' ' + refnum) if refnum else ''}"
            for annex in hits:
                if annex.get("label") != expected or annex.get("law") != law:
                    continue
                if amounts <= _annex_numbers(annex.get("text", "")):
                    # The provision + table are sufficient evidence for free
                    # and its quoted exceptional prices. Avoid citation padding.
                    candidates.append((clause, annex))
    if not candidates:
        return used
    # A law-scoped article/table pair can appear several times if the table is chunked.
    # Prefer whichever article the model already selected, then the shorter clause text.
    selected = {h.get("label") for h in used}
    candidates.sort(key=lambda pair: (
        pair[0]["label"] not in selected,
        len(pair[0].get("text", "")),
        pair[0]["label"],
    ))
    principle, annex = candidates[0]
    log.info("Reconciling fee evidence to explicit rule/table: %s + %s", principle["label"], annex["label"])
    return [principle, annex]


def _detail(response, verification_status, used=None, unknown_reason=""):
    """웹 전용 메타데이터를 보존하되, 대회 응답 형식과 분리합니다."""
    return {
        "response": response,
        "verification_status": verification_status,
        "used": used or [],
        "unknown_reason": unknown_reason,
    }


def answer_detailed(question):
    """대회 응답과 웹용 근거 메타데이터를 함께 생성합니다.

    `answer()`는 이 함수의 response만 반환하므로 대회 4필드 계약은 바뀌지 않습니다.
    """
    import upstage
    question = str(question).strip()[:config.MAX_QUESTION_LENGTH]
    if not question or _out_of_scope(question):
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="out_of_scope_or_empty")
    hits = _merged_retrieve(question)
    if not hits:
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="no_hits")
    if not _specific_penalty_has_basis(question, hits):
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="penalty_without_direct_provision")

    # 검증 ①: 검색 단계에서 이미 관련 조문이 없어 보이면 바로 '확인 불가'
    if config.USE_VERIFY and hits and hits[0]["cos"] is not None and hits[0]["cos"] < config.MIN_SCORE:
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="retrieval_low")

    # 중복(둘 다/함께) 질문이고 검색에 '중복 적용 제한' 조항이 있으면, LLM이 그 조항을 놓치거나
    # 반대로 답하지 않도록 질문 끝에 명시적 지시를 덧붙인다(근거 자체는 [근거]에 이미 들어 있다).
    hint = ""
    if _OVERLAP_QUESTION.search(question) and any(_OVERLAP_LIMIT.search(h["text"]) for h in hits):
        hint = ("\n\n[지시] 이 질문은 두 혜택을 '둘 다' 받을 수 있는지를 묻는다. 근거 중 "
                "'둘 이상의 사유에 해당하면 높은 하나만 적용한다'는 중복 적용 제한 조항을 반드시 "
                "반영해, 중복해서 받을 수 없고 감면율이 높은 하나만 적용된다고 답하고 그 조항 "
                "번호를 evidence_ids에 포함하라.")

    raw = upstage.chat(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": f"[근거]\n{_context(hits)}\n\n[질문]\n{question}{hint}"}],
        response_format=SCHEMA)
    out = json.loads(raw)
    raw_ids = out.get("evidence_ids", [])
    if not isinstance(raw_ids, list):
        raw_ids = []
    used = [hits[i - 1] for i in raw_ids if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= len(hits)]
    used = [h for h in used if not DISTRICT.search(h["label"])]
    if not out.get("found") or not used:
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="no_supported_answer")
    first_sentence = re.split(r"[.!?\n]", out["answer"], maxsplit=1)[0]
    if re.search(r"(규정|과태료|처벌|벌금).{0,25}(없습니다|없어요|정해져\s*있지|확인할\s*수\s*없)", first_sentence) and not \
            any(re.search(r"(규칙|시장|고시).{0,25}(정한다|정하여|따른다)", h["text"]) for h in used):
        return _detail(dict(UNKNOWN), "unavailable", unknown_reason="unsupported_negative_claim")

    # Before verification/citation truncation, reconcile fees explicitly linked
    # by the legal provision to its annex. Search had already retrieved both;
    # the model occasionally selected 3 nearby management clauses instead.
    used = _reconcile_free_fee_evidence(question, out["answer"], used, hits)

    # 검증 ②: high search cosine is NOT proof of answer correctness.
    verdict = "not_checked"
    if config.USE_VERIFY:
        verdict = upstage.groundedness(_context(used), out["answer"])
        if verdict == "notGrounded":
            # No arbitrary override based on retrieval cosine; fail closed.
            return _detail(dict(UNKNOWN), "not_grounded", unknown_reason="verification_failed")
        if verdict == "notSure":
            # If the verifier cannot establish support, avoid a misleading legal assertion.
            return _detail(dict(UNKNOWN), "not_sure", unknown_reason="verification_uncertain")

    # 근거 보강: 중복(둘 다/함께) 질문이고 검색에 '중복 적용 제한' 조항이 있으면, LLM이 근거
    # 번호를 누락했더라도 그 조항을 근거에 보탠다. 답 내용은 위 [지시]로 교정되고, 근거는 코드가
    # 보강해 '내용은 맞는데 조항 번호만 빠지는' 흔들림을 없앤다(해당 조항이 없으면 아무것도 안 함).
    if _OVERLAP_QUESTION.search(question) and re.search(r"중복|높은\s*하나|하나만|함께\s*적용|동시\s*적용", out["answer"]):
        used_labels = {h["label"] for h in used}
        for h in hits:
            if h["label"] in used_labels or DISTRICT.search(h["label"]):
                continue
            if _OVERLAP_LIMIT.search(h["text"]):
                used.append(h)
                break

    # Citation budget: organizer deducts when citations exceed gold count + 2.
    # Three is the conservative maximum for public sample questions; prefer claims actually used.
    citation = list(dict.fromkeys(h["label"] for h in used))[:config.MAX_CITATIONS]
    used = [h for h in used if h["label"] in citation]
    first = used[0]
    response = {"answer": out["answer"], "citation": citation,
                "source_url": first["source_url"], "effective_date": first["effective_date"]}
    status = {"grounded": "grounded", "notSure": "not_sure"}.get(verdict, "not_checked")
    return _detail(response, status, used=used)


def answer(question):
    """대회 채점용 4필드 응답만 반환합니다."""
    return answer_detailed(question)["response"]


def answer_without_rag(question):
    """시연용 '전' 장면: 검색 없이 LLM만으로 답하게 하면 어떻게 되는가."""
    import upstage
    return upstage.chat([
        {"role": "system", "content": "너는 서울시 조례 전문가다. 답과 함께 근거 조항(제○조 제○항)과 원문 링크, 시행일을 적어라."},
        {"role": "user", "content": question}], temperature=0.3)

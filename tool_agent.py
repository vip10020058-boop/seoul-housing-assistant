"""Optional bounded evidence-tool router for an explanatory UI only.

The official competition /ask does NOT call this module: it answers against the
fixed October-1-2026 ordinance corpus. Optional official live checks are
clearly labelled 'reference only' and never become competition citations.
"""
import json
import logging
import re

import config
import rag

log = logging.getLogger(__name__)

TOOL_DEFS = [
    {"type":"function","function": {
        "name":"search_ordinance", "description":"Find relevant passages within the six Seoul Metropolitan Government ordinances effective on 2026-10-01. Use for questions about ordinance provisions, exceptions, fee annexes.",
        "parameters":{"type":"object","properties":{"query":{"type":"string","description":"Question or statutory search expression"}},"required":["query"],"additionalProperties":False}}},
    {"type":"function","function":{
        "name":"lookup_provision", "description":"Retrieve the verbatim passage for an exact article or annex citation from the fixed six-ordinance corpus. Do not invent labels.",
        "parameters":{"type":"object","properties":{"citation":{"type":"string","description":"Exact citation label such as 서울특별시 주차장 설치 및 관리 조례 제7조 제1항"}},"required":["citation"],"additionalProperties":False}}},
    {"type":"function","function":{
        "name":"check_official_archive", "description":"Optional official archived ordinance XML check for one of the six fixed corpus ordinances; for UI inspection only, never change grading evidence or effective date.",
        "parameters":{"type":"object","properties":{"ordinance":{"type":"string","description":"Exact ordinance name in the six-name allowlist"}},"required":["ordinance"],"additionalProperties":False}}},
]


def local_search(query):
    hits = rag.retrieve(query[:300], top_k=5)
    return [{"citation":h["label"], "excerpt":h["text"][:750], "effective_date":h.get("effective_date", ""),
             "source_url":h.get("source_url", "")} for h in hits if not rag.DISTRICT.search(h["label"])]


def local_provision(label):
    if not isinstance(label, str) or len(label)>200:
        return []
    chunks=rag._load()[0]
    return [{"citation":c["label"],"text":c["text"][:1600],"source_url":c.get("source_url", ""),
             "effective_date":c.get("effective_date", "")} for c in chunks if c["label"]==label][:4]


def official_archived_check(name):
    """The official API returns archived MST specified by locally verified data."""
    if not config.ENABLE_LIVE_LAW_TOOL:
        return {"status":"disabled", "note":"Enable ENABLE_LIVE_LAW_TOOL=1 for external reference checks."}
    if name not in config.TARGET_ORDINANCES:
        return {"status":"out_of_scope"}
    import requests
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    law = next((x for x in laws if x.get("name")==name), None)
    if not law:
        return {"status":"not_in_corpus"}
    try:
        from fetch_ordinances import SERVICE_URL
        # Fixed MST: do not silently replace October 2026 text with newer law.
        r=requests.get(SERVICE_URL, params={"OC":config.LAW_OC,"target":"ordin","ID":law["mst"],"type":"XML"}, timeout=(3,5))
        r.raise_for_status()
        from xml.etree import ElementTree as ET
        root=ET.fromstring(r.content)
        eff=root.findtext(".//시행일자")
        return {"status":"checked","archived_mst":law["mst"],"source_url":law["source_url"],
                "corpus_effective_date":law["effective_date"],"remote_effective_date":eff,
                "note":"참고 확인만 수행함. 대회용 /ask 인용은 고정 조례 데이터만 사용."}
    except Exception as ex:
        return {"status":"unavailable","reason":type(ex).__name__,"fallback":"local_corpus"}


def run_tools(question, max_calls=4):
    """A model can select evidence tools, but cannot create competition citations.

    Returns an audit trace; final grounded answer is always produced by rag.answer
    separately. If a model/provider does not support tools, retrieval fallback works.
    """
    if not config.UPSTAGE_API_KEY:
        return {"mode":"local_fallback", "trace":[{"tool":"search_ordinance", "status":"offline",
                "result":local_search(question)}]}
    import upstage
    messages=[{"role":"system","content":
      "You are a tool-routing assistant. Call tools to inspect only source materials for the user's question. "
      "Never fabricate law names or article numbers. At most 4 tool calls. Return a short summary, not legal advice. "
      "Do not show hidden reasoning or chain-of-thought."},
      {"role":"user","content":question[:config.MAX_QUESTION_LENGTH]}]
    trace=[]
    max_calls=min(max(int(max_calls),1),4)
    try:
        for _ in range(3):
            def _call():
                return upstage.client().chat.completions.create(
                    model=config.CHAT_MODEL, messages=messages, tools=TOOL_DEFS,
                    tool_choice="auto", temperature=0, timeout=min(config.API_TIMEOUT_SEC,15))
            resp=upstage._retry(_call, tries=2)
            msg=resp.choices[0].message
            calls=msg.tool_calls or []
            if not calls or len(trace)>=max_calls:
                break
            messages.append(msg.model_dump(exclude_none=True))
            for c in calls:
                if len(trace)>=max_calls:
                    # Preserve protocol consistency for any unexecuted tool call.
                    result={"status":"budget_exhausted"}
                else:
                    args={}
                    try:
                        args=json.loads(c.function.arguments or '{}')
                        if c.function.name=="search_ordinance":
                            result=local_search(str(args.get("query","")))
                        elif c.function.name=="lookup_provision":
                            result=local_provision(args.get("citation", ""))
                        elif c.function.name=="check_official_archive":
                            result=official_archived_check(args.get("ordinance", ""))
                        else:
                            result={"status":"unknown_tool"}
                    except Exception as ex:
                        result={"status":"error","type":type(ex).__name__}
                    trace.append({"tool":c.function.name,"arguments":args if isinstance(args,dict) else {},
                                  "result":result})
                messages.append({"role":"tool","tool_call_id":c.id,
                                 "content":json.dumps(result, ensure_ascii=False)[:5000]})
        return {"mode":"model_tool_calling", "trace":trace}
    except Exception as ex:
        log.warning("tool_calling_not_available: %r", ex)
        return {"mode":"local_fallback","reason":type(ex).__name__,
                "trace":[{"tool":"search_ordinance", "status":"fallback", "result":local_search(question)}]}

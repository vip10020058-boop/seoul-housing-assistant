"""Offline compound-query retrieval evaluation; evaluates original and manually decomposed queries."""
import json
from pathlib import Path
from collections import defaultdict
import rag
import config

DATA = Path(__file__).parent / "compound_questions.json"

def rank_decomposed(queries, top_k):
    # Reciprocal-rank fusion: repeated retrieval and higher ranks increase confidence.
    stats = defaultdict(lambda: {"count": 0, "rrf": 0.0, "hit": None})
    for query in queries:
        hits = rag.retrieve(query, top_k=3)
        for rank, h in enumerate(hits, 1):
            s = stats[h["label"]]
            s["count"] += 1
            s["rrf"] += 1.0 / rank
            s["hit"] = h
    return [v["hit"] for _, v in sorted(stats.items(), key=lambda kv: (kv[1]["count"], kv[1]["rrf"]), reverse=True)[:top_k]]

def evaluate(path=DATA, top_k=config.TOP_K):
    questions = json.loads(Path(path).read_text(encoding="utf-8"))
    rows=[]
    for q in questions:
        original=rag.retrieve(q["question"],top_k=top_k)
        decomposed=rank_decomposed(q["search_queries"],top_k)
        row={"id":q["id"],"type":q["type"],"question":q["question"],"expected":len(q["gold_labels"])}
        for mode,hits in [("baseline",original),("decomposed",decomposed)]:
            labels=[h["label"] for h in hits]
            found=[g for g in q["gold_labels"] if g in labels]
            row[mode]={"found":len(found),"coverage":len(found)/len(q["gold_labels"]),"missing":[g for g in q["gold_labels"] if g not in labels],"top_labels":labels}
        rows.append(row)
    expected=sum(r["expected"] for r in rows)
    result={"questions":len(rows),"top_k":top_k,"expected_citations":expected,"results":rows,"summary":{}}
    for mode in ["baseline","decomposed"]:
        got=sum(r[mode]["found"] for r in rows)
        perfect=sum(r[mode]["coverage"]==1.0 for r in rows)
        result["summary"][mode]={"retrieved":got,"coverage":got/expected if expected else 1.0,"perfect_questions":perfect,"perfect_rate":perfect/len(rows) if rows else 1.0}
    result["by_type"]={}
    for typ in sorted(set(r["type"] for r in rows)):
        group=[r for r in rows if r["type"]==typ]
        ex=sum(r["expected"] for r in group)
        result["by_type"][typ]={}
        for mode in ["baseline","decomposed"]:
            got=sum(r[mode]["found"] for r in group)
            perfect=sum(r[mode]["coverage"]==1.0 for r in group)
            result["by_type"][typ][mode]={"expected":ex,"retrieved":got,"coverage":got/ex if ex else 1.0,"perfect_questions":perfect,"question_count":len(group)}
    return result

if __name__=="__main__":
    res=evaluate()
    for mode,s in res["summary"].items(): print(f"{mode}: {s['retrieved']}/{res['expected_citations']} ({s['coverage']:.1%}), perfect {s['perfect_questions']}/{res['questions']} ({s['perfect_rate']:.1%})")
    for r in res["results"]:
        print(f"{r['id']}: baseline {r['baseline']['found']}/{r['expected']} → decomposed {r['decomposed']['found']}/{r['expected']}")
        if r['decomposed']['missing']: print('  still missing:',r['decomposed']['missing'])
    out=Path(__file__).parent/'compound_evaluation_results.json'
    out.write_text(json.dumps(res,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Saved:',out)

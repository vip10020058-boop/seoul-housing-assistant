"""Read-only evidence graph for optional demos, derived entirely from fixed local text.

The graph is explanatory metadata: only the selected grounded citations appear in
competition `/ask`. Neighbors never become answer citations without verification.
"""
import re

import rag

_REFERENCE = re.compile(r"제(\d+)조(의\d+)?(?:\s*제(\d+)항)?")
_ANNEX = re.compile(r"별표\s*(\d*)")


def related(citations, max_edges=12):
    if not citations:
        return {"nodes": [], "edges": []}
    chunks=rag._load()[0]
    known={c['label'] for c in chunks}
    by_label={}
    for c in chunks:
        by_label.setdefault(c['label'],c)
    selected=[label for label in citations if label in known and not rag.DISTRICT.search(label)]
    nodes={label: {'id':label,'primary':True,'source_url':by_label[label].get('source_url','')}
           for label in selected}
    edges=[]
    for label in selected:
        source=by_label[label]
        law=source.get('law','')
        text=source.get('text','')
        # Strip known source title to avoid treating own article heading as a reference.
        content=text.split('\n',1)[-1] if text.startswith('[') else text
        references=[]
        for m in _REFERENCE.finditer(content):
            # "법 제N조" refers to another national law, not our ordinance.
            if re.search(r'(?:법|영|규칙)\s*$',content[max(0,m.start()-12):m.start()]):
                continue
            dest=f"{law} 제{m.group(1)}조{m.group(2) or ''}"
            if m.group(3):
                dest+=f" 제{m.group(3)}항"
            references.append((dest,'예외' if '불구하고' in content[m.end():m.end()+20] else '참조',m.group(0)))
        for m in _ANNEX.finditer(content):
            references.append((f"{law} 별표 {m.group(1)}".rstrip(), '별표 참조',m.group(0)))
        for dest,relation,proof in references:
            if len(edges)>=max_edges:
                break
            if dest not in known or dest==label:
                continue
            key=(label,dest,relation)
            if any((e['from'],e['to'],e['relation'])==key for e in edges):
                continue
            entry=by_label[dest]
            nodes.setdefault(dest,{'id':dest,'primary':False,'source_url':entry.get('source_url','')})
            edges.append({'from':label,'to':dest,'relation':relation,'proof':proof})
    return {'nodes':list(nodes.values()),'edges':edges}

"""3단계: 조문을 조각(chunk)으로 나누고 임베딩해 검색 색인을 만듭니다.

조각마다 '인용 라벨'을 붙여 두는 것이 핵심입니다.
  예) 서울특별시 주차장 설치 및 관리 조례 제7조 제3항
LLM은 이 라벨을 고르기만 하고, 조항 표기를 직접 쓰지 않습니다.

실행:  python -X utf8 build_index.py             (CHUNK_UNIT=paragraph 기본)
       set CHUNK_UNIT=article & python -X utf8 build_index.py   (조 단위, 시연 '전' 장면)
결과:  data/chunks_<단위>.json, data/vectors_<단위>.npy
"""
import hashlib
import json

import numpy as np

import config
from parse_annex import annex_path



def split_annex_rows(body, limit=1800):
    """Split at newline/table row boundaries and repeat the most recent table headers.

    Never cut a Markdown table row or a cell mid-value. A row longer than limit is
    retained intact rather than split into a semantically wrong fee/value pair.
    """
    if not body.strip():
        return [body]
    lines = body.splitlines()
    current, size, output = [], 0, []
    headers, heading = [], ""
    in_table = False
    for line in lines:
        row = line.lstrip().startswith("|")
        if row and not in_table:
            headers = [line]
            # Delimiter row follows as ordinary next line; it is captured below.
        elif row and headers and len(headers) == 1 and set(line.replace("|", "").strip()) <= set(" :-"):
            headers.append(line)
        if not row:
            if line.strip() and len(line.strip()) < 80 and not line.strip().startswith("<"):
                heading = line.strip()
            if in_table:
                headers = []
        if current and size + len(line) + 1 > limit:
            output.append("\n".join(current))
            current = []
            size = 0
            if row and headers and line not in headers:
                carry = ([heading] if heading else []) + headers
                current.extend(carry)
                size += sum(len(x) + 1 for x in carry)
        current.append(line)
        size += len(line) + 1
        in_table = row
    if current:
        output.append("\n".join(current))
    return output


def make_chunks(laws, unit):
    chunks = []
    for law in laws:
        meta = {"law": law["name"], "effective_date": law["effective_date"], "source_url": law["source_url"]}
        for a in law["articles"]:
            head = f"{law['name']} {a['article']}({a['title']})"
            if unit == "article" or len(a["paragraphs"]) <= 1 or a["paragraphs"][0]["no"] is None:
                chunks.append({**meta, "label": f"{law['name']} {a['article']}",
                               "text": a["text"]})
                continue
            for p in a["paragraphs"]:
                chunks.append({**meta, "label": f"{law['name']} {a['article']} 제{p['no']}항",
                               "text": f"[{head}]\n{p['text']}"})
        for ax in law["annexes"]:
            if not ax["label"].startswith("별표"):
                continue
            parsed = annex_path(law["mst"], ax["label"])
            if config.USE_PARSED_ANNEX and parsed.exists():
                body, src = parsed.read_text(encoding="utf-8"), "document-parse"
            else:
                body, src = ax["text"], "law.go.kr 원문(괘선 표)"
            # 긴 별표는 잘라서 넣되 라벨은 같게 둡니다
            for piece in split_annex_rows(body):
                chunks.append({**meta, "label": f"{law['name']} {ax['label']}",
                               "text": f"[{law['name']} {ax['label']} {ax['title']}]\n{piece}",
                               "annex_source": src})
    for i, c in enumerate(chunks):
        c["id"] = i
    return chunks


def main():
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    unit = config.CHUNK_UNIT
    chunks = make_chunks(laws, unit)
    chunk_text = json.dumps(chunks, ensure_ascii=False, indent=1)
    (config.DATA / f"chunks_{unit}.json").write_text(chunk_text, encoding="utf-8")
    print(f"조각 {len(chunks)}개 ({unit} 단위)")

    if not config.UPSTAGE_API_KEY:
        print("⚠ API 키가 없어 임베딩은 건너뜁니다. 키워드 검색만 동작합니다.")
        return
    import upstage
    vecs = upstage.embed([c["text"] for c in chunks], config.EMBED_PASSAGE)
    np.save(config.DATA / f"vectors_{unit}.npy", np.array(vecs, dtype=np.float32))
    manifest = {"chunks_sha256": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "passage_model": config.EMBED_PASSAGE}
    (config.DATA / f"vectors_{unit}.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    print("임베딩 저장 완료")


if __name__ == "__main__":
    main()

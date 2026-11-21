import json
import numpy as np

import config
import rag


def test_stale_vector_row_count_falls_back_to_bm25(tmp_path, monkeypatch):
    chunks = [
        {"label": "서울특별시 테스트 조례 제1조", "text": "테스트 조항 첫 번째"},
        {"label": "서울특별시 테스트 조례 제2조", "text": "테스트 조항 두 번째"},
    ]
    (tmp_path / "chunks_paragraph.json").write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    # Simulate a stale cache created for only one of the two chunks.
    np.save(tmp_path / "vectors_paragraph.npy", np.ones((1, 3), dtype=np.float32))
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CHUNK_UNIT", "paragraph")
    monkeypatch.setattr(config, "UPSTAGE_API_KEY", "")
    rag._cache.clear()

    loaded_chunks, vectors, *_ = rag._load()
    assert len(loaded_chunks) == 2
    assert vectors is None
    hits = rag.retrieve("테스트 조항", top_k=1)
    assert len(hits) == 1
    assert hits[0]["label"] in {c["label"] for c in chunks}
    rag._cache.clear()

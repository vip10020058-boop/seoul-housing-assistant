import hashlib
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


def test_only_vectors_for_the_current_chunk_content_are_loaded(tmp_path, monkeypatch):
    chunks = [{"label": "서울특별시 청년 기본 조례 제3조", "text": "기존 조각"}]
    chunk_file = tmp_path / "chunks_paragraph.json"
    chunk_text = json.dumps(chunks, ensure_ascii=False)
    chunk_file.write_text(chunk_text, encoding="utf-8")
    manifest = {"chunks_sha256": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "passage_model": config.EMBED_PASSAGE}
    (tmp_path / "vectors_paragraph.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    np.save(tmp_path / "vectors_paragraph.npy", np.ones((1, 3), dtype=np.float32))
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CHUNK_UNIT", "paragraph")
    rag._cache.clear()
    assert rag._load()[1] is not None

    # A re-index can change content or order without changing the number of rows.
    chunks[0]["text"] = "갱신된 조각"
    chunk_file.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    rag._cache.clear()
    assert rag._load()[1] is None
    rag._cache.clear()

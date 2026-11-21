import unittest

import rag


class RetrievalRegressionTests(unittest.TestCase):
    def test_youth_age_definition_ranks_definition_clause_first(self):
        question = "서울시 청년 기본 조례에서 말하는 청년은 몇 살부터 몇 살까지인가요?"
        hits = rag.retrieve(question, top_k=6)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["label"], "서울특별시 청년 기본 조례 제3조")
        self.assertIn("19세 이상 39세 이하", hits[0]["text"])

    def test_definition_clause_remains_in_top_six(self):
        question = "서울시 청년 기본 조례에서 말하는 청년은 몇 살부터 몇 살까지인가요?"
        labels = [h["label"] for h in rag.retrieve(question, top_k=6)]
        self.assertIn("서울특별시 청년 기본 조례 제3조", labels)

class CompoundRetrievalTests(unittest.TestCase):
    def test_decomposition_disabled_without_api_key(self):
        original = rag.config.UPSTAGE_API_KEY
        try:
            rag.config.UPSTAGE_API_KEY = ""
            self.assertEqual(rag._decompose_question("요금과 예외를 알려주세요"), [])
        finally:
            rag.config.UPSTAGE_API_KEY = original

    def test_rrf_merge_keeps_baseline_ranked_hit(self):
        baseline = [{"label": "기준 조항", "score": 0.7}, {"label": "기타", "score": 0.5}]
        subquery = [{"label": "추가 조항", "score": 0.6}, {"label": "기타", "score": 0.4}]
        merged = rag._rrf_merge([baseline, subquery], top_k=3, weights=[3.0, 1.0])
        labels = [h["label"] for h in merged]
        self.assertIn("기준 조항", labels)
        self.assertIn("추가 조항", labels)
        self.assertIn("기타", labels)
        self.assertEqual(labels[0], "기준 조항")

    def test_rrf_merge_preserves_distinct_chunks_with_same_citation_label(self):
        label = "서울특별시 주차장 설치 및 관리 조례 별표 1"
        chunk_a = {"id": 109, "law": "서울특별시 주차장 설치 및 관리 조례", "label": label,
                   "text": "별표 1 첫 번째 구간", "score": 0.8}
        chunk_b = {"id": 110, "law": "서울특별시 주차장 설치 및 관리 조례", "label": label,
                   "text": "별표 1 두 번째 구간", "score": 0.7}

        merged = rag._rrf_merge([[chunk_a], [chunk_b]], top_k=5)

        self.assertEqual([hit["id"] for hit in merged], [109, 110])
        self.assertEqual({hit["label"] for hit in merged}, {label})

    def test_rrf_merge_combines_the_same_chunk_across_queries(self):
        chunk = {"id": 109, "law": "서울특별시 주차장 설치 및 관리 조례",
                 "label": "서울특별시 주차장 설치 및 관리 조례 별표 1",
                 "text": "별표 1 첫 번째 구간", "score": 0.8}
        duplicate = {**chunk, "score": 0.9}

        merged = rag._rrf_merge([[chunk], [duplicate]], top_k=5)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["score"], 0.9)

    def test_rrf_merge_uses_content_fallback_when_chunk_id_is_missing(self):
        label = "서울특별시 주차장 설치 및 관리 조례 별표 1"
        chunk_a = {"label": label, "text": "별표 1 첫 번째 구간"}
        chunk_b = {"label": label, "text": "별표 1 두 번째 구간"}

        merged = rag._rrf_merge([[chunk_a], [chunk_b]], top_k=5)

        self.assertEqual([hit["text"] for hit in merged], [
            "별표 1 첫 번째 구간", "별표 1 두 번째 구간"
        ])

if __name__ == "__main__":
    unittest.main()

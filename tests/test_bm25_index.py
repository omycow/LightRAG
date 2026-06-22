"""Unit tests for BM25 index and RRF fusion."""

from lightrag.bm25_index import BM25Index, reciprocal_rank_fusion


class TestBM25Index:
    def _build_sample_index(self):
        idx = BM25Index()
        idx.build(
            {
                "d1": "machine learning deep neural network",
                "d2": "natural language processing nlp text",
                "d3": "computer vision image recognition deep",
                "d4": "reinforcement learning agent policy",
                "d5": "transfer learning domain adaptation",
                "d6": "graph neural network gnn node",
            }
        )
        return idx

    def test_build_and_query(self):
        idx = self._build_sample_index()
        assert idx.is_built
        results = idx.query("machine learning", top_k=3)
        assert len(results) > 0
        assert results[0]["id"] == "d1"

    def test_empty_index(self):
        idx = BM25Index()
        assert not idx.is_built
        assert idx.query("test") == []

    def test_empty_query(self):
        idx = self._build_sample_index()
        assert idx.query("") == []

    def test_no_match(self):
        idx = self._build_sample_index()
        results = idx.query("xyznonexistent123")
        assert results == []

    def test_build_empty_docs(self):
        idx = BM25Index()
        idx.build({})
        assert not idx.is_built


class TestReciprocalRankFusion:
    def test_basic_fusion(self):
        vec = [{"id": "a", "s": 0.9}, {"id": "b", "s": 0.8}]
        bm25 = [{"id": "b", "s": 5.0}, {"id": "c", "s": 3.0}]
        fused = reciprocal_rank_fusion(vec, bm25)
        ids = [r["id"] for r in fused]
        assert ids[0] == "b"  # found in both → highest RRF
        assert set(ids) == {"a", "b", "c"}

    def test_empty_inputs(self):
        assert reciprocal_rank_fusion([], []) == []

    def test_one_empty(self):
        vec = [{"id": "a"}, {"id": "b"}]
        fused = reciprocal_rank_fusion(vec, [])
        assert [r["id"] for r in fused] == ["a", "b"]

    def test_custom_id_fields(self):
        vec = [{"entity_name": "X"}, {"entity_name": "Y"}]
        bm25 = [{"doc_id": "Y"}, {"doc_id": "Z"}]
        fused = reciprocal_rank_fusion(
            vec, bm25, vector_id_field="entity_name", bm25_id_field="doc_id"
        )
        ids = [r.get("entity_name") or r.get("doc_id") for r in fused]
        assert ids[0] == "Y"  # in both

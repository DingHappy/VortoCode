"""向量后端选择/降级测试（qdrant 未装时降级内存）。"""

from src.indexing.code_indexer import make_vector_store, VectorStore


def test_in_memory_by_default(monkeypatch):
    monkeypatch.delenv("AUTODEV_QDRANT_URL", raising=False)
    assert isinstance(make_vector_store(), VectorStore)


def test_falls_back_when_qdrant_unavailable(monkeypatch):
    # 配了 URL 但 qdrant-client 未装 → QdrantVectorStore 构造抛错 → 降级内存
    monkeypatch.setenv("AUTODEV_QDRANT_URL", "http://localhost:6333")
    assert isinstance(make_vector_store(), VectorStore)


def test_vectorstore_delete():
    vs = VectorStore()
    vs.add("a", [1.0, 0.0], {"x": 1})
    vs.add("b", [0.0, 1.0], {"x": 2})
    vs.delete("a")
    results = vs.search([1.0, 0.0], top_k=5)
    assert all(r[0] != "a" for r in results)         # a 已删
    assert any(r[0] == "b" for r in results)

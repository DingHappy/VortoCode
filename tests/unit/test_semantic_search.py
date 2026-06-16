"""语义检索测试（离线，使用词袋回退 embedder，不打网络）。"""

import json

import pytest

from src.indexing.code_indexer import CodeEmbedding, VectorStore, CodeIndexer


def test_fallback_embedding_ranks_by_word_overlap():
    """词袋回退向量：词重叠越多，余弦越高。"""
    emb = CodeEmbedding()
    store = VectorStore()
    docs = {
        "auth": "def login user authentication password session",
        "math": "def add numbers sum arithmetic compute",
        "stack": "class Stack push pop data structure",
    }
    for did, text in docs.items():
        store.add(did, emb._fallback_embedding(text), {"text": text})

    q = emb._fallback_embedding("user login authentication password")
    results = store.search(q, top_k=3)

    assert results[0][0] == "auth"               # 最相关
    assert results[0][1] > results[1][1]         # 分数单调


@pytest.mark.asyncio
async def test_code_indexer_semantic_search(tmp_path, monkeypatch):
    """CodeIndexer 全链路：索引代码 → 语义检索，最相关来自 auth 文件。"""
    emb = CodeEmbedding()

    async def _t(text):
        return emb._fallback_embedding(text)

    async def _c(code, language=None):
        return emb._fallback_embedding(code)

    # 强制走回退向量，避免任何网络调用
    monkeypatch.setattr(emb, "embed_text", _t)
    monkeypatch.setattr(emb, "embed_code", _c)

    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n"
        "    '''authenticate a user with password and start a session'''\n"
        "    return authenticate(user, password)\n"
    )
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    '''sum two numbers'''\n    return a + b\n"
    )

    indexer = CodeIndexer(str(tmp_path), embedding=emb)
    await indexer.index_repository()

    results = await indexer.search("user login authenticate password session", top_k=5)
    assert results, "应有检索结果"
    assert "auth" in json.dumps(results[0], default=str)   # 最相关来自 auth.py

"""向量索引增量/持久化测试（离线，计数式回退 embedder）。"""

import pytest

from src.indexing.code_indexer import CodeIndexer, CodeEmbedding


def _counting_embedding():
    emb = CodeEmbedding()
    calls = {"n": 0}

    async def _t(text):
        calls["n"] += 1
        return emb._fallback_embedding(text)

    async def _c(code, language=None):
        calls["n"] += 1
        return emb._fallback_embedding(code)

    emb.embed_text = _t
    emb.embed_code = _c
    return emb, calls


@pytest.mark.asyncio
async def test_incremental_skip_and_selective_reembed(tmp_path):
    emb, calls = _counting_embedding()
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("def g():\n    return 2\n")

    # 首次：全量索引
    await CodeIndexer(str(tmp_path), embedding=emb).index_repository()
    first = calls["n"]
    assert first > 0

    # 重索引（无变更）→ 全部跳过，0 次新 embed
    calls["n"] = 0
    await CodeIndexer(str(tmp_path), embedding=emb).index_repository()
    assert calls["n"] == 0

    # 改 a.py → 仅重嵌入 a.py（少于首次的总量）
    calls["n"] = 0
    (tmp_path / "a.py").write_text("def f():\n    return 42  # changed\n")
    await CodeIndexer(str(tmp_path), embedding=emb).index_repository()
    assert calls["n"] > 0
    assert calls["n"] < first


@pytest.mark.asyncio
async def test_incremental_removes_deleted_file(tmp_path):
    emb, _ = _counting_embedding()
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "b.py").write_text("def g():\n    return 2\n")
    await CodeIndexer(str(tmp_path), embedding=emb).index_repository()

    (tmp_path / "b.py").unlink()
    idx = CodeIndexer(str(tmp_path), embedding=emb)
    await idx.index_repository()

    assert not any("b.py" in fk for fk in idx.file_index)      # b 的索引已移除
    assert any("a.py" in fk for fk in idx.file_index)          # a 仍在

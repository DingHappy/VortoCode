"""长期记忆检索排序（src/memory/session_store.SessionStore.search_memories）单测。

旧版 `LIKE '%整句%'` 极脆——整条 query 须原样作子串出现；现改为**词重叠打分 + CJK 二元组**排序。
"""

from src.memory.session_store import SessionStore


def _store(tmp_path):
    s = SessionStore(db_path=str(tmp_path / "sessions.db"))
    s.add_memory("__longterm__", "fact", "跑测试用 pytest -q，别跑全量", importance=0.6)
    s.add_memory("__longterm__", "fact", "前端用 Vite + React，别引入 CRA", importance=0.6)
    s.add_memory("__longterm__", "fact", "部署走 GitHub Actions，主分支保护", importance=0.6)
    return s


def test_search_ranks_by_term_overlap_not_literal_substring(tmp_path):
    s = _store(tmp_path)
    # 旧版 `LIKE '%测试命令%'` 会 0 命中；新版按词/二元组重叠能召回「跑测试用 pytest」
    rows = s.search_memories("__longterm__", "测试命令怎么跑")
    assert rows and "pytest" in rows[0]["content"]


def test_search_english_terms(tmp_path):
    s = _store(tmp_path)
    rows = s.search_memories("__longterm__", "which frontend build tool Vite")
    assert rows and "Vite" in rows[0]["content"]


def test_search_no_match_returns_empty(tmp_path):
    s = _store(tmp_path)
    assert s.search_memories("__longterm__", "量子色动力学") == []


def test_search_empty_query_lists_all(tmp_path):
    s = _store(tmp_path)
    assert len(s.search_memories("__longterm__", "")) == 3

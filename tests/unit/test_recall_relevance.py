"""Synthetic retrieval cases, separate from model/agent effectiveness scoring."""
import pytest

from src.memory.lexical import rank_memories


MEMORIES = [
    {"id": "django", "content": "The user maintains a Django website."},
    {"id": "reactive", "content": "用户读过 reactive 编程的资料。"},
    {"id": "unsafe", "content": "unsafe_id 只出现在合成示例中。"},
    {"id": "postgresql", "content": "PostgreSQL 的数据备份保留在本机。"},
    {"id": "vortocode", "content": "VortoCode 的更新需要签名安装包。"},
    {"id": "xiaozhi", "content": "XiaoZhi 的验收需要实际设备。"},
    {"id": "wheelcam", "content": "WheelCAM 需要验证 STEP 结构。"},
    {"id": "go", "content": "用户使用 Go 编写网络服务。"},
    {"id": "react", "content": "界面使用 React 和 Vite。"},
    {"id": "safe", "content": "safe_id 拒绝路径穿越。"},
    {"id": "next", "content": "前端使用 Next.js 构建。"},
    {"id": "cxx", "content": "底层模块使用 C++ 开发。"},
]

# These cases are fixed before changing the ranker. Expected IDs describe
# retrieval behavior, not the answer a real model would produce.
CASES = [
    ("VortoCode这次怎么部署", {"vortocode"}, {"django", "xiaozhi"}),
    ("帮我继续推进XiaoZhi", {"xiaozhi"}, {"vortocode"}),
    ("WheelCAM接下来做什么", {"wheelcam"}, {"vortocode"}),
    ("怎么处理safe_id", {"safe"}, {"unsafe"}),
    ("Go", {"go"}, {"django"}),
    ("React", {"react"}, {"reactive"}),
    ("Next.js怎么打包", {"next"}, set()),
    ("C++怎么构建", {"cxx"}, set()),
    ("VORTOCODE这次怎么部署", {"vortocode"}, {"xiaozhi"}),
    ("How should I build React?", {"react"}, {"django", "reactive"}),
    ("What is the weather?", set(), {item["id"] for item in MEMORIES}),
    ("Can you tell me a joke?", set(), {item["id"] for item in MEMORIES}),
    ("Django怎么发布", {"django"}, {"go"}),
    ("请说明PostgreSQL的备份", {"postgresql"}, {"go"}),
]


@pytest.mark.parametrize("query,required,forbidden", CASES)
def test_mixed_language_recall_and_unrelated_fact_exclusion(query, required, forbidden):
    ids = {item["id"] for item in rank_memories(MEMORIES, query, 3)}
    assert required <= ids
    assert not ids & forbidden


def test_specific_project_terms_rank_above_shared_deployment_words():
    rows = [{"id": str(index), "content": f"其他项目部署签名包要求 {index}"} for index in range(12)]
    rows.append({"id": "project", "content": "VortoCode 使用稳定签名包"})
    assert rank_memories(rows, "VortoCode部署签名包", 3)[0]["id"] == "project"


def test_equal_relevance_preserves_the_callers_importance_and_recency_order():
    rows = [{"id": key, "content": "VortoCode 使用签名包"} for key in ("newer", "older")]
    assert [row["id"] for row in rank_memories(rows, "VortoCode部署", 3)] == ["newer", "older"]


def test_empty_query_keeps_the_manual_listing_contract():
    assert rank_memories(MEMORIES, "", 2) == MEMORIES[:2]


@pytest.mark.parametrize("query,content", [
    ("Go", "Django"), ("React", "reactive"), ("safe_id", "unsafe_id"),
    ("safe_id", "safe_id_extra"), ("Next.js", "NextXjs"),
    ("_safe_id", "__safe_id"),
])
def test_ascii_words_and_identifiers_are_not_substring_matches(query, content):
    assert rank_memories([{"id": "unrelated", "content": content}], query) == []


@pytest.mark.parametrize("query,other,correct", [
    ("Go模块", "Django模块", "Go 是已选定的语言"),
    ("safe_id的约定", "unsafe_id的约定", "约定针对 safe_id"),
    ("使用safe_id", "使用safe_id_extra", "操作对象是 safe_id"),
])
def test_identifier_match_outranks_shared_generic_words(query, other, correct):
    # Shared Chinese words may still be relevant background; exact identifiers
    # must win over generic overlap even when the other row has higher importance.
    rows = [{"id": "generic", "content": other}, {"id": "exact", "content": correct}]
    assert rank_memories(rows, query, 3)[0]["id"] == "exact"


@pytest.mark.parametrize("query", ["---", "...", "___", "?!", "what is it"])
def test_punctuation_and_function_words_do_not_select_personal_facts(query):
    assert rank_memories([{"content": "The user prefers safe_id..."}], query) == []


@pytest.mark.parametrize("identifier", ["_safe_id", "__init__", "foo__bar", "API_TOKEN"])
def test_mixed_language_preserves_the_whole_underscore_identifier(identifier):
    rows = [{"id": "exact", "content": f"操作对象是 {identifier}。"}]
    assert rank_memories(rows, f"请检查{identifier}")[0]["id"] == "exact"

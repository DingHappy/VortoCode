"""工序产出物：交接载体 + 血缘 + 污点沿血缘传播。

断的是行为不是源码：每条测试都描述"少了这个防护会漏进哪个坏状态"。
"""
import json

import pytest

from src.gateway.products import MAX_PAYLOAD_BYTES, Product, ProductStore


@pytest.fixture
def store(tmp_path):
    return ProductStore(str(tmp_path))


# ------------------------------------------------------------------ 交接
def test_next_stage_reads_by_id_not_by_context(store):
    """交接的全部意义：下一道工序**按 id 取**，不依赖任何上下文。"""
    pool = store.create("topic_pool", payload={"topics": ["A", "B"]}, summary="2 个候选",
                        pipeline="content-ops", run_id="run-1", stage="scout")
    # 换一个 store 实例 = 换一个进程/会话/回合，产出照样取得到
    fresh = ProductStore(store.repo_root).load(pool.id)
    assert fresh is not None
    assert fresh.payload["topics"] == ["A", "B"]
    assert fresh.kind == "topic_pool" and fresh.stage == "scout"


def test_latest_of_kind_feeds_the_next_round(store):
    """闭环靠这个：scout 读上一轮 metrics，不用每次从零抓。"""
    store.create("metrics", payload={"views": 1}, pipeline="content-ops")
    newest = store.create("metrics", payload={"views": 2}, pipeline="content-ops")
    assert store.latest("metrics", pipeline="content-ops").id == newest.id
    assert store.latest("topic_pool") is None          # 没有就是没有，不瞎给一个


def test_listing_filters_and_orders_newest_first(store):
    a = store.create("topic_pool", pipeline="content-ops", run_id="r1")
    b = store.create("topic_pool", pipeline="content-ops", run_id="r2")
    store.create("metrics", pipeline="content-ops", run_id="r1")
    assert [p.id for p in store.list(kind="topic_pool")] == [b.id, a.id]
    assert [p.id for p in store.list(run_id="r1", kind="topic_pool")] == [a.id]
    assert store.list(pipeline="别的部门") == []


# ------------------------------------------------------------------ 血缘
def test_lineage_walks_back_to_the_origin(store):
    """"这篇文章是从哪条热点来的"要能回答——否则证据链断在中间。"""
    pool = store.create("topic_pool", summary="候选")
    pack = store.create("content_pack", inputs=[pool.id], summary="成稿")
    pub = store.create("publication", inputs=[pack.id], summary="已发布")
    chain = [p.id for p in store.lineage(pub.id)]
    assert chain == [pub.id, pack.id, pool.id]


def test_lineage_survives_a_hand_edited_cycle(store):
    """存储是可手改的；一个手滑的自引用不该让日报进程转到天荒地老。"""
    a = store.create("topic_pool")
    b = store.create("content_pack", inputs=[a.id])
    a.inputs = [b.id]                                   # 人为造环
    store.save(a)
    chain = store.lineage(b.id)
    assert {p.id for p in chain} == {a.id, b.id}        # 每条只出现一次，且能返回


def test_lineage_of_missing_product_is_empty_not_an_error(store):
    assert store.lineage("prod-nope-000") == []


# ------------------------------------------------------------------ 污点传播（本模块的安全命题）
def test_taint_flows_down_the_lineage(store):
    """污点是回合级的，产出物是跨天的。

    一个来自 web_search 的选题池，三天后被另一个会话读进来写文章——那个回合同样在处理不可信
    内容。不沿血缘传播的话，免确认授权照样有效，等于把提示注入 D0 的口子换个时间维度重开。
    """
    dirty = store.create("topic_pool", tainted=True, taint_reason="来自 web_search 结果")
    pack = store.create("content_pack", inputs=[dirty.id])
    pub = store.create("publication", inputs=[pack.id])
    assert pack.tainted and pub.tainted
    assert "web_search" in pub.taint_reason              # 理由要一路带着，人才看得懂为什么

    clean = store.create("topic_pool", payload={"src": "人工输入"})
    assert store.create("content_pack", inputs=[clean.id]).tainted is False


def test_a_stage_cannot_declare_itself_clean(store):
    """污点只能加不能减——否则任何一道工序都能自证清白，模型形同虚设。"""
    dirty = store.create("topic_pool", tainted=True, taint_reason="外部抓取")
    laundered = store.create("content_pack", inputs=[dirty.id], tainted=False)
    assert laundered.tainted is True


def test_unreadable_input_counts_as_tainted(store):
    """血缘断了就无法证明来源干净；"证明不了干净"在污点模型里只能当脏（fail-closed）。"""
    orphan = store.create("content_pack", inputs=["prod-topic_pool-deleted"])
    assert orphan.tainted is True and "读不到" in orphan.taint_reason


# ------------------------------------------------------------------ 存储硬约束
def test_oversized_payload_is_rejected_not_truncated(store):
    """截断过的选题池看起来一切正常——那是最坏的一种坏。宁可拒绝。"""
    with pytest.raises(ValueError, match="超过上限"):
        store.create("topic_pool", payload={"blob": "x" * (MAX_PAYLOAD_BYTES + 10)})
    assert store.list(kind="topic_pool") == []          # 拒绝之后不留半条脏记录


def test_product_id_cannot_escape_the_store(store, tmp_path):
    """id 是会进文件路径的，挡 ../ 与 goals/dev_plan 同口径。"""
    assert store.load("../../etc/passwd") is None
    assert store.load("") is None
    evil = Product(id="../escape", kind="topic_pool")
    assert store.save(evil) is True
    assert not (tmp_path.parent / "escape.json").exists()
    assert (tmp_path / ".vortocode" / "products" / "escape.json").is_file()


def test_kind_is_required(store):
    with pytest.raises(ValueError, match="kind"):
        store.create("")


def test_corrupt_file_is_skipped_not_fatal(store, tmp_path):
    good = store.create("topic_pool")
    bad = tmp_path / ".vortocode" / "products" / "prod-broken.json"
    bad.write_text("{ 这不是 json", encoding="utf-8")
    assert [p.id for p in store.list()] == [good.id]
    assert store.load("prod-broken") is None


def test_unknown_fields_in_stored_file_are_ignored(store, tmp_path):
    """老文件遇到新版本、新文件遇到老版本，都不该崩——只忽略不认识的字段。"""
    product = store.create("topic_pool")
    path = tmp_path / ".vortocode" / "products" / f"{product.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_field"] = {"whatever": 1}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert store.load(product.id).kind == "topic_pool"


def test_products_dir_is_gitignored(store, tmp_path):
    """产出物是运行时状态，不该出现在目标仓库的 git status 里。"""
    store.create("topic_pool")
    ignore = (tmp_path / ".vortocode" / ".gitignore").read_text(encoding="utf-8")
    assert "products/" in ignore


def test_same_second_products_keep_their_order(store):
    """秒级时间戳会让同一秒内的产出排序退化成按随机 id 排，`latest()` 于是随机给一条。

    一道工序连续产出几条是常态（比如 publish 逐渠道各落一条回执），所以这里必须比秒更细。
    """
    ids = [store.create("publication", payload={"n": i}).id for i in range(8)]
    assert [p.id for p in store.list(kind="publication")] == list(reversed(ids))
    assert store.latest("publication").id == ids[-1]

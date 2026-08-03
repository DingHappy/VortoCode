"""dev 计划 DAG 投影（P1 数据面）的回归网。

投影的三条设计约束各有对照：纯投影零新状态 / 分层在服务端算好 / 坏数据如实标注不炸。
计划文件**允许手改**（C1 特性），所以"改出环、删出悬空依赖"不是异常路径，是一等输入。
"""

import pytest

from src.agents.dev_plan import Block, DevPlan, save_plan
from src.gateway.dev_graph import graph_of, plan_graph, plan_summaries


def _plan(blocks, **kw):
    p = DevPlan.new("测试任务", "vorto/x", "main")
    p.blocks = blocks
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _b(bid, deps=None, status="pending", kind=None):
    return Block(id=bid, kind=kind or ("dependent" if deps else "independent"),
                 desc=f"块 {bid}", deps=list(deps or []), status=status)


# ---------------------------------------------------------------- 1. 图形状
def test_graph_carries_nodes_edges_and_plan_facts():
    p = _plan([_b("a", status="landed"), _b("b", deps=["a"], status="running")],
              pr={"url": "https://example/pr/1"})
    g = graph_of(p)
    assert [n["id"] for n in g["nodes"]] == ["a", "b"]
    assert g["nodes"][1]["deps"] == ["a"]
    assert g["nodes"][0]["status"] == "landed"
    assert g["pr"] == {"url": "https://example/pr/1"}
    assert g["branch"] == "vorto/x" and g["base"] == "main"


def test_layers_are_topological_batches():
    """独立批在第 0 层，接力块按依赖满足的先后分层——前端照 layers 排列就是正确的图。"""
    p = _plan([_b("a"), _b("b"), _b("c", deps=["a", "b"]), _b("d", deps=["c"])])
    g = graph_of(p)
    assert g["layers"] == [["a", "b"], ["c"], ["d"]]
    assert g["cycle"] is False and g["cyclic_ids"] == []


def test_layer_order_is_deterministic():
    """同层内保持计划文件里的顺序——同一份计划两次投影必须逐字节一致（前端 diff 刷新依赖这点）。"""
    p = _plan([_b("z"), _b("a"), _b("m")])
    assert graph_of(p)["layers"] == [["z", "a", "m"]]
    assert graph_of(p) == graph_of(p)


# ---------------------------------------------------------------- 2. 手改出的坏数据
def test_cycle_is_reported_not_crashed():
    """改出环 → 环外正常分层，环内进 cyclic_ids。可视化的职责是把'计划坏了'摆出来。"""
    p = _plan([_b("a"), _b("b", deps=["c"]), _b("c", deps=["b"])])
    g = graph_of(p)
    assert g["layers"] == [["a"]]
    assert g["cycle"] is True and g["cyclic_ids"] == ["b", "c"]


def test_dangling_dep_is_dropped_and_does_not_block():
    """依赖指向被手删的块 → 丢边且不挡后继（与 dev_resume '删块=已满足'的语义一致）。"""
    p = _plan([_b("a", deps=["ghost"]), _b("b", deps=["a"])])
    g = graph_of(p)
    assert g["nodes"][0]["deps"] == []
    assert g["layers"] == [["a"], ["b"]]


def test_empty_plan_projects_cleanly():
    g = graph_of(_plan([]))
    assert g["nodes"] == [] and g["layers"] == [] and g["cycle"] is False


# ---------------------------------------------------------------- 3. 落盘往返（纯投影）
def test_projection_reads_the_authoritative_file(tmp_path):
    """投影必须读 write-ahead 的那份权威文件——不是内存里碰巧有的对象。"""
    p = _plan([_b("a"), _b("b", deps=["a"])])
    assert save_plan(str(tmp_path), p)
    g = plan_graph(str(tmp_path), p.plan_id)
    assert g is not None and [n["id"] for n in g["nodes"]] == ["a", "b"]

    assert plan_graph(str(tmp_path), "no-such-plan") is None
    assert plan_graph(str(tmp_path), "../../etc/passwd") is None   # 路径穿越走 _clean_id


def test_summaries_come_from_the_same_source(tmp_path):
    p = _plan([_b("a")])
    save_plan(str(tmp_path), p)
    items = plan_summaries(str(tmp_path))
    assert len(items) == 1 and items[0]["plan_id"] == p.plan_id


# ---------------------------------------------------------------- 4. 路由（含 404 与鉴权面）
@pytest.mark.asyncio
async def test_routes_serve_graph_and_404(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = _plan([_b("a"), _b("b", deps=["a"], status="failed")])
    save_plan(str(tmp_path), p)

    from src.web.routers.dev_plans import dev_plan_graph, list_dev_plans

    items = await list_dev_plans()
    assert items and items[0]["plan_id"] == p.plan_id

    g = await dev_plan_graph(p.plan_id)
    assert g["layers"] == [["a"], ["b"]]
    assert g["nodes"][1]["status"] == "failed"

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await dev_plan_graph("nope")
    assert e.value.status_code == 404


# ---------------------------------------------------------------- 5. 块级 token 归因
def test_block_tokens_survive_the_roundtrip(tmp_path):
    """块级花费必须**落盘并投影出来**——只记日志的话，看图的人永远看不到"哪块贵"。

    这条链有四段（计量 → 写进 Block → 存盘 → 投影），断哪一段界面上都只是"没显示花费"，
    不会报错。所以从落盘那头往回验。
    """
    p = _plan([_b("a", status="landed"), _b("b", deps=["a"], status="failed")])
    p.blocks[0].tokens = 7300
    p.blocks[1].tokens = 96200
    assert save_plan(str(tmp_path), p)

    g = plan_graph(str(tmp_path), p.plan_id)
    assert [n["tokens"] for n in g["nodes"]] == [7300, 96200]
    assert g["tokens"] == 103500, "图级合计要等于各块之和（前端直接显示这个数）"


def test_old_plan_files_without_tokens_project_as_zero(tmp_path):
    """今天之前的计划文件没有 tokens 字段——**读得出来且记 0**，不是崩掉。

    dev_plan.from_dict 过滤未知字段 + dataclass 默认值，双向兼容；这里从行为端钉住。
    """
    import json
    p = _plan([_b("a", status="landed")])
    save_plan(str(tmp_path), p)
    path = tmp_path / ".vortocode" / "dev_plans" / f"{p.plan_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for blk in data["blocks"]:
        blk.pop("tokens", None)                  # 模拟旧文件
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    g = plan_graph(str(tmp_path), p.plan_id)
    assert g is not None and g["nodes"][0]["tokens"] == 0 and g["tokens"] == 0

"""审批工作台的 REST 面——**读要全，写要窄**。

IM 那条路负责"叫人"（状态变化推一条通知），这条路负责"审阅"：一屏几十行的选题池在手机上
读不了，人需要能横向对比、能展开原文、能带着意见驳回。两条路**共用同一份状态**
（`.vortocode/pipeline_runs/` + 产出物台账），不是两套。

写操作只有三个动词（approve/reject/defer）和一个推进，**没有"编辑产出物"**：
产出物是工序的产出，人改了它，血缘就断了——要改就驳回重做，意见落成 review_note 进血缘。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.gateway.pipeline import PipelineStore, load_definition, review, review_fingerprint
from src.gateway.products import ProductStore

router = APIRouter()

_TERMINAL = {"done", "abandoned"}


def _repo() -> str:
    return os.getcwd()


def _product_view(product_id: str, product) -> Optional[Dict[str, Any]]:
    if not product_id:
        return None
    if product is None:
        # 读不到要**说出来**，不能当成"没有产出物"——那两件事在审批时含义完全不同：
        # 一个是还没跑，一个是台账坏了。
        return {"id": product_id, "missing": True}
    return {
        "id": product.id, "kind": product.kind, "summary": product.summary,
        "tainted": bool(product.tainted), "taint_reason": product.taint_reason,
        "payload": product.payload, "inputs": list(product.inputs),
        "created": product.created, "missing": False,
    }


def _run_view(run, *, detail: bool = False) -> Dict[str, Any]:
    waiting = run.awaiting()
    view: Dict[str, Any] = {
        "run_id": run.run_id, "pipeline": run.pipeline, "status": run.status,
        "created": run.created, "updated": run.updated,
        "awaiting": waiting.id if waiting is not None else "",
        "stages": [{"id": s.id, "status": s.status, "attempts": s.attempts,
                    "tokens": s.tokens, "note": s.note, "product_id": s.product_id}
                   for s in run.stages],
        "tokens": sum(s.tokens for s in run.stages),
    }
    if detail:
        store = ProductStore(_repo())
        view["review_token"] = ""
        for stage in view["stages"]:
            product = store.load(stage["product_id"])
            stage["product"] = _product_view(stage["product_id"], product)
            if waiting is not None and waiting.id == stage["id"] and product is not None:
                view["review_token"] = review_fingerprint(_repo(), run, product)
        definition = load_definition(_repo(), run.pipeline)
        if definition is not None:
            for stage in view["stages"]:
                spec = definition.stage(stage["id"])
                if spec is not None:
                    stage["spec"] = {"role": spec.role, "produces": spec.produces,
                                     "review": spec.review, "outbound": spec.outbound,
                                     "web": spec.web, "note": spec.note}
    return view


@router.get("/api/pipelines")
async def list_pipeline_runs(include_done: bool = False, limit: int = 50) -> dict:
    """运行列表。默认只给**还活着的**——工作台第一屏该是"该你管的"，不是历史归档。"""
    runs = PipelineStore(_repo()).list(limit=max(1, min(int(limit), 200)))
    if not include_done:
        runs = [r for r in runs if r.status not in _TERMINAL]
    # 等人批的排最前，其余按更新时间。
    runs.sort(key=lambda r: (r.status != "awaiting_review", r.updated), reverse=False)
    return {"runs": [_run_view(r) for r in runs],
            "awaiting": sum(1 for r in runs if r.status == "awaiting_review")}


@router.get("/api/pipelines/{run_id}")
async def get_pipeline_run(run_id: str) -> dict:
    run = PipelineStore(_repo()).load(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"无此运行：{run_id}")
    return _run_view(run, detail=True)


@router.get("/api/pipelines/{run_id}/lineage/{product_id}")
async def get_product_lineage(run_id: str, product_id: str) -> dict:
    """一条产出物的血缘。**审批时最该看的就是"这东西从哪来"**——尤其带污点的那些。"""
    chain = ProductStore(_repo()).lineage(product_id)
    if not chain:
        raise HTTPException(status_code=404, detail=f"无此产出物：{product_id}")
    return {"lineage": [{"id": p.id, "kind": p.kind, "stage": p.stage, "summary": p.summary,
                         "tainted": bool(p.tainted), "taint_reason": p.taint_reason,
                         "created": p.created, "inputs": list(p.inputs)} for p in chain]}


class ReviewBody(BaseModel):
    verdict: str
    comment: str = ""
    rollback_to: str = ""
    review_token: str = ""


@router.post("/api/pipelines/{run_id}/review")
async def post_review(run_id: str, body: ReviewBody) -> dict:
    """人批三档。**驳回必须带意见**——不说理由，重跑出来还是原样，那趟 token 白烧。"""
    if body.verdict not in {"approve", "reject", "defer"}:
        raise HTTPException(status_code=400, detail="verdict 必须是 approve/reject/defer")
    if body.verdict == "reject" and not body.comment.strip():
        raise HTTPException(status_code=400, detail="驳回要写一句为什么——不说理由，重跑出来还是原样")
    result = review(_repo(), run_id, verdict=body.verdict, comment=body.comment,
                    rollback_to=body.rollback_to, reviewer="web", review_token=body.review_token)
    if result.status == "missing":
        raise HTTPException(status_code=404, detail=result.reason)
    if result.status in {"conflict", "busy"}:
        raise HTTPException(status_code=409, detail=result.reason)
    if result.status == "storage_error":
        raise HTTPException(status_code=503, detail=result.reason)
    return {"status": result.status, "reason": result.reason, "blocked_on": result.blocked_on}


@router.get("/api/pipelines/{run_id}/products/{product_id}/raw")
async def get_product_raw(run_id: str, product_id: str) -> dict:
    """产出物原文。列表页给摘要，这里给全量——**批之前要能看见完整的东西**。"""
    product = ProductStore(_repo()).load(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"无此产出物：{product_id}")
    return {"id": product.id, "kind": product.kind, "summary": product.summary,
            "tainted": bool(product.tainted), "taint_reason": product.taint_reason,
            "payload": product.payload}

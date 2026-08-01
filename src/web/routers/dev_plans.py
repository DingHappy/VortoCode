"""dev 计划的只读数据面（P1）——DAG 投影给 Desktop / 未来移动 PWA 共用。

只读：计划的**写**路径只有一条（dev_auto/dev_resume 的 write-ahead），这里绝不提供
改计划的接口——要改，语义上等于改一次运行的历史，那不是 REST 该开的口子。
鉴权与全站一致：设了 VORTOCODE_API_TOKEN 则所有 /api/* 强制鉴权（见 web/auth）。
"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


@router.get("/api/dev-plans")
async def list_dev_plans():
    """计划列表，按最近更新排序（IM /status 同款数据，REST 化）。"""
    from src.gateway.dev_graph import plan_summaries

    return plan_summaries(os.getcwd())


@router.get("/api/dev-plans/{plan_id}/graph")
async def dev_plan_graph(plan_id: str):
    """一份计划的 DAG 投影：nodes + 服务端算好的拓扑 layers（前端不需要图算法）。"""
    from src.gateway.dev_graph import plan_graph

    graph = plan_graph(os.getcwd(), plan_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"无此计划 {plan_id}")
    return graph

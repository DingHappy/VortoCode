/**
 * dev 计划的 DAG 视图（P2）——把"拆解→并行批→拓扑接力→集成→PR"从进度条升级成图。
 *
 * 借的是 homerail 观感上最强的那张牌：DAG 让人一眼看出"哪块卡住了、卡在谁后面"，
 * 这是平铺列表和百分比条给不了的。
 *
 * 设计取舍：
 * - **不引图库**。layers 已由服务端拓扑分层算好（数据面只建一次的约定），前端只剩
 *   "按列摆卡片 + 量出坐标画贝塞尔"，自己写 ~40 行；reactflow 那类依赖为这点事不值得。
 * - **边在 SVG overlay 里画**，坐标用 ResizeObserver 实测——比"猜固定行高"能扛住
 *   标题换行、窗口缩放、字体缩放。
 * - **环如实标红**。计划文件允许手改（C1 特性），改出环是要摆给人看的事实，不是异常。
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import type { DevPlanGraph, DevPlanGraphNode } from "../types";

/**
 * 懒加载壳：展开时才取图，refreshKey（由进度计数派生）变了就重取。
 * 不做轮询——任务进度本来就随 listTasks 周期刷新，蹭它的节拍即可，别多一路定时器。
 */
export function DevPlanDagLoader({ load, refreshKey }: {
  load: () => Promise<DevPlanGraph>;
  refreshKey: string;
}) {
  const [graph, setGraph] = useState<DevPlanGraph | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    load().then((next) => {
      if (alive) { setGraph(next); setError(""); }
    }).catch((cause) => {
      // 图取不到不该毁掉任务卡的其余部分——平铺进度仍在，这里只如实说图挂了
      if (alive) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  if (error) return <div className="devplan-dag-error">依赖图加载失败：{error}</div>;
  if (!graph) return <div className="devplan-dag-loading">加载依赖图…</div>;
  return <DevPlanDag graph={graph} />;
}

type Edge = { from: string; to: string };
type Path = { d: string; failed: boolean };

const STATUS_GLYPH: Record<string, string> = {
  landed: "✓", running: "●", failed: "!", pending: "○",
};

function edgesOf(graph: DevPlanGraph): Edge[] {
  const edges: Edge[] = [];
  for (const node of graph.nodes) for (const dep of node.deps) edges.push({ from: dep, to: node.id });
  return edges;
}

export function DevPlanDag({ graph }: { graph: DevPlanGraph }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [paths, setPaths] = useState<Path[]>([]);
  const [openNode, setOpenNode] = useState("");

  const byId = new Map<string, DevPlanGraphNode>(graph.nodes.map((node) => [node.id, node]));
  // 环里的块进不了 layers，但必须可见——追加成末尾一列（标红），绝不静默吞掉。
  const columns: string[][] = graph.cycle && graph.cyclic_ids.length > 0
    ? [...graph.layers, graph.cyclic_ids]
    : graph.layers;

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const draw = () => {
      const base = container.getBoundingClientRect();
      const next: Path[] = [];
      for (const edge of edgesOf(graph)) {
        const from = container.querySelector<HTMLElement>(`[data-dag-node="${CSS.escape(edge.from)}"]`);
        const to = container.querySelector<HTMLElement>(`[data-dag-node="${CSS.escape(edge.to)}"]`);
        if (!from || !to) continue;
        const a = from.getBoundingClientRect();
        const b = to.getBoundingClientRect();
        const x1 = a.right - base.left;
        const y1 = a.top + a.height / 2 - base.top;
        const x2 = b.left - base.left;
        const y2 = b.top + b.height / 2 - base.top;
        const bend = Math.max(18, (x2 - x1) / 2);
        next.push({
          d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
          failed: byId.get(edge.to)?.status === "failed",
        });
      }
      setPaths(next);
    };
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(container);
    return () => observer.disconnect();
    // graph 是每次 fetch 的新对象，作依赖即可；byId 由它派生，不单独列。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  return (
    <div className="devplan-dag">
      <div className="devplan-dag-facts">
        <code>{graph.branch}</code>
        <span className={`dag-plan-status ${graph.status}`}>{graph.status}</span>
        {graph.integration && (
          <span className={graph.integration.ok ? "dag-integration ok" : "dag-integration bad"}>
            集成{graph.integration.ok ? "绿" : "红"}
          </span>
        )}
        {graph.pr?.url && (
          <a href={graph.pr.url} target="_blank" rel="noreferrer">PR ↗</a>
        )}
        {graph.cycle && <span className="dag-cycle-warn">⚠ 计划被改出了依赖环</span>}
      </div>
      <div className="devplan-dag-canvas" ref={containerRef}>
        <svg className="devplan-dag-edges" aria-hidden="true">
          {paths.map((path, index) => (
            <path className={path.failed ? "failed" : ""} d={path.d} key={index} />
          ))}
        </svg>
        {columns.map((column, columnIndex) => (
          <div className="devplan-dag-layer" key={columnIndex}>
            {column.map((id) => {
              const node = byId.get(id);
              if (!node) return null;
              const cyclic = graph.cyclic_ids.includes(id);
              return (
                <button
                  className={`devplan-dag-node ${node.status} ${cyclic ? "cyclic" : ""} ${openNode === id ? "open" : ""}`}
                  data-dag-node={id}
                  key={id}
                  onClick={() => setOpenNode(openNode === id ? "" : id)}
                  title={node.desc}
                >
                  <span className="dag-glyph">{cyclic ? "⟳" : STATUS_GLYPH[node.status] ?? "○"}</span>
                  <b>{node.title}</b>
                  {node.attempts > 1 && <small>{node.attempts} 次尝试</small>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {openNode && byId.get(openNode) && (
        <div className="devplan-dag-detail">
          <b>{byId.get(openNode)!.title}</b>
          <p>{byId.get(openNode)!.desc}</p>
          {/* note 是最近一次失败的输出尾部——排查"这块为什么红"的第一手证据 */}
          {byId.get(openNode)!.note && <pre>{byId.get(openNode)!.note}</pre>}
        </div>
      )}
    </div>
  );
}

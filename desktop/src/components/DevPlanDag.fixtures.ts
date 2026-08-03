/**
 * DevPlanDag 的样本图——**测试与可视 harness 共用同一份**。
 *
 * 为什么需要：这个视图上线时我从没亲眼看它渲染过——唯一的观看方式是真跑一次
 * `dev_auto`（烧 token、要几分钟），而测试只断了数据形状，没有任何东西证明画出来像样。
 * 借的是 homerail `task-canvas-harness` 的做法：给 canvas 一个脱离主应用的独立入口。
 *
 * 每个样本对应一种「画出来可能不对」的情形，不是随便造几个点。
 */
import type { DevPlanGraph, DevPlanGraphNode } from "../types";

function node(
  id: string,
  over: Partial<DevPlanGraphNode> = {},
): DevPlanGraphNode {
  return {
    id,
    title: `子任务 ${id}`,
    desc: `${id} 的完整描述`,
    kind: over.deps?.length ? "dependent" : "independent",
    status: "pending",
    attempts: 0,
    note: "",
    deps: [],
    ...over,
  };
}

function graph(over: Partial<DevPlanGraph>): DevPlanGraph {
  return {
    plan_id: "plan-fixture",
    task: "样本任务",
    status: "running",
    branch: "vorto/auto-fixture",
    base: "main",
    created: "2026-08-03T00:00:00+00:00",
    updated: "2026-08-03T00:00:00+00:00",
    integration: null,
    review: null,
    pr: null,
    nodes: [],
    layers: [],
    cycle: false,
    cyclic_ids: [],
    ...over,
  };
}

/** 单节点：最常见的真实情形（真机上那份计划就是 1 个块）。别让空图/单点画崩。 */
export const singleNode = graph({
  nodes: [node("ind-0", { status: "landed" })],
  layers: [["ind-0"]],
  status: "done",
  pr: { url: "https://example.com/pr/1" },
  integration: { ok: true },
});

/** 宽并行批：一层里塞很多块——列会不会撑破容器、要不要横向滚。 */
export const wideParallel = graph({
  nodes: Array.from({ length: 7 }, (_, i) =>
    node(`ind-${i}`, { status: i < 4 ? "landed" : "running" })),
  layers: [Array.from({ length: 7 }, (_, i) => `ind-${i}`)],
});

/** 多层接力 + 汇聚：边要跨列画，且有"多入边汇到一个点"。 */
export const layeredWithJoin = graph({
  nodes: [
    node("a", { status: "landed" }),
    node("b", { status: "landed" }),
    node("c", { deps: ["a", "b"], status: "running" }),
    node("d", { deps: ["c"] }),
  ],
  layers: [["a", "b"], ["c"], ["d"]],
});

/** 失败块：红色节点 + 虚线入边 + 展开看失败输出尾部。 */
export const withFailure = graph({
  nodes: [
    node("a", { status: "landed" }),
    node("b", {
      deps: ["a"], status: "failed", attempts: 3,
      note: "FAILED tests/unit/test_x.py::test_y\nAssertionError: 期望 3 实际 2\n（重试 3 次仍未过）",
    }),
    node("c", { deps: ["b"] }),
  ],
  layers: [["a"], ["b"], ["c"]],
  status: "integration_failed",
  integration: { ok: false, output: "集成验证红" },
});

/** 被手改出环：环内块进不了 layers，必须作为末尾一列可见地标红。 */
export const withCycle = graph({
  nodes: [
    node("a", { status: "landed" }),
    node("b", { deps: ["c"] }),
    node("c", { deps: ["b"] }),
  ],
  layers: [["a"]],
  cycle: true,
  cyclic_ids: ["b", "c"],
});

/** 超长标题：省略号有没有生效、会不会把列撑爆。 */
export const longTitles = graph({
  nodes: [
    node("a", { title: "把 src/gateway 下所有路由的鉴权判定统一收敛到中间件并补齐契约测试基线" }),
    node("b", { deps: ["a"], title: "顺带把 README 与 CLAUDE.md 里过时的模块路径一并订正" }),
  ],
  layers: [["a"], ["b"]],
});

/** 空图：不该崩，也不该画出空框。 */
export const empty = graph({});

export const ALL_FIXTURES: Array<{ name: string; graph: DevPlanGraph }> = [
  { name: "单节点（真机最常见）", graph: singleNode },
  { name: "宽并行批（7 个）", graph: wideParallel },
  { name: "多层接力 + 汇聚", graph: layeredWithJoin },
  { name: "失败块（红 + 虚线入边）", graph: withFailure },
  { name: "被手改出环", graph: withCycle },
  { name: "超长标题", graph: longTitles },
  { name: "空图", graph: empty },
];

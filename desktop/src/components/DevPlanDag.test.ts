import { describe, expect, it } from "vitest";

import { ALL_FIXTURES, empty, layeredWithJoin, withCycle, withFailure } from "./DevPlanDag.fixtures";
import { dagColumns, edgesOf, fmtTokens } from "./DevPlanDag";

/**
 * 只测**纯派生**（图 → 列 / 图 → 边）。组件本体要量真实坐标画贝塞尔，那部分靠 harness 用眼睛看
 * （`npm run dev` 打开 /dag-harness.html）——不为它引 jsdom + testing-library，
 * 那会把 desktop 门禁从「离线秒级」拖成另一回事。
 */
describe("dagColumns", () => {
  it("无环时就是服务端算好的分层", () => {
    expect(dagColumns(layeredWithJoin)).toEqual([["a", "b"], ["c"], ["d"]]);
  });

  it("有环时把环内块追加成末尾一列——绝不静默吞掉", () => {
    // 计划文件允许手改，改出环是要摆给人看的事实。少了这一列，那两个块在界面上凭空消失。
    expect(dagColumns(withCycle)).toEqual([["a"], ["b", "c"]]);
  });

  it("空图给空列，不画空框", () => {
    expect(dagColumns(empty)).toEqual([]);
  });
});

describe("edgesOf", () => {
  it("多入边汇聚照实产出", () => {
    expect(edgesOf(layeredWithJoin)).toEqual([
      { from: "a", to: "c" },
      { from: "b", to: "c" },
      { from: "c", to: "d" },
    ]);
  });

  it("空图没有边", () => {
    expect(edgesOf(empty)).toEqual([]);
  });
});

describe("样本图自身的自洽性", () => {
  it("每条边的两端都在节点集合里——悬空边会画向虚空", () => {
    for (const { name, graph } of ALL_FIXTURES) {
      const ids = new Set(graph.nodes.map((n) => n.id));
      for (const e of edgesOf(graph)) {
        expect(ids.has(e.from), `${name}: 边起点 ${e.from} 不存在`).toBe(true);
        expect(ids.has(e.to), `${name}: 边终点 ${e.to} 不存在`).toBe(true);
      }
    }
  });

  it("每个节点都出现在某一列里——否则它在界面上不可见", () => {
    for (const { name, graph } of ALL_FIXTURES) {
      const shown = new Set(dagColumns(graph).flat());
      for (const n of graph.nodes) {
        expect(shown.has(n.id), `${name}: 节点 ${n.id} 一列都没进，界面上看不见`).toBe(true);
      }
    }
  });

  it("图级 tokens 必须等于节点求和——服务端就是这么算的，样本不能描述后端产不出的状态", () => {
    for (const { name, graph } of ALL_FIXTURES) {
      const sum = graph.nodes.reduce((s, n) => s + (n.tokens || 0), 0);
      expect(graph.tokens, `${name}: 图级合计与节点对不上`).toBe(sum);
    }
  });

  it("失败样本带得有失败输出——那是排查'这块为什么红'的第一手证据", () => {
    const failed = withFailure.nodes.find((n) => n.status === "failed");
    expect(failed?.note).toBeTruthy();
  });
});

describe("fmtTokens", () => {
  it.each([
    [0, "0"], [1, "1"], [999, "999"],
    [1000, "1.0k"], [1500, "1.5k"],
    // 这两个是把输出真打出来看一眼才发现的边界
    [9999, "10k"],          // 曾是 "10.0k"：toFixed 进位后仍走了小数分支
    [1234567, "1.2M"],      // 曾是 "1235k"：百万级没有单位
    [18400, "18k"], [96200, "96k"],
  ])("%i → %s", (input, want) => {
    expect(fmtTokens(input)).toBe(want);
  });

  it("负数与小数不产出怪东西——数据来自累加，别让脏值把卡片撑坏", () => {
    expect(fmtTokens(-5)).toBe("0");
    expect(fmtTokens(1234.7)).toBe("1.2k");
  });
});

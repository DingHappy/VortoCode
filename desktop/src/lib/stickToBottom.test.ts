import { describe, expect, it } from "vitest";
import { stickToBottom, type ScrollBox } from "./stickToBottom";

function fakes() {
  const resizers: Array<{ fire: () => void; targets: object[] }> = [];
  const mutations: Array<() => void> = [];
  class Resize {
    targets: object[] = [];
    constructor(public fire: () => void) { resizers.push(this); }
    observe(target: object) { this.targets.push(target); }
    disconnect() { this.targets = []; }
  }
  class Mutation {
    constructor(callback: () => void) { mutations.push(callback); }
    observe() { /* 由测试手动触发 */ }
    disconnect() { mutations.length = 0; }
  }
  return { resizers, mutations, observers: { resize: Resize, mutation: Mutation } };
}

describe("stickToBottom", () => {
  it("人在底部时，后加载的卡片把内容撑高也跟到底", () => {
    const node: ScrollBox & { scrollHeight: number } = { scrollTop: 400, scrollHeight: 1000, children: [{}] };
    const { resizers, observers } = fakes();
    stickToBottom(node, () => true, observers);
    node.scrollHeight = 1300;               // 历史之后，Codex 动作卡加载出来
    resizers[0].fire();
    expect(node.scrollTop).toBe(1300);
  });

  it("人往上翻了就不动", () => {
    const node: ScrollBox & { scrollHeight: number } = { scrollTop: 100, scrollHeight: 1000, children: [] };
    const { resizers, observers } = fakes();
    stickToBottom(node, () => false, observers);
    node.scrollHeight = 1300;
    resizers[0].fire();
    expect(node.scrollTop).toBe(100);
  });

  it("新加的子元素也会被盯上；解除后不再跟", () => {
    const children: object[] = [{}];
    const node: ScrollBox & { scrollHeight: number } = { scrollTop: 0, scrollHeight: 500, children };
    const { resizers, mutations, observers } = fakes();
    const stop = stickToBottom(node, () => true, observers);
    expect(resizers[0].targets).toHaveLength(2);       // 容器 + 一个子元素
    children.push({});
    node.scrollHeight = 800;
    mutations[0]();
    expect(resizers[0].targets).toHaveLength(3);
    expect(node.scrollTop).toBe(800);
    stop();
    expect(resizers[0].targets).toHaveLength(0);
  });
});

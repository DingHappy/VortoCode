/**
 * lib/storage.ts 的纯逻辑单测（B8-④b S1 拆分安全网）。
 * localStorage 用内存实现 stub（node 环境无 DOM），断 key 常量 / 派生 key /
 * notifiedDecisions 读写与 200 条截断；不联网、离线确定性。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  loadNotifiedDecisionIds,
  persistNotifiedDecisionIds,
  projectSessionKey,
  STORAGE_KEYS,
} from "./storage";

function memoryStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => {
      map.set(key, String(value));
    },
    removeItem: (key: string) => {
      map.delete(key);
    },
    clear: () => map.clear(),
  };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", memoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("STORAGE_KEYS", () => {
  it("固定住全部 8 个 key 字面量（防拼写漂移）", () => {
    expect(STORAGE_KEYS).toEqual({
      baseUrl: "vortocode.desktop.baseUrl",
      sid: "vortocode.desktop.sid",
      repoRoot: "vortocode.desktop.repoRoot",
      generalSid: "vortocode.desktop.generalSid",
      scratchSid: "vortocode.desktop.scratchSid",
      notificationsEnabled: "vortocode.desktop.notificationsEnabled",
      notifiedDecisions: "vortocode.desktop.notifiedDecisions",
      projectSessionPrefix: "vortocode.desktop.projectSid:",
    });
  });
});

describe("projectSessionKey", () => {
  it("按项目 id 拼前缀", () => {
    expect(projectSessionKey("abc")).toBe("vortocode.desktop.projectSid:abc");
  });
});

describe("loadNotifiedDecisionIds", () => {
  it("无值时返回空集合", () => {
    expect(loadNotifiedDecisionIds().size).toBe(0);
  });

  it("解析数组、过滤非字符串项", () => {
    localStorage.setItem(STORAGE_KEYS.notifiedDecisions, JSON.stringify(["a", 1, "b", null, "c"]));
    const ids = loadNotifiedDecisionIds();
    expect([...ids].sort()).toEqual(["a", "b", "c"]);
  });

  it("坏 JSON 兜底为空集合", () => {
    localStorage.setItem(STORAGE_KEYS.notifiedDecisions, "{not json");
    expect(loadNotifiedDecisionIds().size).toBe(0);
  });

  it("读取时截断为最近 200 条", () => {
    const many = Array.from({ length: 250 }, (_, i) => `id-${i}`);
    localStorage.setItem(STORAGE_KEYS.notifiedDecisions, JSON.stringify(many));
    const ids = loadNotifiedDecisionIds();
    expect(ids.size).toBe(200);
    expect(ids.has("id-249")).toBe(true);
    expect(ids.has("id-49")).toBe(false);
    expect(ids.has("id-50")).toBe(true);
  });
});

describe("persistNotifiedDecisionIds", () => {
  it("写入 JSON 数组并截断为最近 200 条", () => {
    const many = new Set(Array.from({ length: 250 }, (_, i) => `id-${i}`));
    persistNotifiedDecisionIds(many);
    const raw = localStorage.getItem(STORAGE_KEYS.notifiedDecisions);
    const parsed = JSON.parse(raw ?? "[]") as string[];
    expect(parsed).toHaveLength(200);
    expect(parsed[parsed.length - 1]).toBe("id-249");
    expect(parsed[0]).toBe("id-50");
  });

  it("与 load 往返一致", () => {
    persistNotifiedDecisionIds(new Set(["x", "y"]));
    expect([...loadNotifiedDecisionIds()].sort()).toEqual(["x", "y"]);
  });
});

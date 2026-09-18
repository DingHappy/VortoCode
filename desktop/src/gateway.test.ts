/**
 * gateway.ts 的纯逻辑单测（B6-3 前端测试脚手架）。
 *
 * 硬约束：**不起 dev server、不联网、不开真实 WebSocket**。
 * `@tauri-apps/plugin-websocket` / `@tauri-apps/plugin-http` 全部 vi.mock 掉，
 * localStorage 用内存实现 stub。只有这样这套测试才配挂进离线门禁
 * （`npm run check:ci` 与 `scripts/ci-local.sh` 的 desktop 段）。
 *
 * 覆盖四类：① 协议版本校验 ② 事件分发 ③ cursor/seq 处理 ④ 错误路径。
 * 一律断行为（喂帧 → 看回调/落盘），不查源码子串。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import WebSocketPlugin from "@tauri-apps/plugin-websocket";

import {
  DESKTOP_PROTOCOL_VERSION,
  GatewayClient,
  createSessionId,
  normalizeLocalBaseUrl,
} from "./gateway";
import type { ProtocolEvent } from "./types";

// vitest.config.ts 从 src/gateway/protocol.py 里读出来注入的 runtime 协议版本。
declare const __RUNTIME_PROTOCOL_VERSION__: number;

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: vi.fn() }));
vi.mock("@tauri-apps/plugin-websocket", () => ({ default: { connect: vi.fn() } }));

const SID = "sid-1";
const CURSOR_KEY = `vortocode.desktop.eventCursor:${SID}`;
const SETTINGS = { baseUrl: "http://127.0.0.1:8080", token: "" };

interface Frame {
  type: string;
  data?: unknown;
}

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

/** 假 WebSocket：录下发出去的帧，并允许测试主动把帧喂给已注册的 listener。 */
function socketHarness() {
  const sent: Array<Record<string, unknown>> = [];
  let listener: ((frame: Frame) => void) | null = null;
  const socket = {
    addListener(callback: (frame: Frame) => void) {
      listener = callback;
      return () => {
        listener = null;
      };
    },
    async send(payload: string) {
      sent.push(JSON.parse(payload) as Record<string, unknown>);
    },
    async disconnect() {
      /* 假 socket，无需真的关 */
    },
  };
  const frame = (value: Frame) => {
    if (!listener) throw new Error("listener 还没注册");
    listener(value);
  };
  const text = (raw: string) => frame({ type: "Text", data: raw });
  const json = (event: Record<string, unknown>) => text(JSON.stringify(event));
  return { socket, sent, frame, text, json };
}

async function connectClient(storedCursor?: string) {
  const harness = socketHarness();
  vi.mocked(WebSocketPlugin.connect).mockResolvedValue(
    harness.socket as unknown as WebSocketPlugin,
  );
  if (storedCursor !== undefined) localStorage.setItem(CURSOR_KEY, storedCursor);
  const received: ProtocolEvent[] = [];
  const client = new GatewayClient(SETTINGS);
  await client.connect(SID, (event) => {
    received.push(event);
  });
  return { harness, received, client };
}

function mockHttpFailure(status: number, body: string) {
  vi.mocked(tauriFetch).mockResolvedValue({
    ok: false,
    status,
    text: async () => body,
    json: async () => JSON.parse(body) as unknown,
  } as unknown as Response);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("localStorage", memoryStorage());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ─────────────────────────── ① 协议版本校验 ───────────────────────────

describe("协议版本校验", () => {
  it("DESKTOP_PROTOCOL_VERSION 必须与 runtime 的 PROTOCOL_VERSION 对齐", () => {
    expect(DESKTOP_PROTOCOL_VERSION).toBe(__RUNTIME_PROTOCOL_VERSION__);
  });

  it("版本不一致的 init 事件要原样送达，网关不能吞掉或改写 v", async () => {
    const { harness, received } = await connectClient();
    harness.json({ type: "init", v: DESKTOP_PROTOCOL_VERSION + 1, data: {} });
    expect(received).toHaveLength(1);
    expect(received[0]).toMatchObject({ type: "init", v: DESKTOP_PROTOCOL_VERSION + 1 });
  });

  it("init 不带 seq，也不能被历史 cursor 过滤掉（否则重连后永远看不到版本）", async () => {
    const { harness, received } = await connectClient("999");
    harness.json({ type: "init", v: DESKTOP_PROTOCOL_VERSION, data: {} });
    expect(received.map((event) => event.type)).toEqual(["init"]);
    expect(received[0].v).toBe(DESKTOP_PROTOCOL_VERSION);
  });
});

// ─────────────────────────── ② 事件分发 ───────────────────────────

describe("事件分发", () => {
  it("合法 Text 帧解析后交给回调", async () => {
    const { harness, received } = await connectClient();
    harness.json({ type: "agent_say", text: "hi" });
    expect(received).toEqual([{ type: "agent_say", text: "hi" }]);
  });

  it("非 Text 帧（二进制 / Ping）直接忽略", async () => {
    const { harness, received } = await connectClient();
    harness.frame({ type: "Binary", data: [1, 2, 3] });
    harness.frame({ type: "Ping", data: [] });
    expect(received).toEqual([]);
  });

  it("坏 JSON 不抛异常也不分发——坏帧不该让 Desktop 崩", async () => {
    const { harness, received } = await connectClient();
    expect(() => harness.text("{ 这不是 JSON")).not.toThrow();
    expect(received).toEqual([]);
  });

  it("type 不是字符串 / 帧本身是 null 的都丢弃", async () => {
    const { harness, received } = await connectClient();
    harness.json({ type: 42 });
    harness.json({ text: "没有 type" });
    harness.text("null");
    expect(received).toEqual([]);
  });

  it("agent_events 批次要拆开逐条分发，wrapper 本身不进回调", async () => {
    const { harness, received } = await connectClient();
    harness.json({
      type: "agent_events",
      cursor: 2,
      items: [
        { type: "agent_say", text: "a", seq: 1 },
        { type: "agent_done", seq: 2 },
      ],
    });
    expect(received.map((event) => event.type)).toEqual(["agent_say", "agent_done"]);
  });

  it("agent_events 的 items 不是数组时按空批处理，不炸", async () => {
    const { harness, received } = await connectClient();
    expect(() => harness.json({ type: "agent_events", cursor: 4, items: null })).not.toThrow();
    expect(received).toEqual([]);
    expect(localStorage.getItem(CURSOR_KEY)).toBe("4");
  });
});

// ─────────────────────────── ③ cursor / seq 处理 ───────────────────────────

describe("cursor / seq 处理", () => {
  it("agent_events 里乱序的 items 按 seq 升序分发", async () => {
    const { harness, received } = await connectClient();
    harness.json({
      type: "agent_events",
      cursor: 3,
      items: [
        { type: "c", seq: 3 },
        { type: "a", seq: 1 },
        { type: "b", seq: 2 },
      ],
    });
    expect(received.map((event) => event.type)).toEqual(["a", "b", "c"]);
  });

  it("同一个 seq 重复到达只分发一次", async () => {
    const { harness, received } = await connectClient();
    harness.json({ type: "agent_say", text: "one", seq: 7 });
    harness.json({ type: "agent_say", text: "one", seq: 7 });
    expect(received).toHaveLength(1);
  });

  it("带 cursor 重连：发 replay 请求，且 seq <= cursor 的旧事件被过滤", async () => {
    const { harness, received } = await connectClient("5");
    expect(harness.sent).toEqual([
      { type: "get_status", hydrate: true },
      { type: "agent_events_replay", after_seq: 5, limit: 500 },
    ]);
    harness.json({
      type: "agent_events",
      cursor: 7,
      latest_seq: 7,
      items: [
        { type: "old4", seq: 4 },
        { type: "old5", seq: 5 },
        { type: "new6", seq: 6 },
        { type: "new7", seq: 7 },
      ],
    });
    expect(received.map((event) => event.type)).toEqual(["new6", "new7"]);
    expect(localStorage.getItem(CURSOR_KEY)).toBe("7");
  });

  it("首次连接（无本地 cursor）不发 replay，全量 hydrate 就够了", async () => {
    const { harness } = await connectClient();
    expect(harness.sent).toEqual([{ type: "get_status", hydrate: true }]);
  });

  it("sid 被复用（runtime 的 latest_seq 落后于本地 cursor）时重置 cursor，旧事件重新回放", async () => {
    const { harness, received } = await connectClient("100");
    harness.json({
      type: "agent_events",
      cursor: 3,
      latest_seq: 3,
      items: [
        { type: "fresh1", seq: 1 },
        { type: "fresh2", seq: 2 },
        { type: "fresh3", seq: 3 },
      ],
    });
    expect(received.map((event) => event.type)).toEqual(["fresh1", "fresh2", "fresh3"]);
    expect(localStorage.getItem(CURSOR_KEY)).toBe("3");
  });

  it("首连的 status.cursor 作为基线落盘", async () => {
    const { harness } = await connectClient();
    harness.json({ type: "status", cursor: 42, data: {} });
    expect(localStorage.getItem(CURSOR_KEY)).toBe("42");
  });

  it("cursor 单调不回退：更小的 status.cursor 不能覆盖已推进的进度", async () => {
    const { harness } = await connectClient();
    harness.json({ type: "agent_say", text: "x", seq: 10 });
    expect(localStorage.getItem(CURSOR_KEY)).toBe("10");
    harness.json({ type: "status", cursor: 3, data: {} });
    expect(localStorage.getItem(CURSOR_KEY)).toBe("10");
  });

  it("非安全整数的 seq 不参与分发与推进", async () => {
    const { harness, received } = await connectClient();
    harness.json({
      type: "agent_events",
      cursor: 1,
      items: [
        { type: "bad-string-seq", seq: "3" },
        { type: "bad-float-seq", seq: 2.5 },
        { type: "good", seq: 1 },
      ],
    });
    expect(received.map((event) => event.type)).toEqual(["good"]);
    expect(localStorage.getItem(CURSOR_KEY)).toBe("1");
  });

  it("本地 cursor 是垃圾值时按首次连接处理，不发 replay", async () => {
    const { harness, received } = await connectClient("not-a-number");
    expect(harness.sent).toEqual([{ type: "get_status", hydrate: true }]);
    harness.json({ type: "agent_events", cursor: 2, items: [{ type: "a", seq: 1 }] });
    expect(received.map((event) => event.type)).toEqual(["a"]);
  });
});

// ─────────────────────────── ④ 错误路径 ───────────────────────────

describe("错误路径", () => {
  it("normalizeLocalBaseUrl 拒绝 https（V0 只支持本机 http runtime）", () => {
    expect(() => normalizeLocalBaseUrl("https://127.0.0.1:8080")).toThrow(/只支持本机 http/);
  });

  it("normalizeLocalBaseUrl 拒绝非回环地址", () => {
    expect(() => normalizeLocalBaseUrl("http://10.0.0.9:8080")).toThrow(/127\.0\.0\.1/);
    expect(() => normalizeLocalBaseUrl("evil.example.com")).toThrow(/127\.0\.0\.1/);
  });

  it("normalizeLocalBaseUrl 归一化：补 scheme、抹掉 path/query/hash、去尾斜杠", () => {
    expect(normalizeLocalBaseUrl("   ")).toBe("http://127.0.0.1:8080");
    expect(normalizeLocalBaseUrl("localhost:8080")).toBe("http://localhost:8080");
    expect(normalizeLocalBaseUrl("http://127.0.0.1:8080/agent?x=1#z")).toBe("http://127.0.0.1:8080");
  });

  it("没连上就 send 要报错，而不是静默把消息丢掉", async () => {
    const client = new GatewayClient(SETTINGS);
    await expect(client.send({ type: "get_status" })).rejects.toThrow("尚未连接 runtime");
  });

  it("HTTP 失败：JSON 错误体取 detail 当消息", async () => {
    mockHttpFailure(403, JSON.stringify({ detail: "工作目录之外的路径被拒绝" }));
    const client = new GatewayClient(SETTINGS);
    await expect(client.listSessions()).rejects.toThrow("工作目录之外的路径被拒绝");
  });

  it("HTTP 失败：detail 是对象时取里面的 message", async () => {
    mockHttpFailure(400, JSON.stringify({ detail: { message: "hook 阻断了这次执行" } }));
    const client = new GatewayClient(SETTINGS);
    await expect(client.listSessions()).rejects.toThrow("hook 阻断了这次执行");
  });

  it("HTTP 失败：非 JSON 错误体原样透出", async () => {
    mockHttpFailure(500, "Internal Server Error");
    const client = new GatewayClient(SETTINGS);
    await expect(client.listSessions()).rejects.toThrow("Internal Server Error");
  });

  it("HTTP 失败：空错误体退化成带状态码的兜底消息", async () => {
    mockHttpFailure(503, "");
    const client = new GatewayClient(SETTINGS);
    await expect(client.listSessions()).rejects.toThrow("runtime request failed (503)");
  });
});

// ─────────────────────────── 会话 id ───────────────────────────

describe("会话 id", () => {
  it("每次生成都不同", () => {
    expect(createSessionId()).not.toBe(createSessionId());
  });

  it("环境没有 crypto.randomUUID 时退化到时间戳 + 随机串", () => {
    vi.stubGlobal("crypto", {});
    expect(createSessionId()).toMatch(/^desktop-\d+-[a-z0-9]+$/);
  });
});

// ─────────────────────── ⑤ 确认关闭（v10）───────────────────────
describe("确认关闭事件", () => {
  it("agent_confirm_closed 原样分发给客户端", async () => {
    const { harness, received } = await connectClient();
    harness.json({ type: "agent_confirm", id: "c1", text: "跑命令？", tainted: false });
    harness.json({ type: "agent_confirm_closed", id: "c1", reason: "timeout" });
    expect(received.map((e) => e.type)).toContain("agent_confirm_closed");
    const closed = received.find((e) => e.type === "agent_confirm_closed");
    expect(closed?.id).toBe("c1");
    expect(closed?.reason).toBe("timeout");
  });
});

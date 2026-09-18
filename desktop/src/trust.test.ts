/**
 * 授权档位的前端侧（GatewayClient.getTrust/setTrust）。
 *
 * 与 gateway.test.ts 同样的硬约束：不起服务、不联网，`@tauri-apps/plugin-http` 全 mock。
 * 断的是"请求怎么发、返回怎么用"，判定本身在后端内核（src/agents/trust.py）。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import { GatewayClient } from "./gateway";
import type { TrustStatus } from "./types";

vi.mock("@tauri-apps/plugin-http", () => ({ fetch: vi.fn() }));
vi.mock("@tauri-apps/plugin-websocket", () => ({
  default: { connect: vi.fn() },
}));

const SETTINGS = { baseUrl: "http://127.0.0.1:8080", token: "" };

const STATUS: TrustStatus = {
  level: "full",
  effective: "reads",
  ceiling: "reads",
  levels: ["ask", "reads", "full"],
  capability_profile: "external",
  workspace_scope: "project",
};

function mockJson(body: unknown, ok = true, status = 200) {
  vi.mocked(tauriFetch).mockResolvedValue({
    ok,
    status,
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
  });
});

describe("授权档位", () => {
  it("读取时返回当前档位、生效档位与上限", async () => {
    mockJson(STATUS);
    const status = await new GatewayClient(SETTINGS).getTrust();
    expect(status.level).toBe("full");
    expect(status.effective).toBe("reads");   // 被能力档案夹过
    expect(vi.mocked(tauriFetch).mock.calls[0][0]).toContain("/api/trust");
  });

  it("写入时用 PUT 带上 level", async () => {
    mockJson({ ...STATUS, level: "reads", effective: "reads" });
    const status = await new GatewayClient(SETTINGS).setTrust("reads");
    const [, init] = vi.mocked(tauriFetch).mock.calls[0];
    expect((init as RequestInit).method).toBe("PUT");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ level: "reads" });
    expect(status.level).toBe("reads");
  });

  it("后端拒绝非法档位时把原因抛出来", async () => {
    mockJson({ detail: "level 必须是 ['ask', 'reads', 'full'] 之一" }, false, 400);
    await expect(new GatewayClient(SETTINGS).setTrust("full")).rejects.toThrow(/必须是/);
  });
});

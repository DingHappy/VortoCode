import { describe, expect, it } from "vitest";
import type { DesktopProjectProfile } from "../types";
import { ASSISTANT_HOST_ROOT, findAssistantHost, isAssistantHost, isLocalNetworkBlocked, mainChatKey, workProjects } from "./assistantHost";
import { STORAGE_KEYS } from "./storage";

const project = (id: string, kind: "local" | "remote", repoRoot: string): DesktopProjectProfile =>
  ({ id, name: id, kind, repoRoot, baseUrl: "http://192.168.1.20:8080", lastOpenedAt: 0 });

describe("assistantHost", () => {
  const list = [project("repo", "local", "/Users/me/repo"), project("srv", "remote", "/home/me/VortoCode"), project("host", "remote", ASSISTANT_HOST_ROOT)];

  it("只有标了助理目录的远端才是常驻电脑；本机目录碰巧同名也不算", () => {
    expect(list.map(isAssistantHost)).toEqual([false, false, true]);
    expect(isAssistantHost(project("x", "local", ASSISTANT_HOST_ROOT))).toBe(false);
    expect(findAssistantHost(list)?.id).toBe("host");
    expect(findAssistantHost(list.slice(0, 2))).toBeUndefined();
  });

  it("常驻电脑不出现在工作台的项目列表里（它没有项目目录，不能当项目打开）", () => {
    expect(workProjects(list).map((item) => item.id)).toEqual(["repo", "srv"]);
  });

  it("本机与每台常驻电脑各有自己的主要聊天", () => {
    expect(mainChatKey(null)).toBe(STORAGE_KEYS.mainChatSid);
    expect(mainChatKey("host")).toBe(`${STORAGE_KEYS.mainChatSid}:host`);
  });

  it("只有「本地网络」拦截才换成「重新打开 VortoCode」，别的原因照常重新连接", () => {
    expect(isLocalNetworkBlocked("这台 Mac 拒绝直连过去（No route to host）。如果终端或别的程序能连上，多半是 macOS「本地网络」没放行 VortoCode")).toBe(true);
    expect(isLocalNetworkBlocked("常驻电脑拒绝连接：VortoCode 服务可能没在运行")).toBe(false);
    expect(isLocalNetworkBlocked(null)).toBe(false);
  });
});

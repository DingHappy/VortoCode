import { describe, expect, it } from "vitest";
import type { DesktopProjectProfile } from "../types";
import { ASSISTANT_HOST_ROOT } from "./assistantHost";
import { listedProjects, localTwin, projectKey, residentEntry, residentTwin } from "./residentPlace";

const p = (id: string, kind: "local" | "remote", repoRoot: string, baseUrl = "http://127.0.0.1:8080"): DesktopProjectProfile =>
  ({ id, name: repoRoot.split("/").pop() || id, kind, repoRoot, baseUrl, lastOpenedAt: 0 });

const host = p("host", "remote", ASSISTANT_HOST_ROOT, "http://192.168.10.97:8081");
const local = p("local", "local", "/Users/me/code/VortoCode");
const resident = p("res", "remote", "/home/me/projects/vortocode", "http://192.168.10.97:8082");
const otherServer = p("other", "remote", "/srv/VortoCode", "http://192.168.10.50:8080");
const solo = p("solo", "remote", "/home/me/projects/Notes", "http://192.168.10.97:8083");

describe("residentPlace", () => {
  it("按目录名（不分大小写）把本机与常驻电脑上的同一个项目认作一对", () => {
    expect(projectKey(local)).toBe("vortocode");
    const all = [host, local, resident, otherServer];
    expect(residentTwin(local, all, host)?.id).toBe("res");           // 别的服务器上的同名项目不算
    expect(localTwin(resident, all)?.id).toBe("local");
    expect(residentTwin(p("x", "local", "/Users/me/code/Other"), all, host)).toBeUndefined();
  });

  it("常驻电脑登记表里的同名项目", () => {
    expect(residentEntry(local, [{ name: "VortoCode", repo_root: "/home/me/projects/VortoCode", port: 8082 }])?.port).toBe(8082);
    expect(residentEntry(local, [{ name: "Notes", repo_root: "/home/me/projects/Notes", port: 8083 }])).toBeUndefined();
  });

  it("项目列表：去掉常驻助理；本机有同名时常驻电脑那份不重复列出，只在常驻电脑上的照常列出", () => {
    expect(listedProjects([host, local, resident, otherServer, solo], host).map((item) => item.id)).toEqual(["local", "other", "solo"]);
    expect(listedProjects([host, local, resident]).map((item) => item.id)).toEqual(["local", "res"]);   // 没有常驻电脑：不合并
  });
});

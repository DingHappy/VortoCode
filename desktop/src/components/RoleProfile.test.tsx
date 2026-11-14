import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RoleProfileView } from "./RoleProfile";
import { roleProfileSnapshot } from "../lib/roleProfileSnapshot";
import { settingsTargetLease } from "../lib/settingsTarget";
import { desktopContext } from "../lib/desktopContext";

const sections = [
  { id: "settings" as const, label: "设置", content: <button>编辑资料</button> },
  { id: "access" as const, label: "工具与权限", content: <p>登记的权限</p> },
  { id: "memory" as const, label: "记忆", content: <p>原来的记忆</p> },
];

it("gives the main assistant and registered roles the same accessible profile surface", () => {
  const html = renderToStaticMarkup(<RoleProfileView name="研究助手" description="只读公开资料" character="research" sections={sections} current onClose={() => {}} />);
  expect(html).toContain("research.png");
  expect(html).toContain('role="tablist"');
  expect(html.match(/role="tab"/g)).toHaveLength(3);
  expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
  expect(html.match(/role="tabpanel"/g)).toHaveLength(3);
  expect(html).toContain("关闭角色资料");
});

it("locks old actions when the target changes while keeping the profile identifiable and closable", async () => {
  const source = { registrationId: "a", baseUrl: "https://assistant.example", sid: "main", generation: 1, epoch: 1 };
  let context = desktopContext({ registrationId: "a", assistantId: "a", scope: "general", repoRoot: "", sid: "main", baseUrl: source.baseUrl,
    profiles: [], runtimeId: "", connection: "connected", localResidentId: null, source });
  const client = { connectionEpoch: 1, desktopSource: source, save: async () => {} };
  const lease = settingsTargetLease(() => ({ context, client }));
  context = { ...context, readKey: "different-connection" };
  const html = renderToStaticMarkup(<RoleProfileView name="原来的角色" description="上次资料" sections={sections} current={lease.current()} onClose={() => {}} />);
  expect(html).toContain("原来的角色");
  expect(html).toContain("运行端或连接已变化");
  expect(html.match(/<fieldset disabled=""/g)).toHaveLength(3);
  await expect(lease.client!.save()).rejects.toThrow("设置目标已变化");
});

it("freezes display data so an old role dialog never adopts another space's new profile or memories", () => {
  const profile = { name: "空间 A", soul: "A 的说明", about_you: "A 的资料" };
  const memories = [{ id: "a", content: "A 的记忆", created_at: "2026-11-14T09:00:00Z" }];
  const frozen = roleProfileSnapshot(profile, null, memories, []);
  profile.name = "空间 B";
  memories[0].content = "B 的记忆";
  expect(frozen.profile.name).toBe("空间 A");
  expect(frozen.memories[0].content).toBe("A 的记忆");
});

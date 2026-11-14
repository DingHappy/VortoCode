import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RoleAvatar, staffCharacter, ROLE_ART } from "./RoleAvatar";
import { RoleNavigation } from "./RoleNavigation";
import { AssistantAvatarImage, AssistantSidebar } from "./AssistantMode";
import type { StaffRole } from "../lib/staff";

const role: StaffRole = { id: "public-researcher", title: "研究", brief: "只读公开资料", enabled: true, revision: 1,
  builtin: true, grants: { operations: ["read"], tools: [], sees: [], web: true, project: false },
  engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };

it("uses real role records and does not invent a family of active workers", () => {
  for (const [character, src] of Object.entries(ROLE_ART)) {
    expect(renderToStaticMarkup(<RoleAvatar character={character as keyof typeof ROLE_ART} />)).toContain(src);
  }
  const props = { name: "总助手名字", directory: null, connected: true, onAssistant: () => {}, onMember: () => {}, onAdd: () => {} };
  const empty = renderToStaticMarkup(<RoleNavigation {...props} />);
  expect(empty).toContain("总助手名字");
  expect(empty).not.toContain(ROLE_ART.research);
  const registered = renderToStaticMarkup(<RoleNavigation {...props} directory={{ roles: [role], proposals: [], members: [
    { id: "reader", name: "公开研究", role: role.id, engine: "vortocode", model: "", revision: 1, status: "active", busy: 0, recent_tasks: [] },
  ] }} />);
  expect(registered).toContain("公开研究");
  expect(registered).toContain(ROLE_ART.research);
});

it("keeps owner custom avatars ahead of the default art and rejects unsupported image sources", () => {
  const custom = { type: "image/png", data: "AAAA" };
  const html = renderToStaticMarkup(<AssistantAvatarImage name="个人助手" avatar={custom} />);
  expect(html).toContain("data:image/png;base64,AAAA");
  expect(html).not.toContain(ROLE_ART.general);
  expect(renderToStaticMarkup(<RoleAvatar character="general" avatar={{ ...custom, type: "image/svg+xml" }} />)).toContain(ROLE_ART.general);
});

it("uses registered role identities and stays neutral for unknown or misleading custom roles", () => {
  expect(staffCharacter(role)).toBe("research");
  expect(staffCharacter({ ...role, id: "calendar-reader" })).toBe("calendar");
  expect(staffCharacter({ ...role, id: "developer" })).toBe("development");
  expect(staffCharacter({ ...role, builtin: false, title: "开发助手" })).toBeUndefined();
  expect(staffCharacter({ ...role, id: "unknown" })).toBeUndefined();
  expect(staffCharacter(undefined)).toBeUndefined();
  expect(renderToStaticMarkup(<RoleAvatar />)).not.toContain("<img");
});

it("shows one primary chat entry and one waiting count, without a separate staff destination", () => {
  const props = { mainChat: { sid: "main" }, current: "main", waiting: 2, sideChats: [],
    onSelect: () => {}, onNewSideChat: () => {}, onPage: () => {} };
  const html = renderToStaticMarkup(<AssistantSidebar {...props} />);
  expect(html.match(/>主要聊天</g)).toHaveLength(1);
  expect(html.match(/aria-label="2 件等你处理"/g)).toHaveLength(1);
  expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  expect(html).not.toContain('>专员</span>');
});

import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { RoleAvatar, staffCharacter, ROLE_ART } from "./RoleAvatar";
import { RoleFamily } from "./StaffWorkspace";
import { AssistantAvatarImage, AssistantSidebar } from "./AssistantMode";
import type { StaffRole } from "../lib/staff";

const role: StaffRole = { id: "public-researcher", title: "研究", brief: "只读公开资料", enabled: true, revision: 1,
  builtin: true, grants: { operations: ["read"], tools: [], sees: [], web: true, project: false },
  engine: ["vortocode"], max_steps: 12, timeout_seconds: 600 };

it("renders all four chosen characters without inventing enrollments or dispatch actions", () => {
  const html = renderToStaticMarkup(<RoleFamily />);
  for (const [character, src] of Object.entries(ROLE_ART)) {
    expect(html).toContain(src);
    expect(renderToStaticMarkup(<RoleAvatar character={character as keyof typeof ROLE_ART} />)).toContain(src);
  }
  expect(html).toContain("实际能力以你批准的岗位为准");
  expect(html).not.toContain("<button");
  expect(html).not.toContain("已启用");
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

it("shows one primary chat entry and one waiting count, while staff selection clears chat selection", () => {
  const props = { mainChat: { sid: "main" }, current: "main", waiting: 2, sideChats: [],
    onSelect: () => {}, onNewSideChat: () => {}, onPage: () => {}, onManageStaff: () => {} };
  const html = renderToStaticMarkup(<AssistantSidebar {...props} />);
  expect(html.match(/>主要聊天</g)).toHaveLength(1);
  expect(html.match(/aria-label="2 件等你处理"/g)).toHaveLength(1);
  expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  const staff = renderToStaticMarkup(<AssistantSidebar {...props} staffOpen />);
  expect(staff.match(/aria-current="page"/g)).toHaveLength(1);
  expect(staff).toMatch(/aria-current="page"[^>]*>.*?专员/);
});

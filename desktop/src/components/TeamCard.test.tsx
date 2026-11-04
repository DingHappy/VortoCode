import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TeamCard, type TeamClient } from "./TeamCard";
import type { TeamDetail } from "../types";

const client = { getTeam: async () => { throw new Error("offline"); } } as unknown as TeamClient;
const team = (over: Partial<TeamDetail> = {}): TeamDetail => ({
  id: "task-1", objective: "给 resolve_within 补测试", status: "review", mode: "private", waiting_reason: "",
  used: { tasks: 1, rounds: 2, timeout_seconds: 1800, wakes: 4 }, limits: { tasks: 8, rounds: 12, timeout_seconds: 3600, wakes: 16 },
  members: [{ name: "开发", role: "developer", engine: "opencode" }, { name: "审查员", role: "reviewer", engine: "vortocode" }],
  children: [{ id: "task-c", member: "开发", status: "done", round: 2, tainted: false, review: "accepted",
    result: "<b>加了一条测试</b>", request: "补测试",
    delivery: { branch: "vorto/team-task-c-r2", verified: true, files: ["tests/unit/test_paths.py"], preexisting_failures: 37 } }],
  tainted: false, acceptance: ["新增测试并通过"], project: { name: "VortoCode", repo_root: "/home/me/projects/VortoCode", port: 8082 },
  events: [{ kind: "assigned", text: "开发：补测试", seq: 2 }],
  submission: { summary: "测试已加", evidence: [{ criterion: 0, evidence: "分支 vorto/team-task-c-r2，验证通过" }], tainted: false },
  ...over,
});
const render = (detail: TeamDetail, onPull?: (p: NonNullable<TeamDetail["project"]>, b: string) => Promise<string>) =>
  renderToStaticMarkup(<TeamCard id={detail.id} client={client} connected initial={detail} onPull={onPull} />);

it("shows status, members with their engine, budget, delivery branch and review actions", () => {
  const html = render(team(), async () => "ok");
  for (const text of ["交付了，等你验收", "开发（开发，OpenCode）", "审查员（审查员）", "轮次 2/12", "累计时限 30/60 分钟",
    "vorto/team-task-c-r2", "验证：通过", "另有 37 个测试改动前就失败", "拉回本机", "验收通过", "退回", "停止小组",
    "新增测试并通过：", "协调人已接受"]) expect(html).toContain(text);
  expect(html).not.toContain("<b>"); expect(html).toContain("&lt;b&gt;");
});

it("without a local twin it says how to pull instead of offering the button", () => {
  const html = render(team());
  expect(html).not.toContain("拉回本机</button>"); expect(html).toContain("拉不回来");
});

it("waiting asks for a reply; closed teams cannot be stopped; tainted content is flagged", () => {
  const waiting = render(team({ status: "waiting", waiting_reason: "额度用完了，要加预算吗？", submission: null }));
  expect(waiting).toContain("额度用完了，要加预算吗？"); expect(waiting).toContain("回复小组"); expect(waiting).toContain("加预算，在对话里");
  const done = render(team({ status: "done", tainted: true }));
  expect(done).not.toContain("停止小组"); expect(done).not.toContain("验收通过</button>");
  expect(done).toContain("读过外部内容");
});

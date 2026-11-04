import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TeamChat, type TeamChatClient } from "./TeamChat";
import { AssistantSidebar } from "./AssistantMode";
import type { TeamTranscript } from "../types";

const client = { getTeamTranscript: async () => { throw new Error("offline"); } } as unknown as TeamChatClient;
const at = (minute: number) => `2026-11-04T07:${String(minute).padStart(2, "0")}:00+00:00`;
const data = (over: Partial<TeamTranscript["team"]> = {}): TeamTranscript => ({
  team: { id: "task-1", objective: "调研今日股市", status: "active", mode: "open", waiting_reason: "", used: { tasks: 3, rounds: 4 },
    project: null, tainted: true, acceptance: ["覆盖主要指数"], ...over },
  members: [
    { name: "研究员", role: "web-researcher", title: "研究员", engine: "opencode", state: "working", current: "task-c", rounds: 2, delivered: 1, failures: 1 },
    { name: "审查员", role: "reviewer", title: "审查员", engine: "vortocode", state: "idle", rounds: 1, delivered: 1, failures: 0 },
  ],
  messages: [
    { at: at(1), speaker: "system", kind: "created", text: "小组开工：调研今日股市" },
    { at: at(2), speaker: "coordinator", kind: "assigned", mention: "研究员", member: "研究员", text: "查今日指数点位" },
    { at: at(5), speaker: "member", kind: "result", member: "研究员", engine: "opencode", round: 1, tainted: true,
      text: "<b>上证</b> 3686.17（+2.16%）" + "很长".repeat(400) },
    { at: at(6), speaker: "coordinator", kind: "rework", mention: "研究员", member: "研究员", text: "链接缺日期" },
    { at: at(9), speaker: "member", kind: "failed", member: "研究员", engine: "opencode", round: 2, text: "超过单轮时限" },
    { at: at(10), speaker: "owner", kind: "owner_reply", text: "也看看港股" },
  ],
});
const render = (transcript: TeamTranscript) => renderToStaticMarkup(<TeamChat id="task-1" client={client} connected initial={transcript} />);

it("reads like a group chat: members with framework and state, then who said what", () => {
  const html = render(data());
  for (const text of ["调研今日股市", "进行中", "成员 3", "协调人", "研究员", "OpenCode", "在做 · 做了 2 轮、交回 1 次、失败 1 次",
    "空闲", "@研究员 ", "查今日指数点位", "@研究员 返工：", "这一轮没有交回：", "超过单轮时限", "也看看港股", "外部内容",
    "展开全文", "在群里说点什么"]) expect(html).toContain(text);
  expect(html).not.toContain("<b>上证"); expect(html).toContain("&lt;b&gt;上证");
});

it("delivery asks for a decision; closed groups only keep the record", () => {
  const review = render(data({ status: "review" }));
  expect(review).toContain("验收通过"); expect(review).toContain("退回"); expect(review).not.toContain("在群里说点什么");
  const done = render(data({ status: "done" }));
  expect(done).toContain("小组已结束（你已验收）"); expect(done).not.toContain("发送</button>");
  const waiting = render(data({ status: "waiting", waiting_reason: "成员连续 3 次没有交回结果" }));
  expect(waiting).toContain("成员连续 3 次没有交回结果"); expect(waiting).toContain("回复小组的问题");
});

it("each team shows up in the sidebar's recent list like a group chat", () => {
  const html = renderToStaticMarkup(<AssistantSidebar mainChat={{ sid: "m" }} sideChats={[]} current="m" onSelect={() => {}} onNewSideChat={() => {}}
    teams={[{ id: "t1", title: "调研今日股市", status: "review" }, { id: "t2", title: "补测试", status: "active" }]} currentTeam="t1" />);
  expect(html).toContain("调研今日股市"); expect(html).toContain("待验收"); expect(html).toContain("补测试");
  expect(html).toContain("任务小组：调研今日股市");
});

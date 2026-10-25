import { describe, expect, it } from "vitest";
import type { CodexAction, TaskItem } from "../types";
import { assistantFeed, codexActionFeedItem, confirmationFeedItem, dispatchFeedItem, noticeFeedItem, noticeFeedItems, feedStatus, feedTime, firstSentence, groupFeed, taskFeedItem, turnFeedItem, type FeedItem } from "./activityFeed";

const task = (overrides: Partial<TaskItem>): TaskItem => ({ id: "t1", status: "done", ...overrides });
const question = (status: "open" | "answered") => ({ id: "q", task_id: "t1", round: 1, status, question: "要删除哪几条帖子？请列出链接。", options: [], context: "", asked_by: "worker", answerer: "owner" as const, created: "", expires_at: "", answer: "", answered_at: "" });

describe("firstSentence", () => {
  it("取第一个非空行的第一句，去掉 Markdown 与代码块，超长截断", () => {
    expect(firstSentence("## 结论\n\n完成了静态审查。另外还有别的。", 40)).toBe("结论");
    expect(firstSentence("```py\nx=1\n```\n已归档记录；下一步待定", 40)).toBe("已归档记录；");
    expect(firstSentence("一二三四五六七八九十", 6)).toBe("一二三四五…");
    expect(firstSentence(undefined, 10)).toBe("");
    // 标识符里的下划线不是 Markdown：截图里 timeout_scope 曾被显示成 timeoutscope
    expect(firstSentence("补了 `timeout_scope` / **add_note** 两个垫片。", 40)).toBe("补了 timeout_scope / add_note 两个垫片。");
    expect(firstSentence("- 见 [报告](https://x/y) 第二节", 40)).toBe("见 报告 第二节");
  });
});

describe("feedStatus / taskFeedItem", () => {
  it("有待回答的问题或等验收 → 等你处理，且优先于运行状态", () => {
    expect(feedStatus(task({ status: "running", development: { version: 1, round: 1, owner_session: "", plan_id: "", root_task_id: "", questions: [question("open")] } }))).toBe("waiting");
    expect(feedStatus(task({ status: "done", collaboration: { assignee: "", round: 1, review: "pending", acceptance: [], tainted: false, messages: [] } }))).toBe("waiting");
    expect(feedStatus(task({ status: "running", development: { version: 1, round: 1, owner_session: "", plan_id: "", root_task_id: "", questions: [question("answered")] } }))).toBe("running");
    expect(taskFeedItem(task({ status: "queued" }))).toMatchObject({ status: "running", statusLabel: "排队中" });
    expect(taskFeedItem(task({ status: "queued", development: { version: 1, round: 1, owner_session: "", plan_id: "", root_task_id: "", questions: [question("open")] } })))
      .toMatchObject({ status: "waiting", statusLabel: undefined });
  });

  it("后端七种状态全部有归属", () => {
    expect(["queued", "running", "done", "failed", "cancelled", "paused", "interrupted"].map((status) => feedStatus(task({ status }))))
      .toEqual(["running", "running", "done", "failed", "failed", "stopped", "stopped"]);
  });

  it("标题取请求第一句，摘要按状态取结果/错误/问题的第一句", () => {
    const done = taskFeedItem(task({ prompt: "调研 vortocode 项目。顺便看看测试", result: "完成了静态审查并归档记录。细节见报告。", updated_at: "2026-10-11T12:59:00" }));
    expect(done).toMatchObject({ title: "调研 vortocode 项目。", summary: "完成了静态审查并归档记录。", status: "done", at: "2026-10-11T12:59:00" });
    expect(taskFeedItem(task({ status: "failed", error: "网络超时" })).summary).toBe("网络超时");
    expect(taskFeedItem(task({ status: "cancelled" })).summary).toBe("已取消");
    expect(taskFeedItem(task({ status: "paused" })).summary).toBe("已暂停，可以继续");
    expect(taskFeedItem(task({ status: "running", development: { version: 1, round: 1, owner_session: "", plan_id: "", root_task_id: "", questions: [question("open")] } })).summary)
      .toBe("等你回复：要删除哪几条帖子？");
    expect(taskFeedItem(task({ prompt: "" })).title).toBe("未命名任务");
    expect(taskFeedItem(task({ status: "done", result: "" }))).toMatchObject({ summary: "执行结束，请查看结果", statusLabel: "执行结束" });
  });
});

describe("groupFeed", () => {
  const now = new Date(2026, 9, 11, 13, 0);
  const item = (id: string, status: FeedItem["status"], at: string): FeedItem => ({ id, title: id, summary: "", status, at });

  it("等你处理置顶，其余按今天 / 昨天 / 日期 / 更早分组且组内时间倒序", () => {
    const groups = groupFeed([
      item("a", "done", "2026-10-11T09:00:00"), item("b", "done", "2026-10-11T12:00:00"),
      item("c", "failed", "2026-10-10T11:00:00"), item("d", "waiting", "2026-10-09T08:00:00"),
      item("e", "done", "2026-10-02T08:00:00"), item("f", "done", ""),
    ], now);
    expect(groups.map((group) => group.label)).toEqual(["等你处理", "今天", "昨天", "10月2日", "更早"]);
    expect(groups[1].items.map((entry) => entry.id)).toEqual(["b", "a"]);
  });

  it("时间只显示几点几分；没有时间就留空", () => {
    expect(feedTime(item("a", "done", "2026-10-11T09:05:00"))).toBe("09:05");
    expect(feedTime(item("b", "done", "not-a-date"))).toBe("");
  });

  it("置顶待处理事项跨天补日期，跨年补年份；当日不重复日期", () => {
    expect(feedTime(item("old", "waiting", "2026-10-09T09:05:00"), now)).toBe("10月9日 09:05");
    expect(feedTime(item("year", "waiting", "2025-10-09T09:05:00"), now)).toBe("2025年10月9日 09:05");
    expect(feedTime(item("today", "waiting", "2026-10-11T09:05:00"), now)).toBe("09:05");
  });
});

describe("turnFeedItem", () => {
  const turn = (overrides: Partial<import("../types").AgentTurn>): import("../types").AgentTurn => ({
    sid: "abc", session_title: "主要聊天", rid: "r1", title: "查询宁波天气", outcome: "给出今明两天天气",
    digest_source: "model", status: "done", tools: 1, tool_names: [], at: "2026-10-11T02:00:00+00:00", ...overrides });

  it("按用过的工具选类别：联网 > 改代码 > 跑命令 > 普通对答", () => {
    expect(turnFeedItem(turn({ tool_names: ["read_file", "web_search"] })).kind).toBe("web");
    expect(turnFeedItem(turn({ tool_names: ["write_file", "run_command"] })).kind).toBe("code");
    expect(turnFeedItem(turn({ tool_names: ["run_command"] })).kind).toBe("command");
    expect(turnFeedItem(turn({ tool_names: [] })).kind).toBe("chat");
  });

  it("手机上聊的标 📱，没完成时提示回手机继续", () => {
    expect(turnFeedItem(turn({ sid: "telegram-42" })).title).toBe("📱 查询宁波天气");
    expect(turnFeedItem(turn({ sid: "telegram-42", status: "no_reply" })).summary).toBe("没有完成，可以在手机上继续");
    expect(turnFeedItem(turn({})).title).toBe("查询宁波天气");
  });

  it("有回复记为完成并带结果句；没有回复记为已停下", () => {
    expect(turnFeedItem(turn({}))).toMatchObject({ id: "turn:abc:r1", status: "done", summary: "给出今明两天天气", session: "abc", rid: "r1" });
    expect(turnFeedItem(turn({ status: "no_reply" }))).toMatchObject({ status: "stopped", summary: "没有完成，可以在对话里继续" });
  });

  it("对话状态说明回复已生成，不声称工作已验收", () => {
    expect(turnFeedItem(turn({}))).toMatchObject({ statusLabel: "已回复", sourceLabel: "对话" });
    expect(turnFeedItem(turn({ sid: "telegram-42", status: "no_reply" }))).toMatchObject({ statusLabel: "未完成回复", sourceLabel: "手机对话" });
  });
});

describe("dispatchFeedItem", () => {
  const dispatch = (overrides: Partial<import("../types").AssistantDispatch>): import("../types").AssistantDispatch => ({
    id: "d1", project: "VortoCode", title: "修登录超时", goal: "改超时", criteria: [], status: "proposed", tainted: false,
    remote_task_id: "", result: "", error: "", branch: "", created_at: "2026-10-11T01:00:00Z", updated_at: "2026-10-11T01:05:00Z", ...overrides });

  it("等你确认下发的置顶并说明项目；含外部内容时提醒细看", () => {
    expect(dispatchFeedItem(dispatch({}))).toMatchObject({ status: "waiting", summary: "等你确认下发 · VortoCode", dispatchId: "d1", at: "2026-10-11T01:05:00Z" });
    expect(dispatchFeedItem(dispatch({ tainted: true }))?.summary).toContain("含外部内容");
  });

  it("下发后跟着远端状态走；撤回的不显示", () => {
    expect(dispatchFeedItem(dispatch({ status: "running" }))?.status).toBe("running");
    expect(dispatchFeedItem(dispatch({ status: "done", branch: "vorto/fix" }))?.summary).toBe("执行结束，等待审查 · vorto/fix");
    expect(dispatchFeedItem(dispatch({ status: "send_failed", error: "connection refused" }))).toMatchObject({ status: "failed" });
    expect(dispatchFeedItem(dispatch({ status: "cancelled" }))).toBeNull();
  });

  it("执行结束仍待审查，失联结果标待核对，不把缓存结果当成当前进度", () => {
    expect(dispatchFeedItem(dispatch({ status: "done" }))).toMatchObject({ statusLabel: "执行结束 · 待审查", sourceLabel: "任务单 · VortoCode" });
    expect(dispatchFeedItem(dispatch({ status: "done", sync_state: "unreachable" }))).toMatchObject({ statusLabel: "状态待核对" });
    expect(dispatchFeedItem(dispatch({ status: "submission_unknown" }))).toMatchObject({ statusLabel: "提交待核对" });
  });
});

describe("noticeFeedItem", () => {
  it("插入或重排其他通知不改变原记录身份", () => {
    const original = { ts: "2026-10-11T01:00:23Z", source: "assistant", text: "🗒 早报\n核对交付" };
    const other = { ...original, text: "🗒 晚报\n等待确认" };
    const before = noticeFeedItems([original, other]);
    const after = noticeFeedItems([other, { ...original, ts: "2026-10-12T01:00:23Z" }, original]);
    expect(after[2].id).toBe(before[0].id);
    expect(after[0].id).toBe(before[1].id);
    expect(new Set(after.map(item => item.id)).size).toBe(3);
  });

  it("同秒不同来源、不同正文及完全相同的通知都有独立展示键", () => {
    const original = { ts: "2026-10-11T01:00:23Z", source: "assistant", text: "🗒 早报\n核对交付" };
    const differentSource = { ...original, source: "cron:digest" };
    const same = noticeFeedItems([original, original]);
    const mixed = noticeFeedItems([differentSource, original, { ...original, text: "🗒 更新\n另一件事" }, original]);
    expect(new Set(mixed.map(item => item.id)).size).toBe(4);
    expect(mixed[1].id).toBe(same[0].id);
    expect(mixed[3].id).toBe(same[1].id);
  });
  it("保留完整纯文本供原地阅读，并区分定时结果与简报", () => {
    const text = "✅ cron [daily_digest] 通过\n第一条结果\n第二条结果 <script>example</script>";
    expect(noticeFeedItem({ ts: "2026-10-11T01:00:23Z", source: "cron:daily_digest", text }))
      .toMatchObject({ detail: text, sourceLabel: "定时任务", statusLabel: "执行结束" });
    expect(noticeFeedItem({ ts: "2026-10-11T01:00:23Z", source: "assistant", text: "🗒 早报\n待你确认" }))
      .toMatchObject({ detail: "🗒 早报\n待你确认", sourceLabel: "助理简报", statusLabel: "简报已生成" });
  });
  it("定时作业结果进动态（⏰），失败标失败；早晚报进动态（🗒）；其他通知不进", () => {
    expect(noticeFeedItem({ ts: "2026-10-11T01:00:23Z", source: "cron:daily_digest", text: "✅ cron [daily_digest] 通过\n今天 3 条新闻已整理" }))
      .toMatchObject({ title: "⏰ daily_digest", status: "done", summary: "今天 3 条新闻已整理" });
    expect(noticeFeedItem({ ts: "2026-10-11T01:00:23Z", source: "cron:relay_duty", text: "❌ cron [relay_duty] 失败\n超时" })?.status).toBe("failed");
    expect(noticeFeedItem({ ts: "2026-10-11T13:00:34Z", source: "assistant", text: "🗒 晚报\n有 1 件等你确认。" }))
      .toMatchObject({ title: "🗒 晚报", summary: "有 1 件等你确认。" });
    expect(noticeFeedItem({ ts: "2026-10-11T12:25:27Z", source: "assistant", text: "测试：推送通道已接通" })).toBeNull();
    expect(noticeFeedItem({ ts: "2026-10-11T12:25:27Z", source: "relay", text: "x" })).toBeNull();
  });
});

const action = (changes: Partial<CodexAction> = {}): CodexAction => ({ id: "a1", thread_id: "th1", thread_name: "核对发布",
  kind: "message", text: "先检查当前提交，汇报结果。", status: "proposed", tainted: false, session: "", error: "",
  created_at: "2026-10-11T01:00:00Z", updated_at: "2026-10-11T02:00:00Z", ...changes });

it("把 Codex 待批准动作变成置顶的可定位事项，保留外部内容提醒", () => {
  const item = codexActionFeedItem(action({ tainted: true }))!;
  expect(item).toMatchObject({ codexActionId: "a1", status: "waiting", statusLabel: "待审阅批准", sourceLabel: "Codex 动作" });
  expect(item.summary).toContain("含外部内容");
  expect(groupFeed([item], new Date("2026-10-11T12:00:00Z"))[0].key).toBe("waiting");
  expect(codexActionFeedItem(action({ kind: "interrupt" }))?.title).toContain("建议叫停");
});

it.each(["approved", "sending"] as const)("%s 动作保留待核对入口，不提示再次批准", status => {
  const item = codexActionFeedItem(action({ status }))!;
  expect(item).toMatchObject({ status: "waiting", statusLabel: "发送结果待核对" });
  expect(item.summary).toContain("不要重复发送");
});

it("受理、失败、过时分别显示；撤回动作不计入动态，未知状态不冒充完成", () => {
  expect(codexActionFeedItem(action({ status: "sent" }))).toMatchObject({ statusLabel: "请求已受理", summary: "请求已获受理，不代表 Codex 已完成任务" });
  expect(codexActionFeedItem(action({ status: "failed", error: "连接中断" }))).toMatchObject({ status: "failed", summary: "连接中断" });
  expect(codexActionFeedItem(action({ status: "expired" }))).toMatchObject({ status: "stopped", statusLabel: "已过时" });
  expect(codexActionFeedItem(action({ status: "cancelled" }))).toBeNull();
  expect(codexActionFeedItem(action({ status: "future" as CodexAction["status"] }))).toMatchObject({ status: "stopped", statusLabel: "状态待核对" });
});

it("操作确认可定位原卡片，保留污点提醒，不虚构事件时间", () => {
  const item = confirmationFeedItem({ id: "ask", text: "继续执行？", tainted: true });
  expect(item).toMatchObject({ confirmationId: "ask", status: "waiting", statusLabel: "待你确认", at: "" });
  expect(item.summary).toContain("含外部内容");
});

it.each(["sending", "awaiting", "failed"] as const)("确认 %s 时仍提供回执核对入口", responseState => {
  const item = confirmationFeedItem({ id: "ask", text: "继续执行？", tainted: false, responseState });
  expect(item).toMatchObject({ status: "waiting", statusLabel: "确认回执待核对" });
  expect(item.summary).toContain("不会自动重发批准");
});

it("assistant feed lists turns, then tasks, then the confirmation waiting on you", () => {
  const items = assistantFeed({ turns: [], dispatches: [], notices: [], codexActions: [],
    taskItems: [taskFeedItem(task({ id: "t1" })), taskFeedItem(task({ id: "t2", status: "running" }))],
    confirmation: { id: "ask", text: "继续？", tainted: false } });
  expect(items.map(item => item.taskId ?? item.confirmationId)).toEqual(["t1", "t2", "ask"]);
  expect(assistantFeed({ turns: [], dispatches: [], notices: [], codexActions: [], taskItems: [], confirmation: null })).toEqual([]);
});

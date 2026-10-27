import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import type { CodexAction } from "../types";
import { CodexActionCard } from "./CodexActionCard";

const action: CodexAction = { id: "cdx-1", thread_id: "th1", thread_name: "改登录超时", kind: "message",
  text: "先别推 main，开个分支", status: "proposed", tainted: true, session: "sid-a", error: "",
  created_at: "", updated_at: "" };

it("shows the exact text and lets you approve on the card itself", () => {
  const html = renderToStaticMarkup(<CodexActionCard action={action} connected onDecide={async () => {}} />);
  expect(html).toContain("先别推 main，开个分支");
  expect(html).toContain("批准发送");
  expect(html).toContain("不发");
  expect(html).toContain("外部内容");
});

it("finished actions show their outcome without buttons", () => {
  const html = renderToStaticMarkup(<CodexActionCard action={{ ...action, status: "failed", error: "它已经停了", tainted: false }}
    connected onDecide={async () => {}} />);
  expect(html).toContain("动作未能确认送达");
  expect(html).toContain("它已经停了");
  expect(html).not.toContain("批准发送");
});

it("expired drafts say why and offer no buttons", () => {
  const html = renderToStaticMarkup(<CodexActionCard action={{ ...action, status: "expired",
    error: "Codex 已经开始了新的一轮，这句是按上一轮拟的", tainted: false }} connected onDecide={async () => {}} />);
  expect(html).toContain("已过时");
  expect(html).toContain("新的一轮");
  expect(html).not.toContain("批准发送");
});

it.each(["approved", "sending", "failed", "sent"] as const)("shows receipt state %s without offering to send again", status => {
  const action = { id: "a1", thread_name: "Review", kind: "interrupt", status, text: "", error: "receipt uncertain" } as CodexAction;
  const html = renderToStaticMarkup(<CodexActionCard action={action} connected onDecide={async () => {}} />);
  expect(html).not.toContain("批准叫停");
  expect(html).not.toContain("已停止");
  expect(html).toContain(status === "sent" ? "受理" : status === "approved" ? "尚未确认" : status === "sending" ? "待核对" : "未能确认");
});

it("unknown outcomes say so, point at the Codex session, and offer no buttons", () => {
  const html = renderToStaticMarkup(<CodexActionCard action={{ ...action, status: "unknown", tainted: false,
    error: "交给 Codex 后一直没收到回执：可能已经发出，也可能没有。" }} connected onDecide={async () => {}} />);
  expect(html).toContain("结果无法确认");
  expect(html).toContain("去 Codex 窗口看一眼");
  expect(html).not.toContain("批准发送");
});

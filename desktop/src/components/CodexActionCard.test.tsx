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
  expect(html).toContain("没能交给 Codex");
  expect(html).toContain("它已经停了");
  expect(html).not.toContain("批准发送");
});

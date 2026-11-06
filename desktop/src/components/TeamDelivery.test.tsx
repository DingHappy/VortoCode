import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { TeamDelivery } from "./TeamDelivery";
import type { TeamDetail } from "../types";

const detail: TeamDetail = { id: "team", objective: "日报", status: "review", mode: "open", waiting_reason: "", used: {}, limits: {}, project: null,
  members: [], tainted: true, events: [], acceptance: ["附来源"], submission: { summary: "交付总结，不是完整正文", tainted: true, evidence: [{ criterion: 0, evidence: "参见 member 第2轮" }] },
  children: [{ id: "member", member: "研究员", status: "done", review: "accepted", round: 2, result: "## 成员完整正文", tainted: true }] };
const client = { getTeam: async () => detail };

it("separates saved summary, evidence and versioned member originals without claiming owner acceptance", () => {
  const html = renderToStaticMarkup(<TeamDelivery id="team" client={client} connected initial={detail} />);
  for (const label of ["协调人交付总结", "附来源", "研究员 · member · 第 2 轮", "协调人已接受", "成员完整正文", "不等于你已验收"]) expect(html).toContain(label);
  expect(html).not.toContain("<button>验收通过");
});

it("never displays an initial record for a different team", () => {
  const html = renderToStaticMarkup(<TeamDelivery id="another" client={client} connected initial={detail} />);
  expect(html).not.toContain("成员完整正文"); expect(html).toContain("正在读取交付");
});

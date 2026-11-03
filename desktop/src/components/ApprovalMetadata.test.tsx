import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ConfirmCard } from "./ComposerPanels";
import { AssistantAudit } from "./AssistantMode";
import { approvalCategory, approvalRisk, approvalSource } from "../lib/approval";

it("shows server classification without deriving risk from the operation text", () => {
  const html = renderToStaticMarkup(<ConfirmCard confirm={{ id: "c", text: "只是看看",
    tainted: true, operation_kind: "deliver", risk: "high", approval_scope: "once" }}
    connected trust={null} trustBusy={false} onTrust={() => {}} onCheck={() => {}} onAnswer={() => {}} />);
  expect(html).toContain("对外发送");
  expect(html).toContain("加强确认");
  expect(html).toContain("本按钮仅批准这一次");
  expect(html).toContain("外部内容回合需要人工确认");
});

it("keeps absent, new and prototype metadata visibly unknown", () => {
  for (const value of [undefined, null, [], "future_kind", "constructor", "__proto__"]) {
    expect(approvalCategory(value)).toMatch(/待核对|未分类/);
    expect(approvalRisk(value)).toBe("级别待核对");
    expect(approvalSource(value)).toBe("来源待核对");
  }
  expect(approvalCategory("own_data")).toBe("你的事项");
  expect(approvalRisk("normal")).toBe("普通确认");
  expect(approvalSource("preauthorized")).toBe("既有授权");
});

it("distinguishes a timed out card from a user denial in the existing audit panel", () => {
  const html = renderToStaticMarkup(<AssistantAudit entries={[{
    id: "a", category: "decision", operation: "App 审批卡 c", decision: "denied", tainted: true,
    operation_kind: "deliver", approval_scope: "once", decision_source: "timeout", approval_outcome: "expired",
  }]} />);
  expect(html).toContain("对外发送");
  expect(html).toContain("超时");
  expect(html).toContain("仅本次");
  expect(html).toContain("已过期，未放行");
  expect(html).not.toContain("已拒绝");
});

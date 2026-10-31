import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CodexGoalProgress } from "./CodexGoalProgress";
import { codexGoalProgress } from "../lib/codexGoalSupervision";
import { goalProgressFixture } from "../lib/codexGoalSupervision.fixtures";

function progress() {
  const { goal, snapshot } = goalProgressFixture(1000);
  return codexGoalProgress({ snapshot, goals: [goal], codexCurrent: true, goalsCurrent: true, now: 1000 }).items[0];
}
it("shows waiting reason, allowance and the real scope without offering execution or approval", () => {
  const html = renderToStaticMarkup(<CodexGoalProgress progress={progress()} onOpenGoal={() => {}} />);
  for (const text of ["等你处理", "本轮已结束", "已判断 1 / 4 次", "登录测试通过", "核对目标与验收", "跟进暂停不代表 Codex 已停止"])
    expect(html).toContain(text);
  expect(html).not.toContain("criterion-1");
  expect(html).not.toContain("重新执行");
});
it("unknown data shows historical counts and disables navigation when the goal source isn't current", () => {
  const item = { ...progress(), state: "unknown" as const, reason: "待核对", criteria: [], canOpenGoal: false };
  const html = renderToStaticMarkup(<CodexGoalProgress progress={item} onOpenGoal={() => {}} />);
  expect(html).toContain("上次记录："); expect(html).toContain('disabled=""');
  expect(html).not.toContain("本次跟进范围");
});
it("escapes remote text and never invents a zero budget for missing counters", () => {
  const item = { ...progress(), title: "<script>bad</script>", used: undefined, limit: undefined };
  const html = renderToStaticMarkup(<CodexGoalProgress progress={item} onOpenGoal={() => {}} />);
  expect(html).not.toContain("<script>"); expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain("已判断");
});

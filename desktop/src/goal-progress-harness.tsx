/** Development-only component acceptance preview, opened through ui-harness.html?view=goal-progress. */
import { useMemo, useState } from "react";
import { AssistantWorkday } from "./components/AssistantWorkday";
import { goalProgressFixture } from "./lib/codexGoalSupervision.fixtures";

export function GoalProgressHarness() {
  const [scenario, setScenario] = useState("等你验收"), [connected, setConnected] = useState(true), [selected, setSelected] = useState("");
  const fixture = useMemo(() => {
    const data = goalProgressFixture(), row = (data.snapshot.goal_supervision as Array<Record<string, unknown>>)[0];
    if (scenario === "无进展暂停" || scenario === "正在执行") {
      row.state = "paused"; row.reason = "相同验收缺口连续两轮没有新证据，已暂停；请核对后补充证据或重新交接。"; row.used = 2;
    }
    if (scenario === "正在执行") {
      data.snapshot.threads[0].status = "active"; data.snapshot.threads[0].turn!.status = "inProgress";
      (data.snapshot.threads[0].execution as { state: string }).state = "running";
    }
    if (scenario === "版本已变化") data.goal.revision++;
    if (scenario === "过期报告") data.snapshot.received_at = new Date(Date.now() - 31 * 60_000).toISOString();
    if (scenario === "长内容") {
      data.goal.objective = "修复登录并确认重新启动电脑和切换网络后仍能登录，核对所有相关验收记录与当前会话。".repeat(4);
      row.reason = "本轮已结束，仍需逐项核对登录测试及重启后的行为。请查看原目标里的验收依据，再决定后续工作。".repeat(4);
      data.snapshot.threads[0].cwd = "/projects/a-very-long-project-name-with-no-space/login-and-reconnection-regression-checks";
    }
    return data;
  }, [scenario]);
  const client = useMemo(() => ({ listPersonalGoals: async () => [structuredClone(fixture.goal)],
    getCodexProgress: async () => structuredClone(fixture.snapshot) }), [fixture]);
  return <main style={{ padding: "var(--vc-space-4)", background: "var(--vc-bg)", color: "var(--vc-text)", fontFamily: "var(--vc-font-sans)" }}>
    <h1 style={{ fontSize: "var(--vc-size-title)" }}>目标跟进 · 组件预览</h1>
    <p>全部为虚构记录，不连接服务、不发消息、不调用模型。</p>
    <nav aria-label="预览场景" style={{ display: "flex", flexWrap: "wrap", gap: "var(--vc-space-2)", marginBottom: "var(--vc-space-4)" }}>
      {["等你验收", "无进展暂停", "正在执行", "版本已变化", "过期报告", "长内容"].map(name => <button key={name}
        aria-pressed={scenario === name} onClick={() => { setScenario(name); setSelected(""); }}>{name}</button>)}
      <button onClick={() => setConnected(!connected)}>{connected ? "断开连接" : "恢复连接"}</button>
    </nav>
    <div className="vc-panel" style={{ width: "352px", maxWidth: "100%", padding: "var(--vc-space-4)", boxSizing: "border-box" }}>
      <AssistantWorkday client={client} scope="synthetic-general" connected={connected} dispatches={[]} dispatchesLoaded
        codexActions={[]} onRefreshTasks={async () => {}} onDispatch={() => {}}
        onGoals={id => setSelected(id === fixture.goal.id ? `已定位目标：${fixture.goal.objective}` : "定位失败")}
        onCodexDecision={async () => { throw new Error("Preview never approves actions"); }} />
      {selected && <p role="status">{selected}</p>}
    </div>
  </main>;
}

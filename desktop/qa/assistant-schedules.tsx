import { useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantSchedule } from "../src/components/AssistantMode";
import type { AssistantScheduleItem } from "../src/types";
import "../src/App.css";
import "../src/daily.css";
const initial: AssistantScheduleItem = { id: "cron-brief", kind: "cron", title: "每日项目简报", schedule: "at 09:00", enabled: true, revision: "v1", scheduler_enabled: false, last: "2026-10-12T09:00", detail: "汇总项目进度、待审查任务与失败记录" };
function Fixture() {
  const [item, setItem] = useState(initial);
  const [connected, setConnected] = useState(true);
  const [conflict, setConflict] = useState(false);
  const [calls, setCalls] = useState(0);
  return <main style={{ width: 320, margin: "20px auto", padding: 16 }}>
    <p>受控交互夹具，不调用真实模型或调度器。</p>
    <button onClick={() => setConnected(value => !value)}>切换连接</button>
    <button onClick={() => setConflict(true)}>模拟配置变化</button>
    <AssistantSchedule items={[item, { id: "heartbeat", kind: "heartbeat", title: "巡检", schedule: "每 30 分钟", enabled: true }]} connected={connected}
      onRefresh={async () => { setConflict(false); setItem(current => ({ ...current, revision: "v2" })); }}
      onToggle={async () => {
        setCalls(value => value + 1);
        await new Promise(resolve => setTimeout(resolve, 250));
        if (conflict) throw new Error("定时配置已变化，请刷新后再操作");
        setItem(current => ({ ...current, enabled: !current.enabled }));
      }}
      onHistory={async () => [{ id: "run-one", status: "failed", created: "2026-10-12T09:00:00Z", summary: "获取项目状态失败", error: "运行端暂时无法连接" },
        { id: "run-two", status: "done", created: "2026-10-11T09:00:00Z", summary: "简报已经生成", error: "" }]} />
    <output>启停请求次数：{calls}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

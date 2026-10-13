import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantTaskCard } from "../src/components/AssistantTaskCard";
import type { AssistantDispatch } from "../src/types";
import { AssistantActionGate, type AssistantTaskAction } from "../src/lib/assistantTask";
import "../src/App.css";
import "../src/daily.css";

const initial: AssistantDispatch = { id: "dsp-qa", project: "demo", title: "修复登录超时", goal: "将请求超时改为30秒，保留已有接口。",
  criteria: ["超时场景测试通过", "保持已有接口兼容"], status: "proposed", tainted: true,
  request_id: "qa-key", remote_task_id: "", branch: "", result: "", error: "", session: "sid-qa",
  created_at: "2026-10-12T12:00:00Z", updated_at: "2026-10-12T12:00:00Z" };

function Fixture() {
  const [task, setTask] = useState(initial);
  const [connected, setConnected] = useState(true);
  const [calls, setCalls] = useState(0);
  const [opened, setOpened] = useState(false);
  const [duplicate, setDuplicate] = useState(false);
  const owner = useRef({});
  const gate = useRef(new AssistantActionGate());
  const onAction = async (action: AssistantTaskAction) => gate.current.run(owner.current, "fixture", task.id, () => connected, async () => {
      setCalls(value => value + 1);
      if (action === "cancel") { setTask(current => ({ ...current, status: "cancelled" })); return; }
      if (action === "open") { setOpened(true); return; }
      if (action === "refresh") {
        setTask(current => ({ ...current, status: "done", branch: "vorto/fix-login", result: "测试通过（夹具）",
          error: "", sync_state: "current", last_checked_at: new Date().toISOString(),
          delivery_evidence: { head: "a".repeat(40), verified_head: "a".repeat(40), verification: "passed",
            coverage_known: true, accepted_hunks: 1, total_hunks: 2, verified_at: new Date().toISOString(),
            checked_at: new Date().toISOString() } })); return;
      }
      await new Promise(resolve => setTimeout(resolve, 400));
      setTask(current => ({ ...current, status: "running", remote_task_id: "task-qa", error: "",
        sync_state: "current", last_checked_at: new Date().toISOString() }));
  });
  return <main style={{ maxWidth: 640, margin: "20px auto", padding: 16 }}>
    <h1>任务卡交互验收</h1><p>受控夹具，操作不调用模型或实际项目。</p>
    <button onClick={() => setConnected(value => !value)}>切换连接</button>
    <button onClick={() => setTask({ ...initial, status: "submission_unknown", remote_task_id: "task-qa",
      sync_state: "unreachable", error: "请求响应丢失，提交结果待核对" })}>模拟响应丢失</button>
    <button onClick={() => setTask({ ...initial, status: "proposed" })}>重置任务</button>
    <button onClick={() => setTask(current => ({ ...current, delivery_evidence: current.delivery_evidence
      ? { ...current.delivery_evidence, verification: "stale", coverage_known: false } : null }))}>模拟证据过期</button>
    <button onClick={() => setDuplicate(value => !value)}>显示第二张任务卡</button>
    <div style={{ display: "grid", gridTemplateColumns: duplicate ? "repeat(2, minmax(0, 1fr))" : "1fr", gap: 12 }}>
      <AssistantTaskCard task={task} connected={connected} onAction={onAction} />
      {duplicate && <AssistantTaskCard task={task} connected={connected} onAction={onAction} />}
    </div>
    <output aria-label="操作次数">操作次数：{calls}</output>
    {opened && <p role="status">已请求打开交付审查（夹具）</p>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

// Original owner-attention and task card components; no runtime, native keys or real side effects.
import { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { TaskCard } from "../src/components/TaskCard";
import { WaitingInbox } from "../src/components/WaitingInbox";
import { taskFeedItem } from "../src/lib/activityFeed";
import type { StaffAction, StaffActionClient } from "../src/lib/staffAction";
import type { GatewayClient } from "../src/gateway";
import type { TaskItem } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/styles/shell.css";
import "./staff-actions.css";
const initial: StaffAction = { status: "pending", decision: null, reason: "", digest: "d".repeat(64), payload: {
  id: "staff-action-" + "a".repeat(32), task_id: "task-0123456789", round: 1, member: "staff-study", member_revision: 1,
  member_name: "学习专员", role: "study-helper", role_title: "学习整理", role_revision: 1, instance: "b".repeat(32), runtime: "runtime", nonce: "c".repeat(32),
  tool: "personal_goal_create", args: { objective: "读完一本设计书，整理一页笔记", acceptance_criteria: ["笔记包含三个要点"], next_action: "列出候选书单" },
  kind: "own_data", message: "新建个人目标：读完一本设计书，整理一页笔记\n下一步：列出候选书单\n达标条件：笔记包含三个要点",
  tainted: true, approval_level: "normal", expires_at: 2000000000 } };
const noop = () => {};
function Fixture() {
  const [card, setCard] = useState(structuredClone(initial));
  const [open, setOpen] = useState(false), [narrow, setNarrow] = useState(false);
  const [calls, setCalls] = useState(0);
  const client = useMemo(() => {
    let current = structuredClone(initial);
    const value: StaffActionClient = {
      async readStaffAction() { return structuredClone(current); },
      async decideStaffAction(_task, _id, approve, _digest, reason) {
        current = { ...current, status: approve ? "approved" : "denied", decision: approve, reason };
        setCalls(count => count + 1); setCard(structuredClone(current));
        return { status: current.status, replayed: false };
      },
    };
    return value as GatewayClient;
  }, []);
  const task: TaskItem = { id: initial.payload.task_id, kind: "delegation", status: card.status === "pending" ? "blocked" : "done",
    prompt: "整理学习计划，存入目标前先让我确认。", owner_session: "sid-offline-fixture",
    collaboration: { assignee: "学习专员", round: 1, review: "not_submitted", acceptance: [], tainted: true, messages: [],
      dispatch: { source: "staff", max_steps: 12, timeout_seconds: 600 }, approval: card } };
  return <main className={`qa-staff-actions${narrow ? " narrow" : ""}`}>
    <p>离线交互夹具：不会写真实目标，不调用批准钥匙。</p>
    <button onClick={() => setNarrow(!narrow)}>切换窄栏</button>
    <button onClick={() => document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"}>切换主题</button>
    <WaitingInbox items={card.status === "pending" ? [taskFeedItem(task)] : []} teams={[]} exceptions={0} connected
      onOpen={() => setOpen(true)} onOpenTeam={noop} onAcceptTeam={async () => {}} onShowActivity={noop} />
    {open && <TaskCard task={task} focused taskReviewVerifying={false} loadPlanGraph={async () => { throw new Error("unused"); }}
      onPause={noop} onCancel={noop} onResume={noop} onCopy={noop} onReviewBranch={noop} onVerify={noop} onOpenPr={noop} onCollaboration={noop}
      getClient={() => client} onDispatchUpdated={noop} />}
    <output aria-label="夹具决定次数">{calls}</output>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

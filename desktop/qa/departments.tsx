// Real components with synthetic data only: no API, model, native keys or real notes.
import { useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { DepartmentPage } from "../src/components/DepartmentPage";
import { AssistantSidebar } from "../src/components/AssistantMode";
import { WaitingInbox } from "../src/components/WaitingInbox";
import { StaffBudgets } from "../src/components/settings/StaffBudgets";
import { departmentItems, type NotesDecision } from "../src/lib/departments";
import { departmentFixture, directoryFixture } from "./department-fixture";
import type { GatewayClient } from "../src/gateway";
import type { StaffBudgetSettings } from "../src/lib/staff";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/styles/shell.css";
import "./departments.css";

const noop = () => {};
function Fixture() {
  const current = useRef(departmentFixture());
  const [revision, setRevision] = useState(0), [calls, setCalls] = useState(0), [page, setPage] = useState("waiting"), [draft, setDraft] = useState("");
  const client = useMemo(() => ({
    async readDepartment() { return structuredClone(current.current); },
    async decideDepartmentNotes(_id: string, body: NotesDecision) {
      const d = current.current;
      if (body.revision !== d.notes.current.revision) throw new Error("夹具版本已变化");
      const selected = Object.entries(body.selected ?? {});
      const accepted = selected.filter(([, choice]) => choice.approve).map(([id]) => d.notes.pending.find(item => item.id === id)!.text);
      if ((accepted.length || body.owner_text) && !body.public) throw new Error("夹具也需要明确公开保存");
      if (body.owner_text) accepted.push(body.owner_text);
      d.notes.current.text += accepted.map(text => "\n- " + text + "\n").join("");
      d.notes.current.revision += accepted.length ? 1 : 0;
      d.notes.current.tainted ||= selected.some(([, choice]) => choice.approve);
      d.notes.pending = d.notes.pending.filter(item => !body.selected?.[item.id]);
      d.notes.snapshot = { ...d.notes.current, digest: "fixture-" + d.notes.current.revision };
      setCalls(value => value + 1); setRevision(value => value + 1);
      return structuredClone(d.notes.snapshot);
    },
    async setStaffBudgets(value: StaffBudgetSettings) {
      if (value.revision !== current.current.budget_settings.revision) throw new Error("夹具额度版本已变化");
      current.current.budget_settings = { ...value, revision: value.revision + 1 };
      setCalls(count => count + 1); setRevision(count => count + 1);
      return structuredClone(current.current.budget_settings);
    },
  }) as unknown as GatewayClient, []);
  const snapshot = directoryFixture(current.current);
  const refresh = () => setRevision(value => value + 1);
  return <><nav className="qa-departments-controls"><span>离线夹具：不保存真实资料，不调用模型或批准钥匙</span>
    <button onClick={() => setPage("waiting")}>主要聊天</button><button onClick={() => setPage("department")}>部门详情</button><button onClick={() => setPage("budgets")}>额度设置</button>
    <button onClick={() => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; }}>切换主题</button></nav>
    <div className="qa-departments-layout">
      <aside className="vc-sidebar"><AssistantSidebar mainChat={{ sid: "main" }} sideChats={[]} current="main" onSelect={() => setPage("waiting")} onNewSideChat={noop}
        departments={[{ id: "dept-public", title: "资料专员", status: "active", pending: current.current.notes.pending.length }]}
        currentDepartment={page === "department" ? "dept-public" : ""} onOpenDepartment={() => setPage("department")} /></aside>
      <main>{page === "waiting" ? <><WaitingInbox items={departmentItems(snapshot)} teams={[]} exceptions={0} connected onOpen={() => setPage("department")} onOpenTeam={noop} onAcceptTeam={async () => {}} onShowActivity={noop} />
        {draft && <p role="status">只填入草稿，未派活：{draft}</p>}</> : page === "budgets" ? <StaffBudgets key={revision} value={current.current.budget_settings} usage={current.current.usage} disabled={false} onSave={value => void client.setStaffBudgets(value)} />
        : <DepartmentPage id="dept-public" client={client} getClient={() => client} connected onUpdated={refresh} onOpenTask={() => setDraft("只读打开合成任务")}
          onDraft={text => { setDraft(text); setPage("waiting"); }} />}</main>
    </div><output aria-label="夹具写入次数">{calls}</output></>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

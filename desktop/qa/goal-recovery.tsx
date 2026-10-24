import { StrictMode, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AssistantGoals } from "../src/components/AssistantGoals";
import { AssistantWorkday } from "../src/components/AssistantWorkday";
import type { GatewayClient } from "../src/gateway";
import type { PersonalGoalClient } from "../src/lib/personalGoals";
import type { PersonalGoal } from "../src/types";
import "../src/App.css";

type Host = "A" | "B";
type FixtureClient = PersonalGoalClient & Pick<GatewayClient, "getCodexProgress">;
type SnapshotCase = "normal" | "date" | "progress" | "unproven" | "verified" | "future";
type Changes = Partial<Pick<PersonalGoal, "next_action" | "blocker" | "due_at" | "follow_up_at">>;
const initial = (source: Host): PersonalGoal => ({ id: "personal-example", kind: "personal", revision: 1,
  objective: `来源${source}目标`, acceptance_criteria: [{ id: "criterion-1", text: "核对目标依据", status: "pending", evidence_ids: [] }],
  constraints: [], non_goals: [], status: "active", task_ids: [], evidence: [], next_action: `${source}原下一步`,
  blocker: "", due_at: "", follow_up_at: "", overdue: false, follow_up_due: false, progress: { passed: 0, failed: 0, total: 1 } });
const storeFor = (source: Host): Record<string, PersonalGoal> => ({
  "personal-example": initial(source),
  "personal-other": { ...initial(source), id: "personal-other", objective: `来源${source}的另一目标`, next_action: "另一目标的下一步" },
});

function Fixture() {
  const [shown, setShown] = useState(true), [source, setSource] = useState<Host>("A");
  const [mode, setMode] = useState<"lost" | "success">("lost");
  const [hold, setHold] = useState(false), [old, setOld] = useState(false);
  const [focus, setFocus] = useState<string | undefined>("personal-example"), [missing, setMissing] = useState(false);
  const [snapshotCase, setSnapshotCase] = useState<SnapshotCase>("normal"), [workday, setWorkday] = useState(false);
  const options = useRef({ mode, hold, old, snapshotCase, missing });
  const stores = useRef({ A: storeFor("A"), B: storeFor("B") });
  const sequence = useRef(0), reads = useRef(0);
  const [requests, setRequests] = useState(0), [readCount, setReadCount] = useState(0), [accepted, setAccepted] = useState(0);
  const writes = useRef(new Map<number, { source: Host; previous: PersonalGoal; changes: Changes;
    mode: "lost" | "success"; resolve: (goal: PersonalGoal) => void; reject: (error: Error) => void }>());
  const pendingReads = useRef(new Map<number, { goals: PersonalGoal[]; resolve: (goals: PersonalGoal[]) => void; reject: (error: Error) => void }>());
  const [writeIds, setWriteIds] = useState<number[]>([]), [readIds, setReadIds] = useState<number[]>([]);
  const clients = useMemo(() => {
    const unsupported = async (): Promise<PersonalGoal> => { throw new Error("本检查页仅演练保存进展"); };
    const makeClient = (label: Host): FixtureClient => ({
      listPersonalGoals: () => {
        const id = ++reads.current; setReadCount(id);
        const goals = structuredClone([options.current.old ? initial(label) : stores.current[label]["personal-example"]]);
        const item = goals[0], scenario = options.current.snapshotCase;
        if (scenario === "date") item.due_at = "broken";
        if (scenario === "progress") item.progress.total = 999;
        if (scenario === "future") item.status = "future-state";
        if (scenario === "unproven" || scenario === "verified") {
          item.status = "achieved"; item.updated = new Date().toISOString();
          item.acceptance_criteria[0] = { ...item.acceptance_criteria[0], status: "passed", evidence_ids: ["proof-qa"] };
          item.progress = { passed: 1, failed: 0, total: 1 };
          item.evidence = scenario === "unproven" ? [] : [{ id: "proof-qa", criterion_id: "criterion-1",
            kind: "personal_acceptance", source: "user_confirmation", passed: true, summary: "合成的逐项验收依据" }];
        }
        goals.unshift(structuredClone(stores.current[label]["personal-other"]));
        const visible = options.current.missing ? goals.filter(goal => goal.id !== "personal-example") : goals;
        if (!options.current.hold) return Promise.resolve(visible);
        return new Promise<PersonalGoal[]>((resolve, reject) => {
          pendingReads.current.set(id, { goals: visible, resolve, reject }); setReadIds([...pendingReads.current.keys()]);
        });
      },
      updatePersonalGoal: (previous: PersonalGoal, changes: Changes) => new Promise<PersonalGoal>((resolve, reject) => {
        const id = ++sequence.current; setRequests(id);
        writes.current.set(id, { source: label, previous, changes, mode: options.current.mode, resolve, reject });
        setWriteIds([...writes.current.keys()]);
      }),
      getCodexProgress: async () => ({ overview: "合成进度", received_at: new Date().toISOString(), threads: [] }),
      createPersonalGoal: unsupported, confirmPersonalGoal: unsupported,
      configurePersonalGoalFollowUp: unsupported, acknowledgePersonalGoalFollowUp: unsupported,
    });
    return { A: makeClient("A"), B: makeClient("B") };
  }, []);
  function settleWrite(id: number) {
    const item = writes.current.get(id);
    if (!item) return;
    writes.current.delete(id); setWriteIds([...writes.current.keys()]);
    const current = stores.current[item.source][item.previous.id];
    if (!current || item.previous.revision !== current.revision) { item.reject(new Error("目标已变化")); return; }
    const next = { ...current, ...item.changes, revision: item.previous.revision + 1 };
    stores.current[item.source][item.previous.id] = next; setAccepted(value => value + 1);
    if (item.mode === "lost") item.reject(new Error("合成回执中断：保存结果需读取核对"));
    else item.resolve(structuredClone(next));
  }
  function settleRead(id: number, fail: boolean) {
    const item = pendingReads.current.get(id);
    if (!item) return;
    pendingReads.current.delete(id); setReadIds([...pendingReads.current.keys()]);
    if (fail) item.reject(new Error("合成读取中断")); else item.resolve(item.goals);
  }
  return <main style={{ maxWidth: 960, height: "100vh", overflowY: "auto", margin: "0 auto", padding: 16, boxSizing: "border-box" }}>
    <h3>目标提交恢复检查</h3><p>合成数据，仅演练保存进展。回执与读取由下方控件控制，不连接运行端。</p>
    <div>
      <button onClick={() => setShown(value => !value)}>{shown ? "关闭目标页" : "打开目标页"}</button>
      <button onClick={() => setSource(value => value === "A" ? "B" : "A")}>切换来源</button>
      <button onClick={() => setFocus("personal-other")}>查看另一目标</button>
      <button onClick={() => setFocus("personal-example")}>查看原目标</button>
      <label>写入回执<select value={mode} onChange={event => {
        const value = event.target.value as typeof mode; options.current.mode = value; setMode(value);
      }}><option value="lost">提交后丢失回执</option><option value="success">正常回执</option></select></label>
      <label><input type="checkbox" checked={hold} onChange={event => { options.current.hold = event.target.checked; setHold(event.target.checked); }} />延迟读取</label>
      <label><input type="checkbox" checked={old} onChange={event => { options.current.old = event.target.checked; setOld(event.target.checked); }} />旧版本读取</label>
      <label>目标数据<select value={snapshotCase} onChange={event => {
        const value = event.target.value as SnapshotCase; options.current.snapshotCase = value; setSnapshotCase(value);
      }}><option value="normal">正常记录</option><option value="date">日期异常</option><option value="progress">进度异常</option>
        <option value="unproven">达成但缺少依据</option><option value="verified">合成完整验收</option><option value="future">未知状态</option></select></label>
      <label><input type="checkbox" checked={workday} onChange={event => setWorkday(event.target.checked)} />显示今日工作</label>
      <label><input type="checkbox" checked={missing} onChange={event => { options.current.missing = event.target.checked; setMissing(event.target.checked); }} />隐藏原目标</label>
    </div>
    <p><output id="qa-source">来源：{source}</output> · <output id="qa-requests">写入请求：{requests}</output> ·
      <output id="qa-reads">读取次数：{readCount}</output> · <output id="qa-accepted">已保存：{accepted}</output></p>
    <div>{writeIds.map(id => <button key={id} onClick={() => settleWrite(id)}>完成写入 {id}</button>)}
      {readIds.map(id => <span key={id}><button onClick={() => settleRead(id, false)}>返回读取 {id}</button>
        <button onClick={() => settleRead(id, true)}>读取失败 {id}</button></span>)}</div>
    {workday && <AssistantWorkday client={clients[source]} scope={source} connected dispatches={[]} dispatchesLoaded codexActions={[]}
      onDispatch={() => {}} onGoals={id => { setFocus(id); setShown(true); }} onRefreshTasks={async () => {}} onCodexDecision={async () => {}} />}
    {shown && <AssistantGoals client={clients[source]} connected focusId={focus} onClearFocus={() => setFocus(undefined)}
      onFocusGoal={(id, owner) => { if (owner === clients[source]) setFocus(id); }} />}
  </main>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Fixture /></StrictMode>);

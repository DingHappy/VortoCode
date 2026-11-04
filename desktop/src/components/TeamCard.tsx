import { useCallback, useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../gateway";
import type { TeamChild, TeamDetail, TeamStatus } from "../types";

export type TeamClient = Pick<GatewayClient, "getTeam" | "decideTeam" | "replyTeam" | "stopTeam">;

const STATUS: Record<TeamStatus, string> = {
  active: "进行中", waiting: "等你决定", review: "交付了，等你验收", done: "你已验收", failed: "失败了", cancelled: "已停止",
};
const ROLE: Record<string, string> = { "web-researcher": "研究员", reviewer: "审查员", developer: "开发" };
const ENGINE: Record<string, string> = { vortocode: "VortoCode", opencode: "OpenCode", "deepseek-harness": "DeepSeek Harness" };
const CHILD: Record<string, string> = {
  queued: "排队中", running: "在做", done: "交回了", failed: "失败", cancelled: "已取消", interrupted: "被打断",
};
const LIVE: TeamStatus[] = ["active", "waiting", "review"];

/** 任务小组详情（ADR-014 S3）。内容全部来自服务端代码拼的视图；成员结果和交付证据可能含外部内容，只当纯文本显示。
 * 按钮是固定的应用动作（验收、退回、回话、停止、拉分支），不执行任何模型文本里的指令。 */
export function TeamCard({ id, client, connected, onPull, onChanged, initial = null }: {
  id: string; client: TeamClient; connected: boolean;
  /** 已有的详情（测试与预渲染用）；照样会去服务端核对一次 */
  initial?: TeamDetail | null;
  onPull?: (project: NonNullable<TeamDetail["project"]>, branch: string) => Promise<string>;
  onChanged?: () => void;
}) {
  const [team, setTeam] = useState<TeamDetail | null>(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState("");
  const pending = useRef(false);
  const load = useCallback(async () => {
    try { setTeam(await client.getTeam(id)); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "读取小组失败"); }
  }, [client, id]);
  useEffect(() => { void load(); }, [load]);
  const act = async (run: () => Promise<TeamDetail | string>) => {
    if (pending.current || !connected) return;
    pending.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const value = await run();
      if (typeof value === "string") setNotice(value); else { setTeam(value); setNote(""); onChanged?.(); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败，请核对状态"); }
    finally { pending.current = false; setBusy(false); }
  };
  if (!team) return <article className="assistant-task-card team-card" aria-label="任务小组">
    {error ? <p role="alert" className="task-error">{error}</p> : <p role="status">正在读取小组…</p>}
  </article>;
  const live = LIVE.includes(team.status);
  const branches = team.children.filter((child): child is TeamChild & { delivery: { branch: string } } => Boolean(child.delivery?.branch));
  return <article className="assistant-task-card team-card" aria-label={`任务小组：${team.objective}`}>
    <header><strong>{team.objective}</strong><span>{team.project ? `任务小组 · ${team.project.name}` : "任务小组"}</span></header>
    <p className="assistant-task-status">{STATUS[team.status] ?? "状态未知"}</p>
    {team.waiting_reason && team.status === "waiting" && <p className="assistant-task-text">{team.waiting_reason}</p>}
    {team.error && <p className="task-error">{team.error}</p>}
    <p>成员：{team.members.map(item => `${item.name}（${ROLE[item.role] ?? item.role}${item.engine !== "vortocode" ? `，${ENGINE[item.engine] ?? item.engine}` : ""}）`).join("、")}</p>
    <p>额度：子任务 {team.used.tasks ?? 0}/{team.limits.tasks}，轮次 {team.used.rounds ?? 0}/{team.limits.rounds}，
      累计时限 {Math.round((team.used.timeout_seconds ?? 0) / 60)}/{Math.round((team.limits.timeout_seconds ?? 0) / 60)} 分钟，
      协调人醒来 {team.used.wakes ?? 0}/{team.limits.wakes} 次</p>
    <details>
      <summary>验收标准</summary>
      <ol>{team.acceptance.map((line, index) => <li key={index}>{line}</li>)}</ol>
    </details>
    {team.submission && <section aria-label="交付与证据">
      <p><strong>小组的交付</strong>{team.submission.tainted ? "（含外部内容，请自行核实）" : ""}</p>
      <p className="assistant-task-text">{team.submission.summary}</p>
      <ul>{team.submission.evidence.map((row, index) => <li key={index}>
        {team.acceptance[row.criterion] ?? `标准 ${row.criterion + 1}`}：<span className="assistant-task-text">{row.evidence}</span></li>)}</ul>
    </section>}
    {branches.length > 0 && <section aria-label="交付分支">
      <p><strong>交付分支</strong>（常驻电脑上的本地分支，没有推送、没有合并）</p>
      <ul>{branches.map(child => <li key={child.id}>
        <code>{child.delivery.branch}</code>
        {" "}验证：{child.delivery.verified === true ? "通过" : child.delivery.verified === false ? "没通过" : "没跑"}
        {child.delivery.preexisting_failures ? `（另有 ${child.delivery.preexisting_failures} 个测试改动前就失败）` : ""}
        {team.project && onPull && <div className="task-actions"><button disabled={busy || !connected}
          onClick={() => void act(() => onPull(team.project!, child.delivery.branch))}>拉回本机</button></div>}
      </li>)}</ul>
      {team.project && !onPull && <small>这台 Mac 上还没有「{team.project.name}」的本机项目，拉不回来；
        可在终端用 git fetch 从常驻电脑拉取这个分支。</small>}
    </section>}
    {team.children.length > 0 && <details open={team.status !== "done"}>
      <summary>成员的每一轮（{team.children.length}）</summary>
      <ol className="team-rounds">{team.children.map(child => <li key={child.id}>
        <p>{child.member} · 第 {child.round ?? 1} 轮 · {CHILD[child.status] ?? child.status}
          {child.review === "accepted" ? " · 协调人已接受" : ""}{child.tainted ? " · 含外部内容" : ""}</p>
        {child.request && <details><summary>派给它的要求</summary><p className="assistant-task-text">{child.request}</p></details>}
        {child.result && <details><summary>交回的结果</summary><p className="assistant-task-text">{child.result}</p></details>}
        {child.error && <p className="task-error">{child.error}</p>}
      </li>)}</ol>
    </details>}
    {team.events.length > 0 && <details>
      <summary>协调记录（{team.events.length}）</summary>
      <ol>{team.events.map(event => <li key={event.seq}><span className="assistant-task-text">{event.kind}：{event.text}</span></li>)}</ol>
    </details>}
    {team.tainted && <p className="assistant-task-warning">这个小组读过外部内容；结果和交付请自行核实。</p>}
    {(team.status === "waiting" || team.status === "review") && <>
      <textarea aria-label={team.status === "review" ? "退回原因" : "你的回复"} value={note} maxLength={2000} rows={2}
        disabled={busy || !connected} placeholder={team.status === "review" ? "退回时写明要改什么" : "回复小组的问题"}
        onChange={event => setNote(event.target.value)} />
      <div className="task-actions">
        {team.status === "review" && <button disabled={busy || !connected} onClick={() => void act(() => client.decideTeam(team.id, true, note.trim()))}>验收通过</button>}
        {team.status === "review" && <button disabled={busy || !connected || !note.trim()} onClick={() => void act(() => client.decideTeam(team.id, false, note.trim()))}>退回</button>}
        {team.status === "waiting" && <button disabled={busy || !connected || !note.trim()} onClick={() => void act(() => client.replyTeam(team.id, note.trim()))}>回复小组</button>}
      </div>
      {team.status === "waiting" && <small>要加预算，在对话里跟助理说，它会弹确认卡。</small>}
    </>}
    <div className="task-actions">
      <button disabled={busy || !connected} onClick={() => void act(async () => { await load(); return "已核对最新状态"; })}>核对状态</button>
      {live && <button disabled={busy || !connected} onClick={() => void act(() => client.stopTeam(team.id))}>停止小组</button>}
    </div>
    {!connected && <small>连接中断，恢复后可继续处理。</small>}
    {busy && <small role="status">正在处理…</small>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="task-error">{error}</p>}
  </article>;
}

import { useEffect, useState } from "react";
import type { TeamDetail } from "../types";
import type { TeamClient } from "./TeamCard";
import { ResultDocument } from "./ResultDocument";
import { firstSentence } from "../lib/activityFeed";
import "./TeamDelivery.css";

/** A read-only view of the saved submission and explicitly identified member records. */
export function TeamDelivery({ id, client, connected, initial = null }: {
  id: string; client: Pick<TeamClient, "getTeam">; connected: boolean; initial?: TeamDetail | null;
}) {
  const [snapshot, setSnapshot] = useState({ client, id, data: initial?.id === id ? initial : null });
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true, request = 0;
    setError("");
    if (!connected) return;
    const load = async () => {
      const attempt = ++request;
      try {
        const next = await client.getTeam(id);
        if (!alive || attempt !== request) return;
        if (next.id !== id) throw new Error("交付记录与当前小组不符，请重新读取。");
        setSnapshot({ client, id, data: next }); setError("");
      } catch (reason) {
        if (alive && attempt === request) setError(reason instanceof Error ? reason.message : "读取交付失败");
      }
    };
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 8000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [client, id, connected, refresh]);
  const team = snapshot.client === client && snapshot.id === id ? snapshot.data : null;
  const results = team?.children.filter(child => child.result) ?? [];
  return <section className="team-delivery" aria-label="小组交付">
    <header><h3>交付与原文</h3><button type="button" disabled={!connected} onClick={() => setRefresh(value => value + 1)}>刷新交付</button></header>
    {!connected && <p role="status">连接中断，下面保留上次读到的交付。</p>}
    {error && <p role="alert" className="task-error">{error} 已有内容保留，最新状态待核对。</p>}
    {!team ? <p role="status">{connected ? "正在读取交付…" : "尚无已读取的交付。"}</p> : <>
      {team.submission ? <>
        <p className="team-delivery-lead">{firstSentence(team.submission.summary.replace(/^#{1,6}\s.*$/gm, ""), 180) || "协调人已提交总结，请展开查看。"}</p>
        <ResultDocument title="查看协调人交付总结" text={team.submission.summary} tainted={team.submission.tainted} />
        <details className="team-delivery-evidence"><summary>验收标准与证据（{team.submission.evidence.length}）</summary>
          <ul>{team.submission.evidence.map((row, index) => <li key={index}><strong>{row.text || team.acceptance[row.criterion] || `标准 ${row.criterion + 1}`}</strong><p>{row.evidence}</p></li>)}</ul>
        </details>
      </> : <p>小组还没有提交交付总结，可先查看成员交回的原文。</p>}
      <h4>成员交回的原文 · {results.length}</h4>
      <p className="team-delivery-note">按任务和轮次保留，可能含历史版本；以交付总结引用的任务为准。显示服务端已保存的文本，长结果可能被截断，正文缺失时需补交。协调人接受成员结果不等于你已验收小组。</p>
      {[...results].reverse().map(child => <ResultDocument key={`${child.id}:${child.round}`} title={`${child.member} · ${child.id} · 第 ${child.round ?? 1} 轮 · ${child.review === "accepted" ? "协调人已接受" : "尚未由协调人接受"}`}
        text={child.result!} tainted={child.tainted} />)}
      {results.length === 0 && <p>尚未读到成员原文；交付总结不代表已经取得完整正文。</p>}
    </>}
  </section>;
}

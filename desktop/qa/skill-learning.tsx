/** Production components with synthetic, in-memory transport. No task/model/account traffic. */
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PersonalSkillsCard } from "../src/components/PersonalSkillsCard";
import type { GatewayClient } from "../src/gateway";
import type { PersonalSkill, PersonalSkillDraft, SkillLearningSource, SkillProposal } from "../src/types";
import "../src/styles/tokens.css";
import "../src/styles/components.css";
import "../src/App.css";
import "../src/daily.css";
import "../src/styles/shell.css";

const at = "2026-11-01T12:00:00Z";
const source: SkillLearningSource = { dispatch_id: "dsp-fixture-reviewed", revision: "source-1", title: "重连后旧响应不得回写", project: "验收项目",
  goal: "重连后丢弃旧响应，不重复发送操作。", criteria: ["旧响应不改变新连接状态", "重复点击仅保存一次"], head: "a".repeat(40), checked_at: at, verified_at: at };
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
function transport(empty = false) {
  let skill: PersonalSkill | null = empty ? null : { id: "reconnect", name: "重连流程", description: "已有版本", instructions: "先核对当前连接。",
    revision: "old-1", enabled: true, source_scope: "personal", status: "enabled", capabilities: [], model_invocable: true, detail: "仅提供指令，不增加权限。" };
  let proposal: SkillProposal | null = null, stale = false, fail = false, count = 0;
  const client = {
    connectionEpoch: 1,
    async getPersonalSkills() { count++; return { items: skill ? [copy(skill)] : [], issues: [], truncated: false }; },
    async getSkillLearning() { count++; return { sources: empty ? [] : [copy(source)], proposals: proposal ? [copy(proposal)] : [], issues: [], truncated: false }; },
    async previewPersonalSkill() { count++; if (!skill) throw Error("技能不存在"); return copy(skill); },
    async previewSkillProposal() { count++; if (!proposal) throw Error("提案不存在"); return { ...copy(proposal), source_stale: stale }; },
    async proposePersonalSkill(id: string, _source: SkillLearningSource, draft: PersonalSkillDraft) {
      count++;
      if (proposal?.status === "pending") return copy(proposal);
      proposal = { id: "proposal-1", skill_id: id, status: "pending", created_at: at, source: copy(source), revision: "proposal-1",
        draft: copy(draft), proposed_by: "instance_operator", reviewed_by: null, reviewed_at: null, target_revision: "new-1", current_revision: skill?.revision || null,
        current_enabled: skill?.enabled || false, stale: false, source_stale: false, diff_redacted: false, history: [],
        diff: `--- 当前版本\n+++ 待审草稿\n-name: ${skill?.name || "（无）"}\n+name: ${draft.name}\n-${skill?.instructions || ""}\n+${draft.instructions.split("\n").join("\n+")}` };
      return copy(proposal);
    },
    async decideSkillProposal(_proposal: SkillProposal, action: "accept" | "reject" | "reconcile") {
      count++; if (!proposal) throw Error("提案不存在");
      if (action === "accept" && stale) throw Error("来源交付证据已变化，未保存技能");
      if (action === "reject") proposal.status = "rejected";
      else {
        skill = { id: proposal.skill_id, ...copy(proposal.draft), revision: "new-1", enabled: false, status: "needs_review", source_scope: "personal", capabilities: [], model_invocable: true, detail: "仅提供指令，不增加权限。" };
        proposal.current_revision = "new-1"; proposal.current_enabled = false; proposal.status = fail && action === "accept" ? "applying" : "accepted";
      }
      proposal.reviewed_by = "instance_operator"; proposal.reviewed_at = at; proposal.revision += "-decision";
      if (fail && action === "accept") { fail = false; throw Error("合成磁盘写入中断，保存结果待核对"); }
      return copy(proposal);
    },
    async setPersonalSkillEnabled(_id: string, enabled: boolean) { count++; if (!skill) throw Error("技能不存在"); skill.enabled = enabled; skill.status = enabled ? "enabled" : "needs_review"; return copy(skill); },
    async savePersonalSkill(id: string, draft: PersonalSkillDraft) { count++; skill = { id, ...draft, revision: "manual-1", enabled: false, status: "needs_review", source_scope: "personal", capabilities: [], model_invocable: true, detail: "仅提供指令。" }; return copy(skill); },
  };
  return { client: client as unknown as GatewayClient, stale: () => { stale = true; }, fail: () => { fail = true; }, count: () => count };
}
function Fixture() {
  const [hosts] = useState(() => [transport(), transport(true)]);
  const [host, setHost] = useState(0);
  const [message, setMessage] = useState("");
  return <main className="vc-customize-skills" style={{ maxWidth: 760, padding: 16, margin: "20px auto" }}>
    <h1>任务技能提案验收</h1><p>合成数据，仅浏览器内存；不调用真实任务、模型、凭证或设备。</p>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0" }}>
      <button onClick={() => { hosts[host].stale(); setMessage("已模拟来源失效"); }}>模拟来源失效</button>
      <button onClick={() => { hosts[host].fail(); setMessage("下次保存将模拟中断"); }}>模拟保存中断</button>
      <button onClick={() => { setHost(value => 1 - value); setMessage("已切换运行时"); }}>切换运行时</button>
      <button onClick={() => setMessage(`读取与手动操作请求数：${hosts[host].count()}`)}>查看请求数</button>
    </div><p role="status">{message}</p>
    <PersonalSkillsCard client={hosts[host].client} connection="connected" />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);

import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import type { DesktopLlmProfileStatus, ExternalWorkerStatus, ResidentWorkersView } from "../../types";
import { externalWorkerOptions, roleModelOptions } from "../../lib/modelSettings";

export const MODEL_ROLES = [
  ["research", "研究子 Agent", "资料整理、只读分析"],
  ["implement", "实现子 Agent", "隔离工作区中的编程与自测"],
  ["review", "审查子 Agent", "代码审查与问题核验"],
  ["assess", "模型评判器", "按需评估继续、升级或补证据；留空关闭额外评判调用"],
] as const;

/** 实现与审查可以交给本机已登录的 Codex / Claude Code CLI（`cli:codex`、`cli:claude`）。 */
const CLI_ROLES = new Set(["implement", "review"]);

function cliStatus(value: string, workers: ExternalWorkerStatus[] | null) {
  if (!value.startsWith("cli:")) return null;
  const worker = workers?.find(item => item.worker === value.slice(4));
  if (!workers) return <small>正在检查本机是否安装…</small>;
  if (!worker) return <small className="danger">不支持的外部 CLI</small>;
  const login = worker.worker === "codex" ? "codex login" : "claude auth login";
  if (!worker.path) return <small className="danger">本机没找到 {worker.worker} 命令：安装并在终端运行 {login} 后，保存并重启引擎</small>;
  if (worker.loggedIn === false) return <small className="danger">已找到 {worker.path}，但未登录：在终端运行 {login}</small>;
  if (worker.loggedIn) return <small>已就绪：{worker.path}{worker.auth ? ` · ${worker.auth}` : ""}</small>;
  return <small>已找到 {worker.path}；登录状态未能确认{worker.auth ? `（${worker.auth}）` : ""}</small>;
}

const ROLE_LABEL: Record<string, string> = { implement: "实现", review: "审查" };

/** 常驻电脑上的外部执行者：只读。配置在常驻电脑的服务环境里改，本机设置不碰它。 */
export function ResidentWorkers({ view }: { view: ResidentWorkersView }) {
  const { summary } = view;
  let body;
  if (view.error) body = <small className="danger">{view.error}</small>;
  else if (!summary) body = <small>该实例版本还不会报告外部执行者状态。</small>;
  else if (!Object.keys(summary.roles).length) body = <small>未配置外部执行者：实现与审查用内置子 Agent。</small>;
  else body = <>
    {summary.mode !== "resident" && <small className="danger">该实例没有设 VORTOCODE_WORKER_HOME，会用服务账号自己的个人 CLI 配置；建议改成常驻模式的专用登录。</small>}
    {Object.entries(summary.roles).sort().map(([role, worker]) => {
      const item = summary.workers.find(entry => entry.worker === worker);
      const state = !item?.found ? <span className="danger">找不到 {worker} 命令</span>
        : item.logged_in === false ? <span className="danger">未登录：在常驻电脑上运行 vc workers login {worker}</span>
        : item.logged_in ? `已就绪${item.auth ? ` · ${item.auth}` : ""}${item.model ? ` · 模型 ${item.model}` : ""}`
        : "登录状态未能确认";
      return <small key={role}>{ROLE_LABEL[role] ?? role}：{worker} — {state}</small>;
    })}
  </>;
  return <div className="llm-resident-workers" aria-label="常驻电脑的外部执行者">
    <p>常驻电脑「{view.name}」的外部执行者 <em>只读 · {summary?.mode === "resident" ? `常驻模式，专用登录在 ${summary.home}` : "在常驻电脑的服务环境里配置"}</em></p>
    {body}
  </div>;
}

export function ModelRoles({ profile, value, onChange, disabled = false, workers: given, resident = null }: {
  profile: DesktopLlmProfileStatus | null; value: Record<string, string>;
  onChange: (value: Record<string, string>) => void; disabled?: boolean;
  workers?: ExternalWorkerStatus[] | null;
  resident?: ResidentWorkersView | null;
}) {
  const options = roleModelOptions(profile);
  const [found, setFound] = useState<ExternalWorkerStatus[] | null>(given ?? null);
  useEffect(() => {
    if (given !== undefined) return;
    let alive = true;
    invoke<ExternalWorkerStatus[]>("detect_external_workers")
      .then(result => { if (alive) setFound(result); })
      .catch(() => { if (alive) setFound([]); });
    return () => { alive = false; };
  }, [given]);
  const workers = given !== undefined ? given : found;
  return <section className="llm-routing-fields" aria-label="主与子 Agent 模型分工">
    <p>主与子 Agent 模型分工 <em>主 Agent 跟随聊天框的模型选择，上方是默认主模型。研究、实现和审查留空使用默认服务模型；评判器留空关闭额外调用。</em></p>
    {MODEL_ROLES.map(([role, label, hint]) => <label key={role}><span>{label}</span>
      <input aria-label={label} list={CLI_ROLES.has(role) ? "llm-role-cli-options" : "llm-role-model-options"} value={value[role] || ""} disabled={disabled} maxLength={160}
        placeholder={role === "assess" ? "关闭，或 供应商ID:模型ID" : CLI_ROLES.has(role) ? "默认模型、供应商ID:模型ID，或 cli:codex / cli:claude" : "默认模型，或 供应商ID:模型ID"}
        onChange={event => onChange({ ...value, [role]: event.target.value })} />
      {cliStatus(value[role] || "", workers) ?? <small>{hint}</small>}
    </label>)}
    <datalist id="llm-role-model-options">{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist>
    <datalist id="llm-role-cli-options">{[...externalWorkerOptions(workers), ...options].map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist>
    <p className="llm-profile-note">跨渠道模型先在「其他模型供应商」登记。每家使用自己的 Key。此处随上方保存生效，仅配置本机引擎；常驻电脑须在对应实例配置。角色文件中的明确 model 优先。</p>
    <p className="llm-profile-note">本机引擎：实现、审查选 cli:codex 或 cli:claude 时，交给这台电脑上你本人登录的 CLI 在隔离工作区里执行（审查只读），只沿用你个人配置里的模型选择；拆解、自测、验证、确认和开 PR 仍由 VortoCode 负责，CLI 拿不到 GitHub 令牌。</p>
    {resident && <ResidentWorkers view={resident} />}
  </section>;
}

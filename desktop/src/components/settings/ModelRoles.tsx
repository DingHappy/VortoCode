import type { DesktopLlmProfileStatus, EngineWorkersView, ExternalWorkerStatus, ExternalWorkersSummary, ResidentWorkersView } from "../../types";
import { externalWorkerOptions, roleModelOptions } from "../../lib/modelSettings";

export const MODEL_ROLES = [
  ["research", "研究子 Agent", "资料整理、只读分析"],
  ["implement", "实现子 Agent", "隔离工作区中的编程与自测"],
  ["review", "审查子 Agent", "代码审查与问题核验"],
  ["assess", "模型评判器", "按需评估继续、升级或补证据；留空关闭额外评判调用"],
] as const;

/** 实现与审查可以交给本机已登录的 Codex / Claude Code CLI（`cli:codex`、`cli:claude`）。 */
const CLI_ROLES = new Set(["implement", "review"]);

const CLI_LABEL: Record<string, string> = { codex: "Codex CLI（本机）", claude: "Claude Code CLI（本机）" };

/** 引擎报来的状态（内核唯一实现）→ 设置页用的形状。 */
export function workerStatuses(summary: ExternalWorkersSummary | null | undefined): ExternalWorkerStatus[] | null {
  if (!summary) return null;
  return summary.workers.map(item => ({ worker: item.worker, label: CLI_LABEL[item.worker] ?? item.worker,
    path: item.found ? item.path || "已安装" : null, loggedIn: item.found ? item.logged_in : null, auth: item.auth || null }));
}

function cliStatus(value: string, workers: ExternalWorkerStatus[] | null, unknown: string) {
  if (!value.startsWith("cli:")) return null;
  const worker = workers?.find(item => item.worker === value.slice(4));
  if (!workers) return <small>{unknown}</small>;
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
  else if (summary.experimental === false) body = <small>该实例没有开启实验功能（VORTOCODE_EXPERIMENTAL_EXTERNAL_WORKERS），实现与审查用内置子 Agent。</small>;
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

/** 实验功能开关：把实现 / 审查分发给本机 Codex / Claude Code。默认关闭，打开要过原生确认。 */
export function ExperimentalWorkersToggle({ enabled, busy = false, note = "", onToggle }: {
  enabled: boolean; busy?: boolean; note?: string; onToggle?: (next: boolean) => void;
}) {
  return <div className="llm-experimental-toggle">
    <label>
      <input type="checkbox" checked={enabled} disabled={busy || !onToggle} onChange={event => onToggle?.(event.target.checked)} />
      <span>实验功能：把实现 / 审查分发给本机 Codex / Claude Code <em>默认关闭；主线仍由 VortoCode 自己的子 Agent 完成</em></span>
    </label>
    {note && <small>{note}</small>}
  </div>;
}

export function ModelRoles({ profile, value, onChange, disabled = false, engine = null, experimental = false,
  experimentalBusy = false, experimentalNote = "", onExperimentalToggle }: {
  profile: DesktopLlmProfileStatus | null; value: Record<string, string>;
  onChange: (value: Record<string, string>) => void; disabled?: boolean;
  /** 当前连着的引擎报来的执行者状态；本机引擎时用于下方输入框的检查，常驻电脑时单独只读展示。 */
  engine?: EngineWorkersView | null;
  /** 本机的实验开关（Desktop 偏好）；关着时不提供 cli: 选项，已选的也不生效。 */
  experimental?: boolean; experimentalBusy?: boolean; experimentalNote?: string;
  onExperimentalToggle?: (next: boolean) => void;
}) {
  const options = roleModelOptions(profile);
  const local = engine?.place === "local" ? engine : null;
  const resident = engine?.place === "resident" ? engine : null;
  const workers = local ? workerStatuses(local.summary) : null;
  const unknown = resident ? "当前连着常驻电脑；切回本机项目后显示本机 CLI 的状态"
    : local?.error ?? (local && !local.summary ? "本机引擎版本还不会报告 CLI 状态" : "正在检查本机是否安装…");
  return <section className="llm-routing-fields" aria-label="主与子 Agent 模型分工">
    <p>主与子 Agent 模型分工 <em>主 Agent 跟随聊天框的模型选择，上方是默认主模型。研究、实现和审查留空使用默认服务模型；评判器留空关闭额外调用。</em></p>
    {MODEL_ROLES.map(([role, label, hint]) => {
      const cliRole = experimental && CLI_ROLES.has(role);
      const inactive = !experimental && (value[role] || "").startsWith("cli:");
      return <label key={role}><span>{label}</span>
        <input aria-label={label} list={cliRole ? "llm-role-cli-options" : "llm-role-model-options"} value={value[role] || ""} disabled={disabled} maxLength={160}
          placeholder={role === "assess" ? "关闭，或 供应商ID:模型ID" : cliRole ? "默认模型、供应商ID:模型ID，或 cli:codex / cli:claude" : "默认模型，或 供应商ID:模型ID"}
          onChange={event => onChange({ ...value, [role]: event.target.value })} />
        {inactive ? <small className="danger">实验功能未开启：这一项不生效，{label}仍用内置子 Agent</small>
          : (experimental ? cliStatus(value[role] || "", workers, unknown) : null) ?? <small>{hint}</small>}
      </label>;
    })}
    <datalist id="llm-role-model-options">{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist>
    {experimental && <datalist id="llm-role-cli-options">{[...externalWorkerOptions(workers), ...options].map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist>}
    <p className="llm-profile-note">跨渠道模型先通过「添加模型服务」登记。每家使用自己的 Key。此处随底部「保存模型配置」生效，仅配置本机引擎；常驻电脑须在对应实例配置。角色文件中的明确 model 优先。</p>
    <ExperimentalWorkersToggle enabled={experimental} busy={experimentalBusy} note={experimentalNote} onToggle={onExperimentalToggle} />
    {experimental && <p className="llm-profile-note">本机引擎：实现、审查选 cli:codex 或 cli:claude 时，交给这台电脑上你本人登录的 CLI 在隔离工作区里执行（审查只读），只沿用你个人配置里的模型选择；拆解、自测、验证、确认和开 PR 仍由 VortoCode 负责，CLI 拿不到 GitHub 令牌。执行者不可用时自动改由内置子 Agent 接手。</p>}
    {resident && <ResidentWorkers view={resident} />}
  </section>;
}

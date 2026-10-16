import type { DesktopLlmProfileStatus } from "../../types";
import { roleModelOptions } from "../../lib/modelSettings";

export const MODEL_ROLES = [
  ["research", "研究子 Agent", "资料整理、只读分析"],
  ["implement", "实现子 Agent", "隔离工作区中的编程与自测"],
  ["review", "审查子 Agent", "代码审查与问题核验"],
  ["assess", "模型评判器", "按需评估继续、升级或补证据；留空关闭额外评判调用"],
] as const;

export function ModelRoles({ profile, value, onChange, disabled = false }: {
  profile: DesktopLlmProfileStatus | null; value: Record<string, string>;
  onChange: (value: Record<string, string>) => void; disabled?: boolean;
}) {
  const options = roleModelOptions(profile);
  return <section className="llm-routing-fields" aria-label="主与子 Agent 模型分工">
    <p>主与子 Agent 模型分工 <em>主 Agent 跟随聊天框的模型选择，上方是默认主模型。研究、实现和审查留空使用默认服务模型；评判器留空关闭额外调用。</em></p>
    {MODEL_ROLES.map(([role, label, hint]) => <label key={role}><span>{label}</span>
      <input aria-label={label} list="llm-role-model-options" value={value[role] || ""} disabled={disabled} maxLength={160}
        placeholder={role === "assess" ? "关闭，或 供应商ID:模型ID" : "默认模型，或 供应商ID:模型ID"} onChange={event => onChange({ ...value, [role]: event.target.value })} />
      <small>{hint}</small>
    </label>)}
    <datalist id="llm-role-model-options">{options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist>
    <p className="llm-profile-note">跨渠道模型先在「其他模型供应商」登记。每家使用自己的 Key。此处随上方保存生效，仅配置本机引擎；常驻电脑须在对应实例配置。角色文件中的明确 model 优先。</p>
  </section>;
}

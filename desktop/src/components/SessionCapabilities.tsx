import { useId } from "react";
import { X } from "lucide-react";
import { useDialogSurface } from "../hooks/useDialogSurface";
import type { ResourceView, ToolInventory } from "../lib/sessionResources";
import { SessionResourceDetails } from "./SessionResources";
import "./SessionCapabilities.css";

const groups = [
  { title: "查公开资料", test: (name: string) => ["web_search", "web_fetch"].includes(name) },
  { title: "文件与项目", test: (name: string) => ["read_file", "write_file", "edit_file", "list_files", "search_files", "grep"].includes(name) },
  { title: "命令与验证", test: (name: string) => ["shell", "run_tests", "run_command"].includes(name) },
  { title: "任务与协作", test: (name: string) => /^(team_|task_|dev_|delegate)/.test(name) },
  { title: "记忆与目标", test: (name: string) => /^(memory_|goal_|personal_goal_|recall|remember)/.test(name) || name === "save_memory" },
  { title: "其他工具与集成", test: () => true },
];

const capabilityLabels: Record<string, string> = {
  host_process: "本机进程", authenticated_outbound: "带身份的对外访问",
  sensitive_files: "敏感文件", external_content: "外部内容",
};

function ToolGroups({ inventory }: { inventory: ToolInventory }) {
  return <div className="session-capability-groups">{groups.map((group, index) => {
    const tools = inventory.items.filter(tool => groups.findIndex(candidate => candidate.test(tool.name)) === index);
    return tools.length > 0 && <details key={group.title}><summary>{group.title} · {tools.length}</summary><ul>
      {tools.map(tool => <li key={tool.name}><code>{tool.name}</code><span>{tool.read_only === true ? "声明只读" : tool.read_only === false ? "可能改动状态" : "读写属性未确认"}</span>
        <small>{tool.required_capabilities === null ? "所需能力未确认" : tool.required_capabilities.length ? `调用时需核对：${tool.required_capabilities.map(name => capabilityLabels[name] ?? name).join("、")}` : "调用仍按原工具策略检查"}</small></li>)}
    </ul></details>;
  })}</div>;
}

export function SessionCapabilities({ view, connected, hostLabel, onClose, onRefresh, onManage, onLocalSettings, pendingId, onFocusConfirmation }: {
  view: ResourceView; connected: boolean; hostLabel: string; onClose: () => void; onRefresh: () => void;
  onManage: () => void; onLocalSettings: () => void; pendingId?: string; onFocusConfirmation: () => void;
}) {
  const dialog = useDialogSurface(), titleId = useId();
  const inventory = view.snapshot?.tool_inventory;
  const stale = view.stale || !connected;
  const content = <dialog ref={dialog} className="session-capabilities-dialog" aria-labelledby={titleId}
    onKeyDown={event => { if (event.key === "Escape") event.stopPropagation(); }}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header><div><small>当前对话 · {hostLabel}</small><h2 id={titleId}>此对话的能力与连接</h2></div><button type="button" aria-label="关闭能力视图" onClick={onClose}><X aria-hidden="true" size={18} /></button></header>
    <div className="session-capabilities-scroll">
      <p className="session-capabilities-intro">这里查看当前运行端为这段对话装配的工具与连接记录。具体调用仍需能力和审批检查；刷新只读取元数据。</p>
      {!connected && <p role="status">连接已中断，当前能力待核对。</p>}
      <div className="session-capabilities-actions"><button type="button" disabled={!connected || view.busy} onClick={onRefresh}>{view.busy ? "读取中…" : "刷新当前对话"}</button>
        {pendingId && view.snapshot?.attention_id === pendingId && <button type="button" onClick={() => { onClose(); onFocusConfirmation(); }}>查看当前确认</button>}</div>
      <section aria-label="工具清单"><h3>{inventory ? `${stale ? "上次记录：" : ""}已装配 ${inventory.items.length}${inventory.truncated ? "+" : ""} 项工具` : "工具清单尚未确认"}</h3>
        {inventory ? <>{stale && <p role="status">记录已过期或连接中断，当前是否仍装配这些工具待核对。</p>}
          {inventory.truncated && <p role="status">清单未完整取得，未列出的工具保持未知。</p>}
          {inventory.items.length === 0 && <p>本次观察未列出工具，不据此判断其他会话或设备的能力。</p>}
          <ToolGroups inventory={inventory} /></> : <p>{view.snapshot?.status === "not_loaded" ? "此会话尚未初始化；不会为查看能力创建会话或调用模型。" : "尚未取得兼容的工具清单；可以核对下面的资源观察，或手动刷新。"}</p>}
      </section>
      <section aria-label="连接观察"><h3>连接与交互状态</h3><SessionResourceDetails view={view} connected={connected} hostLabel={hostLabel} /></section>
    </div>
    <footer><p>本机设置影响这台 Mac；远端对话需在对应运行端配置。</p><div><button type="button" onClick={() => { onClose(); onManage(); }}>管理当前连接</button><button type="button" onClick={() => { onClose(); onLocalSettings(); }}>这台 Mac 的权限设置</button></div></footer>
  </dialog>;
  return content;
}

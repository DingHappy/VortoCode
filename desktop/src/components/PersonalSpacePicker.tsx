import { useRef, useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";
import type { ConnectionState, DesktopProjectProfile } from "../types";
import type { DesktopContext } from "../lib/desktopContext";
import { personalSpaceDirectory } from "../lib/personalSpace";
import { errorText } from "../lib/errorText";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { Button, IconButton } from "./ui";
import "./PersonalSpacePicker.css";

const connectionLabel: Record<ConnectionState, string> = {
  connected: "已连接", connecting: "连接中", disconnected: "未连接", error: "连接中断",
};
function origin(value: string) {
  try { return new URL(value).origin; } catch { return "连接地址待核对"; }
}

export function PersonalSpacePicker({ context, profiles, localResidentId, connection, switching, onSelect, onManage }: {
  context: DesktopContext; profiles: DesktopProjectProfile[]; localResidentId: string | null;
  connection: ConnectionState; switching: boolean; onSelect: (profile: DesktopProjectProfile) => Promise<void>; onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status = connection === "connected" && !context.ready ? "正在核对目标" : connectionLabel[connection];
  return <div className="vc-space-picker">
    <button type="button" className="vc-space-trigger" aria-label={`当前空间：${context.runtimeLabel}，${status}。切换空间`}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      <span><strong>{context.runtimeLabel}</strong><small>{context.location} · {status}</small></span>
      <ChevronsUpDown aria-hidden="true" />
    </button>
    {open && <PersonalSpaceDialog context={context} profiles={profiles} localResidentId={localResidentId}
      switching={switching} onSelect={onSelect} onClose={() => setOpen(false)}
      onManage={() => { setOpen(false); onManage(); }} />}
  </div>;
}

export function PersonalSpaceDialog({ context, profiles, localResidentId, switching, onSelect, onClose, onManage }: {
  context: DesktopContext; profiles: DesktopProjectProfile[]; localResidentId: string | null; switching: boolean;
  onSelect: (profile: DesktopProjectProfile) => Promise<void>; onClose: () => void; onManage: () => void;
}) {
  const dialog = useDialogSurface();
  const [pending, setPending] = useState<string | null>(null), [error, setError] = useState("");
  const inFlight = useRef(false);
  const spaces = personalSpaceDirectory(profiles).spaces.filter(space => space.profile);
  const select = async (profile: DesktopProjectProfile) => {
    if (inFlight.current || switching) return;
    inFlight.current = true; setPending(profile.id); setError("");
    try { await onSelect(profile); onClose(); }
    catch (failure) { setError(errorText(failure, "空间切换未完成，请重试")); }
    finally { inFlight.current = false; setPending(null); }
  };
  return <dialog className="vc-space-dialog" ref={dialog} aria-labelledby="personal-space-title" onCancel={event => {
    if (inFlight.current || switching) event.preventDefault(); else onClose();
  }}>
    <header><h2 id="personal-space-title">个人空间</h2><IconButton label="关闭空间选择" icon={<X aria-hidden="true" />}
      disabled={Boolean(pending) || switching} onClick={onClose} /></header>
    <p>每个运行端有自己的对话、资料和项目。切换空间后，未发送的草稿留在原会话。</p>
    {!context.space?.runtime.registrationId && <div className="vc-space-current"><strong>{context.runtimeLabel}</strong>
      <span>{context.location} · 当前目标</span>{context.projectRoot && <span>{context.projectRoot}</span>}</div>}
    <ul aria-label="已登记的个人空间">
      {spaces.map(space => {
        const profile = space.profile!;
        const current = space.id === context.space?.id;
        return <li key={space.id}><button type="button" className="vc-space-choice" aria-current={current ? "true" : undefined}
          disabled={Boolean(pending) || switching || current && context.ready} onClick={() => void select(profile)}>
          <span><strong>{profile.name}</strong><small>{localResidentId === profile.id ? "本机常驻" : origin(profile.baseUrl)}</small>
            <small>连接登记：{profile.id}</small></span>
          {pending === profile.id ? <span role="status">连接中…</span> : current ? <Check aria-label="当前空间" /> : null}
        </button></li>;
      })}
    </ul>
    {!spaces.length && <p>还没有登记常驻助手。</p>}
    {error && <p role="alert">{error}</p>}
    <footer><Button disabled={Boolean(pending) || switching} onClick={onManage}>管理运行位置</Button></footer>
  </dialog>;
}

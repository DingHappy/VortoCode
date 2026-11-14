import { type ReactNode, useRef, useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { RoleAvatar } from "./RoleAvatar";
import { COPY } from "../lib/copy.generated";
import type { AssistantAvatar, ConnectionState, DesktopProjectProfile } from "../types";
import type { DesktopContext } from "../lib/desktopContext";
import { personalSpaceDirectory } from "../lib/personalSpace";
import { errorText } from "../lib/errorText";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { Button, IconButton, PreferenceDetails } from "./ui";
import "./PersonalSpacePicker.css";

const connectionLabel: Record<ConnectionState, string> = {
  connected: "已连接", connecting: "连接中", disconnected: "未连接", error: "连接中断",
};
function origin(value: string) {
  try { return new URL(value).origin; } catch { return "连接地址待核对"; }
}

export function PersonalSpacePicker({ context, profiles, localResidentId, connection, switching, avatar, collapseControl, onSelect, onManage }: {
  context: DesktopContext; profiles: DesktopProjectProfile[]; localResidentId: string | null; avatar?: AssistantAvatar | null; collapseControl?: ReactNode;
  connection: ConnectionState; switching: boolean; onSelect: (profile: DesktopProjectProfile) => Promise<void>; onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const status = connection === "connected" && !context.ready ? "正在核对目标" : connectionLabel[connection];
  return <div className="vc-space-picker">
    <div className="vc-space-heading"><button type="button" className="vc-space-trigger" aria-label={`当前空间：${context.runtimeLabel}，${status}。切换空间`}
      aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>
      <RoleAvatar character="general" avatar={avatar} />
      <span><strong>{context.space ? COPY.characters.personal_space : context.runtimeLabel}</strong><small><span>{context.space ? context.runtimeLabel : context.location}</span><span className="vc-space-connection">· {status}</span></small></span>
      <ChevronsUpDown aria-hidden="true" />
    </button>{collapseControl}</div>
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
    <header><h2 id="personal-space-title">切换个人空间</h2><IconButton label="关闭空间选择" icon={<X aria-hidden="true" />}
      disabled={Boolean(pending) || switching} onClick={onClose} /></header>
    <p>选择要继续工作的个人空间。</p>
    {!context.space?.runtime.registrationId && <div className="vc-space-current"><strong>{context.runtimeLabel}</strong>
      <span>{context.location} · 当前目标</span>{context.projectRoot && <span>{context.projectRoot}</span>}</div>}
    <ul aria-label="已登记的个人空间">
      {spaces.map(space => {
        const profile = space.profile!;
        const current = space.id === context.space?.id;
        const ambiguous = spaces.filter(item => item.profile?.name === profile.name && origin(item.profile.baseUrl) === origin(profile.baseUrl)).length > 1;
        return <li key={space.id}><button type="button" className="vc-space-choice" aria-current={current ? "true" : undefined}
          disabled={Boolean(pending) || switching || current && context.ready} onClick={() => void select(profile)}>
          <span><strong>{profile.name}</strong><small>{localResidentId === profile.id ? "本机常驻" : origin(profile.baseUrl)}</small>
            {ambiguous && <small>同名连接 · {profile.id.slice(-6)}</small>}</span>
          {pending === profile.id ? <span role="status">连接中…</span> : current ? <Check aria-label="当前空间" /> : null}
        </button><PreferenceDetails label={`${profile.name}的连接信息`}><p>连接登记：{profile.id}</p><p>{origin(profile.baseUrl)}</p></PreferenceDetails></li>;
      })}
    </ul>
    {!spaces.length && <p>还没有登记常驻助手。</p>}
    {error && <p role="alert">{error}</p>}
    <PreferenceDetails label="关于空间切换"><p>每个运行端有自己的对话、资料和项目。切换空间后，未发送的草稿留在原会话。</p></PreferenceDetails>
    <footer><Button disabled={Boolean(pending) || switching} onClick={onManage}>管理运行位置</Button></footer>
  </dialog>;
}

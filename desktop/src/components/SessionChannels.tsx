import { useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Hash, Plus, Trash2 } from "lucide-react";
import type { GatewayClient } from "../gateway";
import type { SessionSummary } from "../types";
import type { SettingsTargetLease } from "../lib/settingsTarget";
import { sessionChannel } from "../lib/sessionChannels";
import { COPY } from "../lib/copy.generated";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { Button, GroupTitle, IconButton, NavItem } from "./ui";
import "./SessionChannels.css";

export function SidebarChannels({ channels, current, query = "", disabled = false, onSelect, onCreate, onDelete }: {
  channels: SessionSummary[]; current: string; query?: string; disabled?: boolean;
  onSelect: (sid: string) => void; onCreate: () => void; onDelete?: (sid: string) => void;
}) {
  const keyword = query.trim().toLocaleLowerCase();
  const rows = channels.filter(row => {
    const channel = sessionChannel(row);
    return channel && `${row.title}\n${channel.topic}`.toLocaleLowerCase().includes(keyword);
  });
  return <section className="vc-channels" aria-label={COPY.nav.channels}>
    <div className="vc-channels-heading"><GroupTitle>{COPY.nav.channels}</GroupTitle>
      <IconButton label={COPY.channel.create} icon={<Plus />} disabled={disabled} onClick={onCreate} /></div>
    {rows.map(row => <div className="vc-channel-row" key={row.sid}>
      <NavItem icon={<Hash aria-hidden="true" />} label={row.title} title={[row.title, sessionChannel(row)!.topic].filter(Boolean).join("\n")}
        current={current === row.sid} disabled={disabled} onClick={() => onSelect(row.sid)}
        meta={!disabled && row.status === "working" ? <i className="vc-live-dot" aria-label="进行中" /> : undefined} />
      {onDelete && <IconButton className="vc-channel-remove" label={`${COPY.channel.remove} ${row.title}`}
        icon={<Trash2 />} disabled={disabled} onClick={() => onDelete(row.sid)} />}
    </div>)}
    {!rows.length && <p className="vc-side-hint" role="status">{keyword ? "没有匹配的频道" : disabled ? "连接空间后可查看和创建频道" : COPY.channel.empty}</p>}
  </section>;
}

export function ChannelWelcome({ title, topic }: { title: string; topic: string }) {
  return <div className="vc-channel-welcome"><Hash aria-hidden="true" /><h1>{title}</h1>
    <p>{topic || COPY.channel.empty}</p><small>从下面发一条消息，开始这个话题。</small></div>;
}

export function CreateChannelDialog({ lease, onClose, onCreated }: {
  lease: SettingsTargetLease<Pick<GatewayClient, "createChannel">>; onClose: () => void; onCreated: (row: SessionSummary) => void;
}) {
  const dialog = useDialogSurface(), titleId = useId(), noteId = useId();
  const [name, setName] = useState(""), [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const sending = useRef(false);
  const valid = lease.current() && Boolean(lease.target.space);
  const title = name.trim().replace(/^#+/, "").trim();
  const submit = async () => {
    if (sending.current || !valid || !title || title.length > 30) return;
    sending.current = true; setBusy(true); setError("");
    try {
      if (!lease.client) throw new Error("当前空间尚未连接");
      const row = await lease.client.createChannel(title, topic.trim());
      lease.requireCurrent();
      onCreated(row);
    } catch (reason) { setError(!lease.current() ? "创建回执属于原空间，未打开这个频道。请到原空间核对是否已保存。"
      : reason instanceof Error ? reason.message : "频道未能保存，请核对当前空间后再试"); }
    finally { sending.current = false; setBusy(false); }
  };
  const content = <dialog ref={dialog} className="vc-channel-dialog" aria-labelledby={titleId} aria-describedby={noteId}
    onCancel={event => { event.preventDefault(); if (!sending.current) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <div className="vc-channel-dialog-heading"><Hash aria-hidden="true" /><h2 id={titleId}>{COPY.channel.create}</h2></div>
      <p className="vc-channel-target">{lease.target.runtimeLabel} · {lease.target.location}</p>
      <p id={noteId}>{COPY.channel.scope_hint}</p>
      <label>{COPY.channel.name}<input autoFocus required maxLength={30} value={name} placeholder={COPY.channel.name_placeholder}
        disabled={busy || !valid} onChange={event => setName(event.target.value)} /></label>
      <label>{COPY.channel.topic}<textarea rows={3} maxLength={300} value={topic} placeholder={COPY.channel.topic_placeholder}
        disabled={busy || !valid} onChange={event => setTopic(event.target.value)} /></label>
      <p className="vc-channel-note">{COPY.channel.permission_hint}</p>
      {!valid && <p role="alert">空间连接已变化。请关闭后重新打开；如已提交，请到原空间核对频道，没有自动重试。</p>}
      {error && <p role="alert">{error}</p>}
      <div className="vc-channel-dialog-actions"><Button disabled={busy} onClick={onClose}>{COPY.channel.cancel}</Button>
        <Button type="submit" variant="primary" disabled={busy || !valid || !title || title.length > 30}>{busy ? COPY.channel.saving : COPY.channel.create}</Button></div>
    </form>
  </dialog>;
  return typeof document === "undefined" ? content : createPortal(content, document.body);
}

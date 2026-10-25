// 设置 → 能力 →「让助理查看 Codex」：Mac 上的本机引擎看着 Codex（OpenAI 的编码助手），把精简进度报给常驻助理。
// 默认开；只在登记了常驻电脑时起作用。地址和令牌在本机引擎启动时注入，所以改了要重开 Desktop 才生效——
// 不当场重启引擎：重启会把界面连回本机，正在用常驻电脑上的助理时会被拉下来。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

import { errorText } from "../../lib/errorText";
import { codexObserverReads, codexObserverView, type CodexObserverRead } from "../../lib/codexObserverHealth";
import { startPolling } from "../../lib/polling";

export function CodexObserverStatus({ value, busy, now, onRefresh }: {
  value: CodexObserverRead; busy: boolean; now: number; onRefresh: () => void;
}) {
  const view = codexObserverView(value, now);
  return <div aria-label="本机 Codex 观察员状态">
    <div className="settings-card-head"><strong>{view.title}</strong>
      <button disabled={busy} onClick={onRefresh}>{busy ? "核对中…" : "刷新状态"}</button></div>
    <p className="settings-card-note" role="status">{view.detail}</p>
    {view.acknowledgedAt !== null && <p className="settings-card-note">最近确认送达：
      <time dateTime={new Date(view.acknowledgedAt * 1000).toISOString()}>{new Date(view.acknowledgedAt * 1000).toLocaleString()}</time></p>}
  </div>;
}

export function CodexWatchCard({ hasResident, readSnapshot }: {
  hasResident: boolean; readSnapshot?: () => Promise<CodexObserverRead>;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<CodexObserverRead>({ status: "loading" });
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now);
  const refreshRef = useRef<(() => Promise<void>) | null>(null);

  useEffect(() => {
    let alive = true;
    void invoke<boolean>("get_codex_watch_enabled").then(value => { if (alive) setEnabled(value); })
      .catch(() => { if (alive) setEnabled(null); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    setBusy(false);
    if (!readSnapshot) { setHealth({ status: "unsupported" }); return; }
    let active = true;
    setHealth({ status: "loading" });
    const reads = codexObserverReads({ read: readSnapshot, current: () => active, publish: setHealth });
    const refresh = async () => {
      if (!active) return;
      setBusy(true);
      try { await reads.refresh(); }
      finally { if (active) { setBusy(false); setNow(Date.now()); } }
    };
    refreshRef.current = refresh;
    const stop = startPolling(refresh, { intervalMs: 10_000, hiddenIntervalMs: 60_000, immediate: true, visibility: document });
    return () => { active = false; stop(); reads.invalidate(); if (refreshRef.current === refresh) refreshRef.current = null; };
  }, [readSnapshot]);

  const change = async (next: boolean) => {
    if (saving) return;
    setSaving(true);
    setNote("");
    try {
      setEnabled(await invoke<boolean>("set_codex_watch_enabled", { enabled: next }));
      setNote("已保存：⌘Q 退出 Desktop 再打开后生效");
    } catch (error) { setNote(errorText(error, "保存失败")); }
    finally { setSaving(false); }
  };

  return <section className="settings-card" aria-label="让助理查看 Codex">
    <div className="settings-card-head"><strong>让助理查看 Codex</strong></div>
    <label className="settings-check">
      <input type="checkbox" checked={enabled ?? false} disabled={enabled === null || !hasResident || saving}
        onChange={(event) => void change(event.target.checked)} />
      <span>把 Codex 的进度报给常驻助理：在做什么、做到哪、等不等你批准</span>
    </label>
    <p className="settings-card-note">{hasResident
      ? "助理据此回答「Codex 在干嘛」。等你批准或回答超过 2 分钟时提醒，结束轮次由定时巡视汇总。只发任务、计划、最后几句说明、改动的文件名和等批准的命令，不发完整对话和代码。"
      : "需要先连接常驻电脑（助理 → 连接常驻电脑）。"}</p>
    {note && <p className="settings-card-note" role="status">{note}</p>}
    <p className="settings-card-note">开关表示下次启动时的配置；下面是当前本机引擎的运行状态。</p>
    <CodexObserverStatus value={health} busy={busy} now={now} onRefresh={() => { void refreshRef.current?.(); }} />
  </section>;
}

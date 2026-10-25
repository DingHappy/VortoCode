// 设置 → 个性化：用户级全局指令（~/.vortocode/AGENTS.md），对之后所有新会话生效。
// 保存走原生确认（它会进所有会话的系统提示），失败或取消时保留草稿。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";

import { useNativeSetting } from "../../hooks/useNativeSetting";
import { SettingsReadFeedback } from "./SettingsReadFeedback";
import { errorText } from "../../lib/errorText";
import type { UserInstructions } from "../../types";

const MAX_CHARS = 20_000;

export function PersonalizePane({ onDraftStateChange }: { onDraftStateChange?: (state: { dirty: boolean; busy: boolean }) => void }) {
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  const busyRef = useRef(false);
  const setting = useNativeSetting<UserInstructions>("get_user_instructions", "读取全局指令失败", value => setDraft(value.content));
  const loaded = setting.value;
  const unavailable = setting.loading || Boolean(setting.error) || !loaded;

  const save = async () => {
    if (busyRef.current || unavailable || !loaded || draft === loaded.content) return;
    busyRef.current = true;
    setSaving(true);
    setStatus("");
    try {
      const saved = await invoke<UserInstructions>("set_user_instructions", { content: draft });
      setting.replace(saved);
      setDraft(saved.content);
      setStatus("已保存，之后新建的会话生效");
    } catch (failure) {
      setStatus(errorText(failure, "保存失败"));
    } finally {
      busyRef.current = false;
      setSaving(false);
    }
  };

  const dirty = loaded !== null && draft !== loaded.content;
  useEffect(() => { onDraftStateChange?.({ dirty, busy: saving }); }, [dirty, saving, onDraftStateChange]);
  return (
    <div className="settings-stack">
      <p className="settings-intro">为你与 VortoCode 的所有对话和任务提供额外说明和上下文（包括通用会话与自动化任务），新会话生效。</p>
      <section className="settings-card" aria-label="全局指令" aria-busy={setting.loading}>
        <div className="settings-card-head"><strong>全局指令</strong></div>
        <SettingsReadFeedback label="全局指令" loading={setting.loading} error={setting.error} onRetry={setting.reload} />
        <textarea
          className="settings-textarea"
          value={draft}
          onChange={(event) => { setDraft(event.target.value); setStatus(""); }}
          placeholder="例如：回答用中文；改代码前先说明计划；提交信息用英文。"
          maxLength={MAX_CHARS}
          disabled={unavailable || saving}
        />
        <div className="settings-card-foot">
          <span role="status">{loaded?.path ? `保存在 ${loaded.path}` : ""}{status ? ` · ${status}` : ""}</span>
          <button className="primary" onClick={() => void save()} disabled={!dirty || saving || unavailable}>{saving ? "保存中…" : "保存"}</button>
        </div>
      </section>
    </div>
  );
}

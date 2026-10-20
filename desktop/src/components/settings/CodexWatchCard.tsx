// 设置 → 能力 →「让助理查看 Codex」：Mac 上的本机引擎看着 Codex（OpenAI 的编码助手），把精简进度报给常驻助理。
// 默认开；只在登记了常驻电脑时起作用。地址和令牌在本机引擎启动时注入，所以改了要重开 Desktop 才生效——
// 不当场重启引擎：重启会把界面连回本机，正在用常驻电脑上的助理时会被拉下来。
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";

export function CodexWatchCard({ hasResident }: { hasResident: boolean }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    void invoke<boolean>("get_codex_watch_enabled").then(setEnabled).catch(() => setEnabled(null));
  }, []);

  const change = async (next: boolean) => {
    setNote("");
    try {
      setEnabled(await invoke<boolean>("set_codex_watch_enabled", { enabled: next }));
      setNote("已保存：⌘Q 退出 Desktop 再打开后生效");
    } catch (error) { setNote(errorText(error, "保存失败")); }
  };

  return <section className="settings-card" aria-label="让助理查看 Codex">
    <div className="settings-card-head"><strong>让助理查看 Codex</strong></div>
    <label className="settings-check">
      <input type="checkbox" checked={enabled ?? false} disabled={enabled === null || !hasResident}
        onChange={(event) => void change(event.target.checked)} />
      <span>把 Codex 的进度报给常驻助理：在做什么、做到哪、等不等你批准</span>
    </label>
    <p className="settings-card-note">{hasResident
      ? "助理据此回答「Codex 在干嘛」，Codex 等你批准或问你问题超过 2 分钟、做完一轮时推到手机。只发任务、计划、最后几句说明、改动的文件名和等批准的命令，不发完整对话和代码。"
      : "需要先连接常驻电脑（助理 → 连接常驻电脑）。"}</p>
    {note && <p className="settings-card-note" role="status">{note}</p>}
  </section>;
}

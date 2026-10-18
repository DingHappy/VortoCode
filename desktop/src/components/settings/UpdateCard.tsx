// 设置 → 常规 →「更新」：手动检查、自动检查开关、有新版时打开下载页。
import { invoke } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";

import { errorText } from "../../lib/errorText";
import { checkForUpdate, type UpdateCheck } from "../../lib/updateCheck";

export function UpdateCard() {
  const [auto, setAuto] = useState<boolean | null>(null);
  const [result, setResult] = useState<UpdateCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    void invoke<boolean>("get_update_check_enabled").then(setAuto).catch(() => setAuto(null));
  }, []);

  const check = async () => {
    setBusy(true); setNote("");
    try { setResult(await checkForUpdate()); }
    catch (error) { setNote(errorText(error, "检查更新失败")); }
    finally { setBusy(false); }
  };

  return <section className="settings-card" aria-label="更新">
    <div className="settings-card-head">
      <strong>更新</strong>
      <button onClick={() => void check()} disabled={busy}>{busy ? "检查中…" : "检查更新"}</button>
    </div>
    {result && (result.newer
      ? <p>有新版本 <strong>{result.latest}</strong>（当前 {result.current}）。
          <button className="primary" onClick={() => void openUrl(result.url)}>打开下载页</button></p>
      : <p>已经是最新版本（{result.current}）。</p>)}
    {note && <p role="alert">{note}</p>}
    <label className="settings-check">
      <input type="checkbox" checked={auto ?? false} disabled={auto === null}
        onChange={(event) => {
          const enabled = event.target.checked;
          void invoke<boolean>("set_update_check_enabled", { enabled }).then(setAuto).catch((error) => setNote(errorText(error, "保存失败")));
        }} />
      <span>启动时自动检查（每天最多一次，只问 GitHub 最新版本号）</span>
    </label>
    <p className="settings-card-note">只提醒、不自动下载安装：下载 DMG 后拖进「应用程序」替换，⌘Q 退出再打开即可。</p>
  </section>;
}

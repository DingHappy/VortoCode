// 侧栏底部：账号、有新版时的「更新并重启」、设置入口。
import { Settings2, UserRound } from "lucide-react";
import { useState } from "react";

import { errorText } from "../lib/errorText";
import { installUpdate, type UpdateCheck } from "../lib/updateCheck";
import type { SidebarIdentity } from "../lib/vortoAccount";

export function SidebarFooter({ identity, update, onOpenAccount, onOpenSettings, install = installUpdate }: {
  identity: SidebarIdentity;
  /** 启动时自动检查到的新版；null = 没有新版或没查 */
  update: UpdateCheck | null;
  onOpenAccount: () => void;
  onOpenSettings: () => void;
  /** 测试替身；默认走 Rust 侧的下载、验签、安装和重启 */
  install?: () => Promise<unknown>;
}) {
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState("");
  return (
    <div className="sidebar-footer">
      <button className="sidebar-account" title={identity.title} onClick={onOpenAccount}>
        <span className="sidebar-avatar">{identity.initial || <UserRound size={14} />}</span>
        <span className="sidebar-identity">
          <span>{identity.name}</span>
          {identity.detail && <small>{identity.detail}</small>}
        </span>
      </button>
      {update && (
        <button className="sidebar-update" disabled={updating}
          title={updateError || `VortoCode ${update.latest} 已发布，点击更新并重启`}
          onClick={() => {
            setUpdating(true); setUpdateError("");
            void install()
              .catch((error) => setUpdateError(errorText(error, "更新失败，可在设置 → 高级 → 更新里重试或下载")))
              .finally(() => setUpdating(false));
          }}>{updating ? "正在更新…" : `新版本 ${update.latest}`}</button>
      )}
      <button className="sidebar-gear" aria-label="设置" title="设置" onClick={onOpenSettings}>
        <Settings2 size={16} />
      </button>
    </div>
  );
}

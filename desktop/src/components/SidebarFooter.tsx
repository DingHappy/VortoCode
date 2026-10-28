// 侧栏底部：常驻电脑的状态（助理模式）、账号、有新版时的「更新并重启」、设置入口。
import { Laptop, Server, Settings2, UserRound } from "lucide-react";
import { useState } from "react";

import { errorText } from "../lib/errorText";
import { installUpdate, type UpdateCheck } from "../lib/updateCheck";
import type { SidebarIdentity } from "../lib/vortoAccount";
import { Button, IconButton, NavItem } from "./ui";

/** 助理住在哪：在线 / 连不上 / 没连（只在本机运行）。 */
export type SidebarResident = { state: "online" | "offline" | "local"; name?: string; onClick: () => void };

const RESIDENT_STATE: Record<SidebarResident["state"], { word: string; title: string }> = {
  online: { word: "在线", title: "合上笔记本它也照常干活" },
  offline: { word: "连不上", title: "点击重新连接常驻电脑" },
  local: { word: "未连接", title: "助理现在在本机运行，合上笔记本就会停；点击连接常驻电脑" },
};

export function SidebarFooter({ identity, update, resident, onOpenAccount, onOpenSettings, install = installUpdate }: {
  identity: SidebarIdentity;
  /** 启动时自动检查到的新版；null = 没有新版或没查 */
  update: UpdateCheck | null;
  /** 只在助理模式给 */
  resident?: SidebarResident;
  onOpenAccount: () => void;
  onOpenSettings: () => void;
  /** 测试替身；默认走 Rust 侧的下载、验签、安装和重启 */
  install?: () => Promise<unknown>;
}) {
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState("");
  return (
    <div className="vc-side-footer">
      {resident && (
        <NavItem
          className={`vc-side-resident ${resident.state}`}
          icon={resident.state === "local" ? <Laptop /> : <Server />}
          label="常驻电脑"
          title={`${resident.name && resident.name !== "常驻电脑" ? `「${resident.name}」 · ` : ""}${RESIDENT_STATE[resident.state].title}`}
          meta={<span className="vc-side-resident-state"><i aria-hidden="true" />{RESIDENT_STATE[resident.state].word}</span>}
          onClick={resident.onClick}
        />
      )}
      {update && (
        <Button variant="secondary" disabled={updating} className="vc-side-update"
          title={updateError || `VortoCode ${update.latest} 已发布，点击更新并重启`}
          onClick={() => {
            setUpdating(true); setUpdateError("");
            void install()
              .catch((error) => setUpdateError(errorText(error, "更新失败，可在设置 → 高级 → 更新里重试或下载")))
              .finally(() => setUpdating(false));
          }}>{updating ? "正在更新…" : `新版本 ${update.latest}`}</Button>
      )}
      <div className="vc-side-account-row">
        <button type="button" className="vc-side-account" title={identity.title} onClick={onOpenAccount}>
          <span className="vc-avatar">{identity.initial || <UserRound />}</span>
          <span className="vc-side-identity">
            <span>{identity.name}</span>
            {identity.detail && <small>{identity.detail}</small>}
          </span>
        </button>
        <IconButton label="设置" icon={<Settings2 />} onClick={onOpenSettings} />
      </div>
    </div>
  );
}

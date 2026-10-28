// 侧栏底部：常驻电脑的状态（助理模式）、有新版时的「更新并重启」、账号。
// 点账号弹出头像菜单：账号与会员、手机与设备、检查更新 · 诊断日志、设置（docs/DESIGN_SYSTEM.md 第 2 节「头像菜单与设置」）。
import { CircleUser, Laptop, RefreshCw, Server, Settings2, Smartphone, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { errorText } from "../lib/errorText";
import { installUpdate, type UpdateCheck } from "../lib/updateCheck";
import type { SidebarIdentity } from "../lib/vortoAccount";
import type { SettingsSection } from "../lib/settingsNavigation";
import { Button, Menu, MenuItem, MenuSeparator, NavItem } from "./ui";

/** 助理住在哪：在线 / 连不上 / 没连（只在本机运行）。 */
export type SidebarResident = { state: "online" | "offline" | "local"; name?: string; onClick: () => void };

const RESIDENT_STATE: Record<SidebarResident["state"], { word: string; title: string }> = {
  online: { word: "在线", title: "合上笔记本它也照常干活" },
  offline: { word: "连不上", title: "点击重新连接常驻电脑" },
  local: { word: "未连接", title: "助理现在在本机运行，合上笔记本就会停；点击连接常驻电脑" },
};

/** 打开设置；可指定分区和要滚到的条目（settingsNavigation 里的条目 id） */
export type OpenSettings = (target?: { section: SettingsSection; anchor?: string }) => void;

export function SidebarFooter({ identity, update, resident, onOpenSettings, install = installUpdate, menuOpen: initialMenuOpen = false }: {
  identity: SidebarIdentity;
  /** 启动时自动检查到的新版；null = 没有新版或没查 */
  update: UpdateCheck | null;
  /** 只在助理模式给 */
  resident?: SidebarResident;
  onOpenSettings: OpenSettings;
  /** 测试替身；默认走 Rust 侧的下载、验签、安装和重启 */
  install?: () => Promise<unknown>;
  /** 测试和验收页用：一开始就展开头像菜单 */
  menuOpen?: boolean;
}) {
  const [updating, setUpdating] = useState(false);
  const [updateError, setUpdateError] = useState("");
  const [menuOpen, setMenuOpen] = useState(initialMenuOpen);
  const rowRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // 菜单开着时：点外面或按 Esc 收起，Esc 把焦点还给账号按钮；打开时焦点落在第一项
  useEffect(() => {
    if (!menuOpen) return;
    rowRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const onPointer = (event: PointerEvent) => {
      if (!rowRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation(); setMenuOpen(false); triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("pointerdown", onPointer); document.removeEventListener("keydown", onKey, true); };
  }, [menuOpen]);
  const pick = (target?: Parameters<OpenSettings>[0]) => { setMenuOpen(false); onOpenSettings(target); };
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
      <div className="vc-side-account-row" ref={rowRef}>
        {menuOpen && <Menu className="vc-side-menu" aria-label="账号菜单">
          <MenuItem icon={<CircleUser />} label="账号与会员" onClick={() => pick({ section: "account", anchor: "account" })} />
          <MenuItem icon={<Smartphone />} label="手机与设备" onClick={() => pick({ section: "account", anchor: "mobile-account" })} />
          <MenuItem icon={<RefreshCw />} label="检查更新 · 诊断日志" onClick={() => pick({ section: "advanced", anchor: "version" })} />
          <MenuSeparator />
          <MenuItem icon={<Settings2 />} label={<>设置<kbd>⌘,</kbd></>} onClick={() => pick()} />
        </Menu>}
        <button ref={triggerRef} type="button" className="vc-side-account" title={identity.title}
          aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          <span className="vc-avatar">{identity.initial || <UserRound />}</span>
          <span className="vc-side-identity">
            <span>{identity.name}</span>
            {identity.detail && <small>{identity.detail}</small>}
          </span>
        </button>
      </div>
    </div>
  );
}

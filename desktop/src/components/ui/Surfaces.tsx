// 侧栏项、分组标题、列表行、卡片、菜单、空状态（docs/DESIGN_SYSTEM.md 第 6 节）。
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { joinClass } from "./Button";

type NavItemProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon?: ReactNode;
  label: ReactNode;
  meta?: ReactNode;
  current?: boolean;
};

export function NavItem({ icon, label, meta, current = false, className, type = "button", ...rest }: NavItemProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-current={current ? "page" : undefined}
      className={joinClass("vc-nav-item", className)}
    >
      {icon}
      <span className="vc-nav-item__label">{label}</span>
      {meta != null && <span className="vc-nav-item__meta">{meta}</span>}
    </button>
  );
}

export function GroupTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={joinClass("vc-group-title", className)}>{children}</h3>;
}

type ListRowProps = {
  lead?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  trail?: ReactNode;
  className?: string;
};

export function ListRow({ lead, title, description, trail, className }: ListRowProps) {
  return (
    <li className={joinClass("vc-row", className)}>
      <span className="vc-row__lead">{lead}</span>
      <div className="vc-row__body">
        <div className="vc-row__title">{title}</div>
        {description != null && <div className="vc-row__desc">{description}</div>}
      </div>
      {trail != null ? <span className="vc-row__trail">{trail}</span> : <span />}
    </li>
  );
}

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={joinClass("vc-card", className)} />;
}

type SelectableCardProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-pressed"> & { selected: boolean };

export function SelectableCard({ selected, className, type = "button", ...rest }: SelectableCardProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-pressed={selected}
      className={joinClass("vc-card", "vc-card--selectable", className)}
    />
  );
}

export function Menu({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div role="menu" {...rest} className={joinClass("vc-menu", className)} />;
}

type MenuItemProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & { icon?: ReactNode; label: ReactNode };

export function MenuItem({ icon, label, className, type = "button", ...rest }: MenuItemProps) {
  return (
    <button {...rest} type={type} role="menuitem" className={joinClass("vc-menu-item", className)}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

export function MenuSeparator() {
  return <hr className="vc-menu-sep" role="separator" />;
}

// 空状态：一句话说明现状，再给一个下一步动作。
export function EmptyState({ message, action }: { message: ReactNode; action?: ReactNode }) {
  return (
    <div className="vc-empty">
      <p>{message}</p>
      {action}
    </div>
  );
}

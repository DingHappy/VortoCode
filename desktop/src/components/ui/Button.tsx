// 基础按钮（docs/DESIGN_SYSTEM.md 第 6 节）。样式在 styles/components.css。
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "text" | "danger";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: "md" | "lg";
  icon?: ReactNode;
};

export function joinClass(...names: Array<string | false | null | undefined>): string {
  return names.filter(Boolean).join(" ");
}

export function Button({ variant = "secondary", size = "md", icon, className, children, type = "button", ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={joinClass("vc-btn", `vc-btn--${variant}`, size === "lg" && "vc-btn--lg", className)}
    >
      {icon}
      {children}
    </button>
  );
}

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label" | "children"> & {
  // 只有图标的按钮必须说清楚它做什么：读屏用 aria-label，悬停提示默认同一句。
  label: string;
  icon: ReactNode;
};

export function IconButton({ label, icon, className, title, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-label={label}
      title={title ?? label}
      className={joinClass("vc-btn", "vc-btn--icon", className)}
    >
      {icon}
    </button>
  );
}

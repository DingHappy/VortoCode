// 开关（30×18，开启时强调蓝）。用 role="switch" 的真按钮，键盘和读屏都能用。
import { joinClass } from "./Button";

type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  className?: string;
};

export function Switch({ checked, onChange, label, disabled, className }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={joinClass("vc-switch", className)}
      onClick={() => onChange(!checked)}
    />
  );
}

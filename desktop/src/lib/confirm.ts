// 破坏性操作前的确认门（撤销修改、删除、push、清除配置……）。
//
// 不要用 window.confirm：Tauri 的 dialog 插件把它改写成 async 函数（还去调一个 2.x 已不存在的
// `confirm` 命令），同步写法 `if (!window.confirm(...)) return` 拿到的 Promise 恒为真值，
// 确认形同虚设。这里走 Rust 侧的 confirm_action，阻塞到用户点选为止。
//
// fail-closed：只有对话框明确返回 true 才放行；命令失败、拿不到结果或返回其他值一律视为取消。
import { invoke } from "@tauri-apps/api/core";

type Invoke = (command: string, args: Record<string, unknown>) => Promise<unknown>;

export async function confirmAction(message: string, call: Invoke = invoke): Promise<boolean> {
  try {
    return (await call("confirm_action", { message })) === true;
  } catch {
    return false;
  }
}

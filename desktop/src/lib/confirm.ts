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

export type ConfirmationReplyState = "idle" | "sending" | "awaiting" | "failed";
/** Transport success is not an approval receipt; stale replies cannot update a new card. */
export function createConfirmationReply<C>(read: () => {client:C|null;scope:string;id:string;state?:ConfirmationReplyState},
  send:(client:C,id:string,ok:boolean)=>Promise<void>,
  publish:(id:string,state:ConfirmationReplyState,error:string)=>void) {
  let generation=0;
  let active:{client:C;scope:string;id:string}|null=null;
  return {
    close(id?:string) { if(!id || active?.id===id){generation++;active=null;} },
    async submit(id:string,ok:boolean) {
      const now=read();
      if(!now.client || now.id!==id || now.state==='sending' || now.state==='awaiting' || now.state==='failed')return;
      if(active && active.client===now.client && active.scope===now.scope && active.id===id)return;
      const ticket=++generation;
      const target={client:now.client,scope:now.scope,id};active=target;
      const current=()=>{const n=read();return ticket===generation && n.client===target.client && n.scope===target.scope && n.id===id;};
      publish(id,'sending','');
      try {await send(target.client,id,ok);if(current())publish(id,'awaiting','');}
      catch {if(current())publish(id,'failed','发送结果未确认，请先核对运行端状态；不会自动重发批准。');}
      finally {if(ticket===generation)active=null;}
    },
  };
}

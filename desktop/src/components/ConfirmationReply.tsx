import type { ConfirmationReplyState } from "../lib/confirm";
export function ConfirmationReply({state="idle",error,connected,allowLabel="允许一次",onCheck,onAnswer}:{state?:ConfirmationReplyState;error?:string;connected:boolean;allowLabel?:string;onCheck:()=>void;onAnswer:(ok:boolean)=>void}) {
  const locked=!connected || state!=="idle";
  return <div className="confirmation-reply">
    {state!=="idle" && <p role="status">{state==="sending" ? "正在提交…" : state==="awaiting" ? "已发送，等待运行端回执" : error || "发送结果未确认，请先核对状态。"}</p>}
    {state==="idle" && error && <p role="status">{error}</p>}
    {(state==="awaiting" || state==="failed") && <button disabled={!connected} onClick={onCheck}>核对状态</button>}
    <div><button className="deny" disabled={locked} onClick={()=>onAnswer(false)}>拒绝</button><button className="allow" disabled={locked} onClick={()=>onAnswer(true)}>{allowLabel}</button></div>
  </div>;
}

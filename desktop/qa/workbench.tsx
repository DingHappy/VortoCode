import { useRef,useState } from "react";
import {createRoot} from "react-dom/client";
import {WorkbenchNav} from "../src/components/WorkbenchNav";
import {SessionInfo} from "../src/components/SessionInfo";
import {ConfirmationReply} from "../src/components/ConfirmationReply";
import {createConfirmationReply,type ConfirmationReplyState} from "../src/lib/confirm";
import {ManagementNav} from "../src/components/ManagementNav";
import type {InspectorTab} from "../src/lib/inspector";
import "../src/App.css";
function Fixture(){
 const [tab,setTab]=useState("info"),[plan,setPlan]=useState(true),[state,setState]=useState<ConfirmationReplyState>("idle"),[error,setError]=useState(""),[sent,setSent]=useState(0),[closed,setClosed]=useState(false),[action]=useState("");
 const view=useRef({client:{},scope:"qa",id:"approval",state});view.current.state=state;view.current.id=closed?"":"approval";
 const controller=useRef<ReturnType<typeof createConfirmationReply> | null>(null);
 if(!controller.current)controller.current=createConfirmationReply(()=>view.current,async()=>{setSent(n=>n+1);throw Error("synthetic transport failure");},(_id,next,message)=>{setState(next);setError(message);});
 return <main style={{padding:24,height:"100%",overflow:"auto"}}><h1>原组件工作台验收（合成快照）</h1><section aria-label="独立任务中心"><ManagementNav current={tab as InspectorTab} onSelect={setTab}/></section><p role="status">发送次数：{sent} · 动作：{action}</p><button onClick={()=>setPlan(v=>!v)}>切换无计划</button>
 <aside className="inspector" style={{width:340,maxWidth:"100%",height:"auto",marginTop:20}}><WorkbenchNav current={tab} label={tab} busy tabClass={id=>id===tab?"active":""} onSelect={setTab}><button onClick={()=>setTab("preview")}>预览</button></WorkbenchNav><div className="inspector-body">
 {tab==="info"?<SessionInfo busy connected needsInput={!closed} plan={plan?[{step:"读取来源",status:"completed"},{step:"核对结论",status:"in_progress"},{step:"交付",status:"pending"}]:[]} activities={[{id:"tool",kind:"tool",status:"running",label:"读取网页"}]} queued={0} model="合成模型" onRefresh={()=>{}}/>:<p>{tab}</p>}</div></aside>
 {!closed&&<section className="confirm-card"><div className="confirm-copy"><strong>合成操作确认</strong><ConfirmationReply connected state={state} error={error} onAnswer={ok=>void controller.current?.submit("approval",ok)} onCheck={()=>{controller.current?.close("approval");setState("idle");setError("运行端仍在等待，可重新选择本次批准。");}}/></div></section>}
 <button onClick={()=>{controller.current?.close("approval");setClosed(true);}}>模拟运行端关闭回执</button></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);

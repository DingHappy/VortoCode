import { useState } from "react";
import { createRoot } from "react-dom/client";
import { TurnTimeline } from "../src/components/TurnTimeline";
import { SessionInfo } from "../src/components/SessionInfo";
import "../src/App.css";
import type { TurnActivity } from "../src/types";
function Fixture() {
 const [active,setActive]=useState(true),[output,setOutput]=useState(false),[failed,setFailed]=useState(false);
 const items: TurnActivity[]=[{id:"thinking",kind:"phase",status:active?"running":"completed",phase:"thinking",label:"分析任务",durationMs:1800},{id:"tool",kind:"tool",status:failed?"failed":active?"running":"completed",name:"read_file",label:"读取配置",result:"保留的工具结果",durationMs:2400}];
 return <main style={{padding:32,background:"var(--n2)",color:"var(--n12)",height:"100%",overflow:"auto"}}><h1>过程与结果验收 · 合成数据</h1><button onClick={()=>{setActive(false);setOutput(true);}}>模拟最终输出</button><button onClick={()=>{setActive(false);setOutput(false);}}>模拟停止但无输出</button><button onClick={()=>setFailed(true)}>模拟失败</button><button onClick={()=>{setActive(true);setOutput(false);setFailed(false);}}>开始新回合</button><div style={{display:"grid",gridTemplateColumns:"minmax(0,1fr) 320px",gap:32,marginTop:24}}><section><TurnTimeline items={items} active={active} hasOutput={output}/>{output&&<p>最终输出：已核对配置（合成结果）。</p>}</section><SessionInfo busy={active} connected plan={[{step:"读取配置",status:active?"in_progress":"completed"},{step:"整理结果",status:output?"completed":"pending"}]} activities={items} queued={0} model="合成模型" onRefresh={()=>{}}/></div></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);

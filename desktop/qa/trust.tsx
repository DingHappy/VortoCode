import { ConfirmationTrust } from "../src/components/ConfirmationTrust";
import { useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useTrustLevel } from "../src/hooks/useTrustLevel";
import type { GatewayClient } from "../src/gateway";
import type { TrustStatus } from "../src/types";
import "../src/App.css";
const status = (level: "ask" | "reads"): TrustStatus => ({level,effective:level,ceiling:"reads",levels:["ask","reads"],capability_profile:"external",workspace_scope:"general",local_file_reads:true,supervised:true});
function Fixture() {
  const [workspace, setWorkspace] = useState("A");
  const [note, setNote] = useState("");
  const [approved, setApproved] = useState(false);
  const pending = useRef<{get?: (v:TrustStatus)=>void;put?: (v:TrustStatus)=>void; fail?: (e:Error)=>void}>({});
  const a = useMemo(()=>({getTrust:()=>new Promise<TrustStatus>((resolve)=>{pending.current.get=resolve}),setTrust:()=>new Promise<TrustStatus>((resolve,reject)=>{pending.current.put=resolve;pending.current.fail=reject})}),[]);
  const b = useMemo(()=>({getTrust:async()=>status("ask"),setTrust:async()=>status("reads")}),[]);
  const clientRef = useRef<GatewayClient|null>(null);
  clientRef.current = (workspace === "A" ? a : b) as unknown as GatewayClient;
  const {trust,trustBusy,changeTrustLevel} = useTrustLevel(clientRef,"connected",workspace,false,setNote);
  return <main style={{padding:24}}>
    <section className="confirm-card"><div className="confirm-copy"><p>读取本机测试文件</p><ConfirmationTrust trust={trust} busy={trustBusy} connected={true} tainted={false} onChange={changeTrustLevel}/><button onClick={()=>setApproved(true)}>允许一次</button><p>本次批准：{String(approved)}</p></div></section>
    <p>工作区：{workspace}</p><p>档位：{trust?.level ?? "加载中"}</p><p>忙：{String(trustBusy)}</p><output>{note}</output>
    <button onClick={()=>pending.current.get?.(status("ask"))}>返回 A 查询</button>
    <button onClick={()=>void changeTrustLevel("reads")}>只读免确认</button>
    <button onClick={()=>setWorkspace(workspace === "A" ? "B" : "A")}>切换工作区</button>
    <button onClick={()=>pending.current.get?.(status("reads"))}>迟到 A 查询</button>
    <button onClick={()=>pending.current.fail?.(new Error("测试保存失败"))}>保存失败</button>
    <button onClick={()=>pending.current.put?.(status("reads"))}>迟到 A 保存</button>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);

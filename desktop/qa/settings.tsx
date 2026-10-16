// Isolated original settings view; actions do not persist configuration.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsModal } from "../src/components/SettingsModal";
import "../src/App.css";
const noop = () => {};
function Fixture() {
  const [base, setBase] = useState("http://127.0.0.1:11500/v1");
  const [model, setModel] = useState("Qwen3.6-35B-A3B-4bit");
  const [key, setKey] = useState("");
  const [fast, setFast] = useState(""); const [strong, setStrong] = useState("");
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [trust, setTrust] = useState<import("../src/types").TrustStatus>({level:"ask",effective:"ask",ceiling:"reads",levels:["ask","reads"],capability_profile:"external",workspace_scope:"general",local_file_reads:true,supervised:true});
  return <><output id="qa-note">{note}</output><SettingsModal activeScope="general" connection="connected" connectionText="已连接" connectionNote="隔离展示" runtimeStarting={false} projectSwitching={false} processStatus={{running:true,message:"隔离展示"}} recoveryRecord={null}
  llmProfile={{configured:true,baseUrl:"http://127.0.0.1:11500/v1",model:"Qwen3.6-35B-A3B-4bit",provider:"local",requiresKey:false,contextWindow:262144,contextWindowSource:"service",models:["Qwen3.6-35B-A3B-4bit"]}}
  llmBaseInput={base} llmModelInput={model} llmKeyInput={key} llmFastInput={fast} llmStrongInput={strong} llmRolesInput={roles} onLlmRolesChange={setRoles} llmProfileBusy={false} repoRoot="" baseUrl="http://127.0.0.1:8080" token="" modelPicks={null} initialSection="model"
  onClose={()=>setNote("关闭设置")} onLlmBaseChange={setBase} onLlmModelChange={setModel} onLlmKeyChange={setKey} onLlmFastChange={setFast} onLlmStrongChange={setStrong} onRestartRuntime={async()=>false} onLlmProfileChange={noop} onModelPicksChange={noop} onSaveLlmProfile={()=>setNote("仅记录保存意图，未写盘或重启")} onClearLlmProfile={()=>setNote("仅记录清除意图")}
  onChooseRepo={noop} onDismissRecovery={noop} onRestoreRecovery={noop} onBaseUrlChange={noop} onTokenChange={noop} onStopRuntime={noop} onConnectExisting={noop} onStartRuntime={noop} onSwitchScope={noop} trust={trust} trustBusy={false} onTrustChange={(level)=>{setTrust({...trust,level,effective:level === "full" ? "reads" : level});setNote("隔离档位："+level)}}/></>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);

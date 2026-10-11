import {renderToStaticMarkup} from "react-dom/server";
import {expect,it} from "vitest";
import {ManagementNav} from "./ManagementNav";
import {WorkbenchNav} from "./WorkbenchNav";
import {isManagementTab,type InspectorTab} from "../lib/inspector";
it("routes all cross-session management to the application page",()=>{
 for(const tab of ['inbox','tasks','goals','decisions'] as InspectorTab[])expect(isManagementTab(tab)).toBe(true);
 for(const tab of ['info','preview','files','diff','runs','project'] as InspectorTab[])expect(isManagementTab(tab)).toBe(false);
});
it("keeps board and task management out of conversation navigation",()=>{
 const local=renderToStaticMarkup(<WorkbenchNav current="info" label="当前执行" busy tabClass={()=>''} onSelect={()=>{}}><button>预览</button></WorkbenchNav>);
 expect(local).toContain('任务步骤');for(const text of ['看板','目标','后台任务','待处理'])expect(local).not.toContain(text);
 const global=renderToStaticMarkup(<ManagementNav current="inbox" onSelect={()=>{}}/>);
 for(const text of ['看板','任务','目标','待处理'])expect(global).toContain(text);
 expect(global).not.toContain('当前执行');
});

import {renderToStaticMarkup} from "react-dom/server";
import {expect,it} from "vitest";
import {ManagementNav} from "./ManagementNav";
import {WorkbenchLauncher,WorkbenchTabs,type WorkbenchTool} from "./WorkbenchTabs";
import {isManagementTab,type InspectorTab} from "../lib/inspector";
it("routes all cross-session management to the application page",()=>{
 for(const tab of ['inbox','tasks','goals','decisions'] as InspectorTab[])expect(isManagementTab(tab)).toBe(true);
 for(const tab of ['info','preview','files','diff','runs','project'] as InspectorTab[])expect(isManagementTab(tab)).toBe(false);
});
it("keeps board and task management out of conversation navigation",()=>{
 const tools:WorkbenchTool[]=[{id:'info',label:'任务步骤',icon:null,open(){}},{id:'preview',label:'预览',icon:null,open(){}}];
 const local=renderToStaticMarkup(<><WorkbenchTabs tools={tools} tabs={['info']} current="info" launcher={false} onShowLauncher={()=>{}} onCloseTab={()=>{}} onClosePanel={()=>{}}/><WorkbenchLauncher tools={tools}/></>);
 expect(local).toContain('任务步骤');expect(local).toContain('关闭任务步骤');for(const text of ['看板','目标','后台任务','待处理'])expect(local).not.toContain(text);
 const global=renderToStaticMarkup(<ManagementNav current="inbox" onSelect={()=>{}}/>);
 for(const text of ['看板','后台任务','待处理'])expect(global).toContain(text);
 expect(global).not.toContain('目标');                                   // 目标走侧栏自己的入口
 expect(global).toContain('aria-current="page" class="active">看板');
});

import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ResidentAccountStatus } from "./ResidentAccountCard";
const value = {projectId:"host",name:"常驻助手",baseUrl:"https://resident.test",instanceId:"a".repeat(32),revision:1,registered:true,belongsToCurrent:true,mobileUrl:"https://phone.test/mobile"};
const render=(state: Parameters<typeof ResidentAccountStatus>[0]["state"])=>renderToStaticMarkup(<ResidentAccountStatus state={state} onRefresh={()=>{}} onUpdate={()=>{}}/>);
it("distinguishes association from a configured phone entry and keeps explicit revocation",()=>{
  const html=render({busy:false,error:"",value});
  expect(html).toContain("已关联当前账号");expect(html).toContain("解除关联");expect(html).toContain("打开手机入口");expect(html).toContain("仍需登记");
  const missing=render({busy:false,error:"",value:{...value,mobileUrl:null}});
  expect(missing).toContain("尚未配置手机访问地址");expect(missing).not.toContain("打开手机入口");
});
it("another account cannot be silently overwritten; unavailable state has no mutation buttons",()=>{
  const other=render({busy:false,error:"",value:{...value,belongsToCurrent:false}});
  expect(other).toContain("已关联其他账号");expect(other).not.toContain("打开手机入口");expect(other).not.toContain(">关联当前账号<");
  const unavailable=render({busy:false,error:"状态待核对",value:null});expect(unavailable).toContain("状态待核对");expect(unavailable).not.toContain(">解除关联<");
});

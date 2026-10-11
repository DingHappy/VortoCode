import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { TrustStatus } from "../types";
import { ConfirmationTrust } from "./ConfirmationTrust";
const base: TrustStatus = {level:"ask",effective:"ask",ceiling:"reads",levels:["ask","reads"],capability_profile:"local",workspace_scope:"project",supervised:true};
const render = (trust:TrustStatus|null, busy=false, tainted=false, connected=true) => renderToStaticMarkup(
  <ConfirmationTrust trust={trust} busy={busy} connected={connected} tainted={tainted} onChange={()=>{throw Error("must not auto-save")}}/>);
it("offers only higher levels within the ceiling, with independent approval",()=>{
  const html=render(base);
  expect(html).toContain("保存授权级别");expect(html).toContain('value="reads"');
  expect(html).not.toContain('value="full"');expect(html).toContain("本次仍需点击允许");
  expect(html).toContain("写入、执行和外发始终逐次审批");
});
it("current maximum and missing status never offer a false upgrade",()=>{
  expect(render({...base,level:"reads",effective:"reads"})).not.toContain("保存授权级别");
  expect(render(null)).toBe("");
});
it("pending and disconnected saves are disabled; taint cannot be waived",()=>{
  expect(render(base,true)).toContain("正在保存");expect(render(base,true)).toContain("disabled");
  expect(render(base,false,false,false)).toContain("disabled");
  expect(render(base,false,true)).toContain("提升档位也不会免确认");
});

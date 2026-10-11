import { describe, expect, it } from "vitest";

import { confirmAction } from "./confirm";

describe("confirmAction", () => {
  it("passes the message to the native confirm command and allows only an explicit true", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const allow = await confirmAction("撤销 a.py 的本地修改？", async (command, args) => {
      calls.push([command, args]);
      return true;
    });
    expect(allow).toBe(true);
    expect(calls).toEqual([["confirm_action", { message: "撤销 a.py 的本地修改？" }]]);
  });

  it("treats cancel as a refusal", async () => {
    expect(await confirmAction("删除？", async () => false)).toBe(false);
  });

  it("fails closed on anything other than a boolean true", async () => {
    // 原 bug 就是把"真值"当成"同意"；这里只认严格的 true。
    for (const value of ["true", 1, {}, undefined, null]) {
      expect(await confirmAction("删除？", async () => value)).toBe(false);
    }
  });

  it("fails closed when the native dialog cannot be shown", async () => {
    expect(await confirmAction("删除？", async () => {
      throw new Error("command confirm_action not found");
    })).toBe(false);
  });
});

import { createConfirmationReply, type ConfirmationReplyState } from "./confirm";
function replyFixture() {
  let view={client:{} as object|null,scope:"one",id:"c1",state:"idle" as ConfirmationReplyState};
  let resolve!:()=>void, reject!:(error:Error)=>void;
  const pending=new Promise<void>((yes,no)=>{resolve=yes;reject=no;});
  const calls:boolean[]=[];const states:ConfirmationReplyState[]=[];
  const reply=createConfirmationReply(()=>view,async(_client,_id,ok)=>{calls.push(ok);await pending;},(_id,state)=>{view={...view,state};states.push(state);});
  return {reply,calls,states,resolve,reject,view:()=>view,set:(change:Partial<typeof view>)=>{view={...view,...change};}};
}
it("holds the approval until a server receipt and prevents duplicate submissions",async()=>{
  const f=replyFixture();const p=f.reply.submit("c1",true);
  await f.reply.submit("c1",false);expect(f.calls).toEqual([true]);expect(f.view().state).toBe("sending");
  f.resolve();await p;expect(f.view().state).toBe("awaiting");
  await f.reply.submit("c1",true);expect(f.calls).toHaveLength(1);
});
it("keeps uncertain failures until an explicit read-only check permits retry",async()=>{
  const f=replyFixture();const p=f.reply.submit("c1",true);f.reject(Error("lost"));await p;
  expect(f.view().state).toBe("failed");await f.reply.submit("c1",true);expect(f.calls).toHaveLength(1);
  f.reply.close("c1");f.set({state:"idle"});await f.reply.submit("c1",false);expect(f.calls).toEqual([true,false]);
});
it.each(["receipt","client","scope","id"])("ignores late transport completions after %s changes",async(change)=>{
  const f=replyFixture();const p=f.reply.submit("c1",true);
  if(change==="receipt")f.reply.close("c1");else if(change==="client")f.set({client:{}});else if(change==="scope")f.set({scope:"two"});else f.set({id:"c2"});
  f.resolve();await p;expect(f.states).toEqual(["sending"]);
});

import { expect, it, vi } from "vitest";
import { parseResidentAccount, residentAccountController } from "./residentAccount";
import type { ResidentAccountState } from "./residentAccount";
const value = { projectId: "resident-1", name: "助手", baseUrl: "https://resident.test", instanceId: "a".repeat(32), revision: 1, registered: true, belongsToCurrent: true, mobileUrl: "https://phone.test/mobile" };
const deferred = <T>() => { let resolve!: (value: T) => void; let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
it("rejects another host, unsafe links and malformed identity without exposing extra fields", () => {
  for (const bad of [{...value,projectId:"other"}, {...value,revision:1.5}, {...value,instanceId:null},
    {...value,mobileUrl:"javascript:alert(1)"}, {...value,baseUrl:"http://192.168.1.97"}, {...value,mobileUrl:"https://u:p@phone.test/mobile"}]) {
    expect(()=>parseResidentAccount(bad,value.projectId)).toThrow();
  }
  expect(parseResidentAccount({...value,access_token:"private",owner:{subject:"12"}},value.projectId)).toEqual(value);
});
it("account change and disposal discard late reads and write receipts", async () => {
  const late = deferred<unknown>(), update = deferred<unknown>(); let reads=0; const states: ResidentAccountState[]=[];
  const controller = residentAccountController(value.projectId,{read:()=>++reads===1?late.promise:Promise.resolve(value),update:()=>update.promise,publish:state=>states.push(state),error:()=>"失败"});
  const first=controller.refresh(); controller.invalidate(); await controller.refresh();
  late.resolve({...value,belongsToCurrent:false}); await first;
  expect(states[states.length - 1]?.value?.belongsToCurrent).toBe(true);
  const writing=controller.update(false); controller.invalidate(); await controller.refresh();
  update.resolve({...value,registered:false,belongsToCurrent:null}); await writing;
  expect(states[states.length - 1]?.value?.registered).toBe(true);
  controller.dispose(); const count=states.length; await controller.refresh(); expect(states).toHaveLength(count);
});
it("lost mutation response erases actionability and never retries the mutation", async () => {
  const states: ResidentAccountState[]=[]; const update=vi.fn().mockRejectedValue(new Error("lost"));
  const controller=residentAccountController(value.projectId,{read:async()=>value,update,publish:state=>states.push(state),error:()=>"请刷新"});
  await controller.refresh(); await controller.update(false); await controller.update(false);
  expect(update).toHaveBeenCalledTimes(1); expect(states[states.length - 1]).toEqual({busy:false,value:null,error:"请刷新"});
  await controller.refresh(); expect(states[states.length - 1]?.value).toEqual(value);
});

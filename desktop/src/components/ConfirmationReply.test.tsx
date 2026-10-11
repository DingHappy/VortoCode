import {renderToStaticMarkup} from "react-dom/server";
import {expect,it} from "vitest";
import {ConfirmationReply} from "./ConfirmationReply";
it.each(["sending","awaiting","failed"] as const)("locks both approval choices while %s without removing the card",state=>{
 const html=renderToStaticMarkup(<ConfirmationReply state={state} connected onCheck={()=>{}} onAnswer={()=>{}}/>);
 expect(html.match(/disabled=""/g)).toHaveLength(2);
 expect(html.includes("核对状态")).toBe(state!=="sending");
});
it("only enables a new choice after an explicit idle state",()=>{
 const html=renderToStaticMarkup(<ConfirmationReply state="idle" connected onCheck={()=>{}} onAnswer={()=>{}}/>);
 expect(html).not.toContain('disabled=""');expect(html).toContain("允许一次");
});

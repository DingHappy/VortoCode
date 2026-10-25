import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { activityRecordSteps } from "./ActivityRecordDialog";
import { ActivityDetailDialog } from "./ActivityDetailDialog";

it("uses actual recorded tool outcomes and never completes a running or unknown event because a reply exists", () => {
  const steps=activityRecordSteps({sid:"s",rid:"r",request:"核对",reply:"已回复",has_reply:true,truncated:false,source:"session_archive",
    events:[{type:"agent_tool",id:"a",rid:"r",name:"read_file",status:"running"},
      {type:"agent_tool",id:"a",rid:"r",name:"read_file",status:"completed",result:"真实输出"},
      {type:"agent_tool",id:"b",rid:"r",status:"future"}]});
  expect(steps).toHaveLength(3);
  expect(steps[1].status).toBe("done"); expect(steps[1].detail).toBe("真实输出");
  expect(steps[2].status).toBe("unknown");
  expect(activityRecordSteps(null)).toEqual([]);
});
it("keeps the original approval controls in the result pane and escapes a record title", () => {
  const html=renderToStaticMarkup(<ActivityDetailDialog title="<script>bad</script>" statusLabel="待审阅" steps={[]}
    onClose={()=>{}}><button>批准原动作</button></ActivityDetailDialog>);
  expect(html).toContain('aria-label="执行步骤"'); expect(html).toContain('aria-label="结果与后续"');
  expect(html).toContain("批准原动作"); expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>");
});

it("labels archived running, unknown and timeout states without inventing a live execution or completion", () => {
  const steps = activityRecordSteps({ sid: "s", rid: "r", request: "核对", reply: "有最终回复", has_reply: true,
    source: "session_archive", truncated: false, events: [
      { type: "agent_tool", id: "missing", rid: "r", name: "read_file" },
      { type: "agent_tool", id: "active", rid: "r", name: "run_command", status: "running" },
      { type: "agent_tool", id: "timeout", rid: "r", name: "run_command", status: "timed_out", duration_ms: 1250,
        recorded_at: "2026-10-24T09:00:00Z" },
      { type: "agent_hook", id: "blocked", rid: "r", name: "检查", status: "blocked", event: "PreToolUse", tool: "run_command" },
    ] });
  expect(steps[1].status).toBe("unknown"); expect(steps[1].detail).toContain("不能据此判断");
  expect(steps[2]).toMatchObject({ status: "running", recorded: true });
  expect(steps[2].detail).toContain("保存时");
  expect(steps[3]).toMatchObject({ status: "failed", statusLabel: "超时", durationMs: 1250, recordedAt: "2026-10-24T09:00:00Z" });
  expect(steps[4]).toMatchObject({ status: "failed", statusLabel: "受阻" });
  expect(steps[4].detail).toContain("事件：PreToolUse\n工具：run_command");
  const html = renderToStaticMarkup(<ActivityDetailDialog title="记录" steps={steps} onClose={() => {}}>结果</ActivityDetailDialog>);
  expect(html).toContain("记录为执行中"); expect(html).toContain("待核对"); expect(html).toContain("超时");
  expect(html).not.toContain("session-step-spin");
});

it("retains spinners for explicitly live steps and does not assign old timestamps to sparse later events", () => {
  const html = renderToStaticMarkup(<ActivityDetailDialog title="实时任务" onClose={() => {}}
    steps={[{ id: "live", title: "实时步骤", status: "running" }]}>结果</ActivityDetailDialog>);
  expect(html).toContain("session-step-spin");
  const steps = activityRecordSteps({ sid: "s", rid: "r", request: "核对", reply: "", has_reply: false,
    source: "session_archive", truncated: false, events: [
      { type: "agent_tool", id: "a", rid: "r", status: "running", recorded_at: "2026-10-24T09:00:00Z" },
      { type: "agent_tool", id: "a", rid: "r", status: "completed" },
    ] });
  expect(steps[1].status).toBe("done"); expect(steps[1].recordedAt).toBeUndefined();
});

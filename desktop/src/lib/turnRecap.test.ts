import { describe, expect, it } from "vitest";

import type { TurnActivity } from "../types";
import { recapPhrases, turnRecap } from "./turnRecap";

let n = 0;
const tool = (name: string, patch: Partial<TurnActivity> = {}): TurnActivity =>
  ({ id: `t${n++}`, kind: "tool", status: "completed", label: name, name, ...patch });

describe("turnRecap", () => {
  it("summarises what a round actually did from tool calls", () => {
    const recap = turnRecap([
      { id: "p", kind: "phase", status: "completed", label: "思考", phase: "thinking" },
      tool("read_file", { args: { path: "a.py" } }),
      tool("edit_file", { args: { path: "a.py" } }),
      tool("edit_file", { args: { path: "a.py" } }),
      tool("write_file", { args: { path: "tests/test_a.py" } }),
      tool("edit_file", { status: "failed", args: { path: "b.py" } }),      // 没改成的不算
      tool("run_tests", { result: "测试通过 ✓（pytest -q）。输出尾部：\n3 passed, 1 xfailed" }),
      tool("run_tests", { result: "测试未过 ✗（pytest -q）。输出尾部：\n1 failed, 2 passed" }),
      tool("run_command", { args: { cmd: "ls" } }),
      tool("dev_isolated"),
    ]);
    expect(recap.filesChanged).toEqual(["a.py", "tests/test_a.py"]);
    expect(recap.tests).toEqual({ runs: 2, failed: 1 });
    expect(recap.failures).toBe(1);
    expect(recapPhrases(recap)).toEqual(["改了 2 个文件", "跑测试 2 次（1 次没过）", "隔离开发 1 次", "执行命令 1 次"]);
  });

  it("falls back to a plain tool count when nothing is recognised", () => {
    expect(recapPhrases(turnRecap([tool("grep"), tool("list_files")]))).toEqual(["2 个工具"]);
    expect(recapPhrases(turnRecap([]))).toEqual([]);
  });
});

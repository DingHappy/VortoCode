// 回合回顾：把一轮里的工具调用压成一行「这一轮做了什么」，折叠的时间线标题直接显示它。
// 只从真实的工具调用记录里算（不调模型、编不出来）；不认识的工具只计入总数，不猜它做了什么。
import type { TurnActivity } from "../types";

const WRITE_TOOLS = new Set(["edit_file", "write_file"]);
const DEV_TOOLS = new Set(["dev_isolated", "dev_parallel", "dev_auto", "dev_resume"]);
const FAILED = new Set(["failed", "blocked", "cancelled", "timed_out"]);

export interface TurnRecap {
  filesChanged: string[];
  tests: { runs: number; failed: number };
  commands: number;
  devRuns: number;
  failures: number;
  toolCount: number;
}

export function turnRecap(items: TurnActivity[]): TurnRecap {
  const tools = items.filter((item) => item.kind === "tool");
  const files = new Set<string>();
  let testRuns = 0;
  let testFailed = 0;
  let commands = 0;
  let devRuns = 0;
  for (const tool of tools) {
    const name = tool.name ?? "";
    const failed = FAILED.has(tool.status);
    if (WRITE_TOOLS.has(name) && !failed && typeof tool.args?.path === "string" && tool.args.path) files.add(tool.args.path);
    if (name === "run_tests") {
      testRuns += 1;
      // run_tests 跑红是「调用成功、结果不过」：认它固定的「测试未过」开头，别去扫输出
      // （通过的输出里也可能有 1 xfailed 这种字样）
      if (failed || (tool.result ?? "").startsWith("测试未过")) testFailed += 1;
    }
    if (name === "run_command") commands += 1;
    if (DEV_TOOLS.has(name)) devRuns += 1;
  }
  return {
    filesChanged: [...files], tests: { runs: testRuns, failed: testFailed }, commands, devRuns,
    failures: tools.filter((tool) => FAILED.has(tool.status)).length, toolCount: tools.length,
  };
}

/** 折叠标题里的短语，按「人最关心」排序；什么都没认出来就只给工具总数。 */
export function recapPhrases(recap: TurnRecap): string[] {
  const phrases: string[] = [];
  if (recap.filesChanged.length) phrases.push(`改了 ${recap.filesChanged.length} 个文件`);
  if (recap.tests.runs) phrases.push(`跑测试 ${recap.tests.runs} 次${recap.tests.failed ? `（${recap.tests.failed} 次没过）` : ""}`);
  if (recap.devRuns) phrases.push(`隔离开发 ${recap.devRuns} 次`);
  if (recap.commands) phrases.push(`执行命令 ${recap.commands} 次`);
  if (!phrases.length && recap.toolCount) phrases.push(`${recap.toolCount} 个工具`);
  return phrases;
}

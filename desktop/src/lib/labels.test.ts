/**
 * lib/labels.ts 的纯逻辑单测（B8-④b S1 拆分安全网）。
 * 代表性覆盖 label 映射 / 数值格式化 / 相对时间；不联网、离线确定性。
 */
import { describe, expect, it } from "vitest";

import {
  CONTEXT_CHIP_MIN_PCT,
  compactAuditData,
  compactSessionCwd,
  decisionKindLabel,
  extensionKindLabel,
  fileGlyph,
  formatActivityDuration,
  formatBytes,
  formatFileSize,
  formatRelativeTime,
  formatTokenCount,
  hookCapabilityLabel,
  runKindLabel,
  sessionContextTone,
  shouldShowContextChip,
  statusLabel,
} from "./labels";

describe("statusLabel", () => {
  it("已知状态给中文，未知状态原样透传", () => {
    expect(statusLabel("running")).toBe("执行中");
    expect(statusLabel("achieved")).toBe("已达成");
    expect(statusLabel("some_unknown")).toBe("some_unknown");
  });
});

describe("枚举 label 映射", () => {
  it("runKindLabel / decisionKindLabel / extensionKindLabel", () => {
    expect(runKindLabel("test")).toBe("测试");
    expect(runKindLabel("preview")).toBe("预览");
    expect(decisionKindLabel("pr_check")).toBe("CI");
    expect(decisionKindLabel("hook")).toBe("Hook");
    expect(extensionKindLabel("mcp")).toBe("MCP");
  });

  it("hookCapabilityLabel 已知映射 + 未知透传", () => {
    expect(hookCapabilityLabel("run_command")).toBe("运行命令");
    expect(hookCapabilityLabel("mystery")).toBe("mystery");
  });
});

describe("数值格式化", () => {
  it("formatBytes 按 B/KiB/MiB 分档，负数归零", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MiB");
    expect(formatBytes(-1)).toBe("0 B");
    expect(formatBytes(undefined)).toBe("0 B");
  });

  it("formatFileSize 小于 10KiB 保一位小数、更大取整", () => {
    expect(formatFileSize(100)).toBe("100 B");
    expect(formatFileSize(2048)).toBe("2.0 KiB");
    expect(formatFileSize(50 * 1024)).toBe("50 KiB");
  });

  it("formatTokenCount 按 K/M 缩写", () => {
    expect(formatTokenCount(500)).toBe("500");
    expect(formatTokenCount(1500)).toBe("1.5K");
    expect(formatTokenCount(2_000)).toBe("2K");
    expect(formatTokenCount(3_400_000)).toBe("3.4M");
  });

  it("formatActivityDuration 分档", () => {
    expect(formatActivityDuration(undefined)).toBe("");
    expect(formatActivityDuration(500)).toBe("不到 1 秒");
    expect(formatActivityDuration(2500)).toBe("2.5 秒");
    expect(formatActivityDuration(30_000)).toBe("30 秒");
    expect(formatActivityDuration(90_000)).toBe("1 分 30 秒");
    expect(formatActivityDuration(120_000)).toBe("2 分钟");
  });
});

describe("fileGlyph", () => {
  it("按扩展名给字形", () => {
    expect(fileGlyph("a/b/App.tsx")).toBe("TS");
    expect(fileGlyph("main.py")).toBe("PY");
    expect(fileGlyph("lib.rs")).toBe("RS");
    expect(fileGlyph("README.md")).toBe("MD");
    expect(fileGlyph("pkg.json")).toBe("{}");
    expect(fileGlyph("theme.scss")).toBe("#");
    expect(fileGlyph("noext")).toBe("·");
  });
});

describe("sessionContextTone", () => {
  it("按占比给色调阈值", () => {
    expect(sessionContextTone(95)).toBe("danger");
    expect(sessionContextTone(75)).toBe("warning");
    expect(sessionContextTone(10)).toBe("normal");
    expect(sessionContextTone(undefined)).toBe("normal");
  });
});

describe("compactSessionCwd", () => {
  it("只留末两段路径", () => {
    expect(compactSessionCwd("/Users/x/personal/VortoCode")).toBe("personal/VortoCode");
    expect(compactSessionCwd("C:\\\\a\\\\b\\\\c")).toBe("b/c");
    expect(compactSessionCwd(undefined)).toBe("");
  });
});

describe("formatRelativeTime", () => {
  it("落在最近的相对档位（以当前时钟为基准）", () => {
    const now = Date.now();
    expect(formatRelativeTime(0)).toBe("");
    expect(formatRelativeTime(undefined)).toBe("");
    expect(formatRelativeTime((now - 30_000) / 1000)).toBe("刚刚");
    expect(formatRelativeTime((now - 5 * 60_000) / 1000)).toBe("5 分钟前");
    expect(formatRelativeTime((now - 3 * 3_600_000) / 1000)).toBe("3 小时前");
  });
});

describe("compactAuditData", () => {
  it("空对象/未定义给空串", () => {
    expect(compactAuditData(undefined)).toBe("");
    expect(compactAuditData({})).toBe("");
  });

  it("正常对象序列化，超 260 字截断加省略号", () => {
    expect(compactAuditData({ a: 1 })).toBe('{"a":1}');
    const big = compactAuditData({ blob: "x".repeat(400) });
    expect(big.endsWith("…")).toBe(true);
    expect(big.length).toBe(261);
  });
});

describe("侧边栏 chip 的降噪", () => {
  it("托管工作区不出目录 chip", () => {
    // 真机冒烟：General 会话每一行都挂着 `workspaces/general`，同一句话说 N 遍，
    // 而且那是 Desktop 自己的脚手架路径，用户既没选过也管不着。
    const base = "/Users/x/Library/Application Support/com.vorto.vortocode/workspaces";
    expect(compactSessionCwd(`${base}/general`)).toBe("");
    expect(compactSessionCwd(`${base}/scratch/ab12`)).toBe("");
    expect(compactSessionCwd("C:\\Users\\x\\AppData\\vortocode\\workspaces\\general")).toBe("");
  });

  it("用户自己的项目目录照常显示——那时候它才真的在区分这一行在哪儿跑", () => {
    expect(compactSessionCwd("/Users/x/code/VortoCode")).toBe("code/VortoCode");
    expect(compactSessionCwd("/Users/x/my-workspaces/general-ledger")).toBe("my-workspaces/general-ledger");
  });

  it("空值不报错", () => {
    expect(compactSessionCwd(undefined)).toBe("");
    expect(compactSessionCwd("")).toBe("");
  });

  it("上下文占比低到不用管就不出 chip", () => {
    expect(shouldShowContextChip(0.2)).toBe(false);
    expect(shouldShowContextChip(24.9)).toBe(false);
    expect(shouldShowContextChip(CONTEXT_CHIP_MIN_PCT)).toBe(true);
    expect(shouldShowContextChip(91)).toBe(true);
    expect(shouldShowContextChip(undefined)).toBe(false);
  });
});

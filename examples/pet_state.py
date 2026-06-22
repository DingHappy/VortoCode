#!/usr/bin/env python3
"""桌宠/状态消费者的「胶水」：被 VortoCode 的 hook 调用，把 agent 动作映射成一个状态文件。

VortoCode 的 hook（CommandHook）会把事件 JSON 从 stdin 喂给本脚本（含 event_type、data.tool 等）。
本脚本据此算出一个简单状态（idle/working/tool/error/done），写到 .vortocode/pet_state.json。
任何「桌宠」——网页、菜单栏小应用，乃至将来单独立项的原生悬浮桌宠——只要轮询/监听这个文件即可，
完全不依赖网页 WS，且 TUI/CLI/Web 三端通用（hook 在主 agent loop 里触发，与 UI 无关）。

用法：见同目录 hooks.pet.yaml —— 复制到 .vortocode/hooks.yaml 即生效。
"""

import json
import sys
from pathlib import Path

# event_type → (状态, 表情)；工具事件再带上工具名当 detail
_MAP = {
    "agent_start": ("working", "🙂"),
    "pre_tool_use": ("tool", "🔧"),
    "post_tool_use": ("tool", "🔧"),
    "tool_error": ("error", "😵"),
    "agent_end": ("done", "✅"),
}


def main() -> None:
    try:
        evt = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        evt = {}
    et = evt.get("event_type", "")
    state, emoji = _MAP.get(et, ("idle", "😴"))
    tool = (evt.get("data") or {}).get("tool") or ""
    out = {"state": state, "emoji": emoji, "detail": tool, "event": et}
    # 写到仓库的 .vortocode/pet_state.json（gitignored 运行时目录）
    try:
        d = Path(".vortocode")
        d.mkdir(exist_ok=True)
        (d / "pet_state.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    # 同时打印（hook 协议：stdout 是 JSON 则被解析；这里只为可观测，无需 message）
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()

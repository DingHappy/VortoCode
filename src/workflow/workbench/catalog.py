"""精选目录：审核过的 MCP 服务和技能，一键装进当前范围。

不做开放市场：MCP 服务能拿到你交给它的问题和数据、技能会进系统提示，来源要可控。这里的条目都是：
- MCP：**免凭据的公开 HTTP 服务**（当前安全策略只接这类），2026-10-18 实测能连上、能列出工具；
  装上就是在 ``config/mcp.yaml`` 加一条，与插件页手动添加同一条路（同样的校验、同样的逐行写入）。
- 技能：本仓库自己写的通用流程指令，装上就是写一份 SKILL.md（只在本机或随仓库，二选一）。

要加条目：MCP 先用插件页「测试连接」确认能连上、确认不需要凭据，再写进来并在 PR 里附测试结果。
"""
from __future__ import annotations

from typing import Any, Dict, List

MCP_SERVERS: List[Dict[str, str]] = [
    {"id": "deepwiki", "name": "DeepWiki", "url": "https://mcp.deepwiki.com/mcp",
     "homepage": "https://deepwiki.com",
     "description": "就任意公开 GitHub 仓库的文档和结构提问，适合读陌生代码库。",
     "privacy": "你的问题和仓库名会发给 DeepWiki（Cognition）。"},
    {"id": "context7", "name": "Context7", "url": "https://mcp.context7.com/mcp",
     "homepage": "https://context7.com",
     "description": "按库名和版本查最新的官方文档与示例，避免模型用过时的 API。",
     "privacy": "你查询的库名和问题会发给 Context7（Upstash）。"},
    {"id": "microsoft-learn", "name": "Microsoft Learn", "url": "https://learn.microsoft.com/api/mcp",
     "homepage": "https://learn.microsoft.com",
     "description": "搜索和读取微软官方文档与代码示例（Azure、.NET、TypeScript 等）。",
     "privacy": "你的搜索词会发给 Microsoft。"},
    {"id": "cloudflare-docs", "name": "Cloudflare Docs", "url": "https://docs.mcp.cloudflare.com/mcp",
     "homepage": "https://developers.cloudflare.com",
     "description": "搜索 Cloudflare 开发者文档（Workers、Pages、R2 等）。",
     "privacy": "你的搜索词会发给 Cloudflare。"},
    {"id": "huggingface", "name": "Hugging Face", "url": "https://huggingface.co/mcp",
     "homepage": "https://huggingface.co",
     "description": "匿名搜索 Hugging Face 上的模型、数据集和 Space。",
     "privacy": "你的搜索词会发给 Hugging Face。"},
]

SKILLS: List[Dict[str, str]] = [
    {"id": "code-review", "name": "代码审查",
     "description": "审查一组改动：正确性、安全、可维护性，按严重程度给出可操作的意见。",
     "instructions": """审查指定的改动（分支、PR 或文件），只报真正值得改的问题。

1. 先弄清改动想做什么：读描述、看 diff 涉及哪些文件。
2. 逐项检查，按严重程度排序：
   - 正确性：边界条件、空值、并发、错误处理有没有吞掉异常；
   - 安全：输入校验、注入、路径穿越、凭据是否进了代码或日志；
   - 测试：新行为有没有测试；测试断的是行为还是实现细节；
   - 可维护性：命名、重复、和周围代码风格是否一致。
3. 每条意见写清：在哪（文件:行）、问题是什么、会导致什么后果、建议怎么改。
4. 不确定的地方明确说「不确定」，不要把猜测写成结论；没有问题就直说没有。"""},
    {"id": "bug-triage", "name": "排查 bug",
     "description": "复现 → 定位根因 → 最小修复 → 补回归测试，不靠猜。",
     "instructions": """排查用户描述的 bug，每一步都要有证据。

1. 复现：写出最小复现步骤或一个会失败的测试；复现不了就停下来，说明缺什么信息。
2. 定位：从失败点往回追，找到真正的根因，而不是症状出现的地方。读代码、加日志、二分都可以。
3. 修复：只改根因需要的最少代码；别顺手重构无关部分（发现了别的问题，单独记下来）。
4. 回归测试：把第 1 步的复现变成测试，确认修复前失败、修复后通过。
5. 汇报：根因、改了什么、怎么验证的、还有什么没覆盖到。"""},
    {"id": "write-tests", "name": "补测试",
     "description": "给指定模块补测试：断行为不断实现，覆盖边界和失败路径。",
     "instructions": """给指定的模块或函数补测试。

1. 先读被测代码和已有测试，沿用仓库现有的测试框架、目录和命名习惯。
2. 列出要覆盖的行为：正常路径、边界值、空输入、错误输入、失败路径。
3. 每个测试只断一件事，断言**可观察的行为**（返回值、副作用、抛出的错误），
   不要去检查源码里有没有某段字符串，也不要断言私有实现细节。
4. 测试必须离线、确定：不依赖网络、真实时间、随机数（需要时注入或固定）。
5. 跑一遍确认全部通过；如果发现被测代码本身有 bug，先如实报告，不要为了让测试变绿去改断言。"""},
    {"id": "release-notes", "name": "写发布说明",
     "description": "汇总一段时间内合并的改动，写给用户看的发布说明。",
     "instructions": """为指定的版本或时间段写发布说明。

1. 收集改动：用 git log（或合并的 PR 列表）列出这段时间的提交，按合并提交归组。
2. 分类：新功能、改进、修复、破坏性变更、内部改动（内部改动默认不写进对外说明）。
3. 每条用用户能懂的话写：改了什么、对他有什么影响；破坏性变更写清迁移方法。
4. 不确定某个改动影响的，查对应的 diff 或 PR 描述，不要凭标题猜。
5. 输出 Markdown，按「破坏性变更 → 新功能 → 改进 → 修复」排序。"""},
    {"id": "dependency-check", "name": "依赖检查",
     "description": "列出可升级和有已知风险的依赖，给建议，不动手改。",
     "instructions": """检查项目的依赖，**只出清单和建议，不修改任何文件**。

1. 找到依赖声明（pyproject.toml / requirements*.txt / package.json / go.mod / Cargo.toml 等）和锁文件。
2. 列出：当前版本、最新版本、是不是大版本跨越。
3. 标出值得优先处理的：有已知安全公告的、已停止维护的、落后多个大版本的。
4. 对每个建议升级的依赖，说明可能的破坏点（看它的变更日志），以及建议怎么验证。
5. 没法联网查最新版本时，如实说明，只基于本地信息给出能给的部分。"""},
]


def catalog(root: str) -> Dict[str, Any]:
    """目录 + 每一条在当前范围里装没装。"""
    from src.workflow.workbench.mcp_config import list_servers
    from src.workflow.workbench.project_skills import list_skills
    try:
        configured = {item["name"]: item for item in list_servers(root)["servers"]}
    except ValueError:
        configured = {}
    try:
        skills = list_skills(root)["items"]
    except ValueError:
        skills = []
    return {
        "mcp": [{**item, "installed": item["id"] in configured,
                 "status": configured.get(item["id"], {}).get("status", "")} for item in MCP_SERVERS],
        "skills": [{key: item[key] for key in ("id", "name", "description")}
                   | {"installed_scopes": sorted(s["scope"] for s in skills if s["id"] == item["id"])}
                   for item in SKILLS],
    }


def install_mcp(root: str, item_id: str) -> Dict[str, Any]:
    from src.workflow.workbench.mcp_config import McpConfigError, add_http_server
    item = next((entry for entry in MCP_SERVERS if entry["id"] == item_id), None)
    if item is None:
        raise McpConfigError(f"目录里没有「{item_id}」")
    return add_http_server(root, item["id"], item["url"])


def install_skill(root: str, item_id: str, scope: str) -> Dict[str, Any]:
    from src.workflow.workbench.project_skills import SkillError, save_skill
    item = next((entry for entry in SKILLS if entry["id"] == item_id), None)
    if item is None:
        raise SkillError(f"目录里没有「{item_id}」")
    return save_skill(root, scope, item["id"], {"name": item["name"], "description": item["description"],
                                               "instructions": item["instructions"], "revision": None})

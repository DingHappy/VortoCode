"""GitHub 集成 - Issue 处理和 PR 创建"""

import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IssueState(str, Enum):
    """Issue 状态"""
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class PRState(str, Enum):
    """PR 状态"""
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    ALL = "all"


class Issue(BaseModel):
    """GitHub Issue"""
    number: int
    title: str
    body: str = ""
    state: IssueState = IssueState.OPEN
    labels: List[str] = Field(default_factory=list)
    assignees: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    html_url: str = ""
    user: str = ""


class PullRequest(BaseModel):
    """GitHub Pull Request"""
    number: int
    title: str
    body: str = ""
    state: PRState = PRState.OPEN
    head_branch: str = ""
    base_branch: str = "main"
    labels: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    html_url: str = ""
    diff_url: str = ""


class PRCreateRequest(BaseModel):
    """PR 创建请求"""
    title: str
    body: str = ""
    head_branch: str
    base_branch: str = "main"
    labels: List[str] = Field(default_factory=list)
    draft: bool = False


class FileChange(BaseModel):
    """文件变更"""
    path: str
    content: str
    operation: str = "modified"  # added, modified, deleted


class GitHubClient:
    """GitHub API 客户端"""
    
    def __init__(self, token: str = None, repo: str = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.repo = repo or os.getenv("GITHUB_REPO")
        self.base_url = "https://api.github.com"
        self._session = None
    
    async def _get_session(self):
        """获取 HTTP 会话"""
        if not self._session:
            from src.utils.http import outbound_session
            self._session = outbound_session(
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "VortoCode"
                }
            )
        return self._session
    
    async def _request(
        self, 
        method: str, 
        endpoint: str, 
        data: Dict = None
    ) -> Dict[str, Any]:
        """发送请求"""
        import aiohttp
        
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with session.request(method, url, json=data) as response:
                if response.status >= 400:
                    error = await response.text()
                    raise Exception(f"GitHub API error: {response.status} - {error}")
                
                return await response.json()
        
        except aiohttp.ClientError as e:
            raise Exception(f"GitHub API request failed: {e}")
    
    async def get_issues(
        self, 
        state: IssueState = IssueState.OPEN,
        labels: List[str] = None,
        limit: int = 30
    ) -> List[Issue]:
        """获取 Issue 列表"""
        params = f"state={state.value}&per_page={limit}"
        if labels:
            params += f"&labels={','.join(labels)}"
        
        data = await self._request("GET", f"/repos/{self.repo}/issues?{params}")
        
        return [
            Issue(
                number=issue["number"],
                title=issue["title"],
                body=issue.get("body", ""),
                state=IssueState(issue["state"]),
                labels=[l["name"] for l in issue.get("labels", [])],
                assignees=[a["login"] for a in issue.get("assignees", [])],
                html_url=issue["html_url"],
                user=issue["user"]["login"]
            )
            for issue in data
        ]
    
    async def get_issue(self, issue_number: int) -> Issue:
        """获取单个 Issue"""
        data = await self._request("GET", f"/repos/{self.repo}/issues/{issue_number}")
        
        return Issue(
            number=data["number"],
            title=data["title"],
            body=data.get("body", ""),
            state=IssueState(data["state"]),
            labels=[l["name"] for l in data.get("labels", [])],
            assignees=[a["login"] for a in data.get("assignees", [])],
            html_url=data["html_url"],
            user=data["user"]["login"]
        )
    
    async def create_issue_comment(
        self, 
        issue_number: int, 
        body: str
    ) -> Dict[str, Any]:
        """创建 Issue 评论"""
        return await self._request(
            "POST",
            f"/repos/{self.repo}/issues/{issue_number}/comments",
            {"body": body}
        )
    
    async def update_issue(
        self, 
        issue_number: int, 
        state: IssueState = None,
        labels: List[str] = None
    ) -> Dict[str, Any]:
        """更新 Issue"""
        data = {}
        if state:
            data["state"] = state.value
        if labels:
            data["labels"] = labels
        
        return await self._request(
            "PATCH",
            f"/repos/{self.repo}/issues/{issue_number}",
            data
        )
    
    async def create_branch(self, branch_name: str, base_branch: str = "main") -> bool:
        """创建分支"""
        try:
            # 获取 base 分支的 SHA
            base_data = await self._request(
                "GET",
                f"/repos/{self.repo}/git/refs/heads/{base_branch}"
            )
            base_sha = base_data["object"]["sha"]
            
            # 创建新分支
            await self._request(
                "POST",
                f"/repos/{self.repo}/git/refs",
                {
                    "ref": f"refs/heads/{branch_name}",
                    "sha": base_sha
                }
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to create branch: {e}")
            return False
    
    async def commit_files(
        self,
        branch: str,
        message: str,
        files: List[FileChange]
    ) -> bool:
        """提交文件"""
        try:
            # 获取当前 tree
            ref_data = await self._request(
                "GET",
                f"/repos/{self.repo}/git/refs/heads/{branch}"
            )
            commit_sha = ref_data["object"]["sha"]
            
            # 获取 base tree
            commit_data = await self._request(
                "GET",
                f"/repos {self.repo}/git/commits/{commit_sha}"
            )
            base_tree_sha = commit_data["tree"]["sha"]
            
            # 创建 blob
            blobs = []
            for file in files:
                blob_data = await self._request(
                    "POST",
                    f"/repos/{self.repo}/git/blobs",
                    {
                        "content": file.content,
                        "encoding": "utf-8"
                    }
                )
                blobs.append({
                    "path": file.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_data["sha"]
                })
            
            # 创建 tree
            tree_data = await self._request(
                "POST",
                f"/repos/{self.repo}/git/trees",
                {
                    "base_tree": base_tree_sha,
                    "tree": blobs
                }
            )
            
            # 创建 commit
            new_commit = await self._request(
                "POST",
                f"/repos/{self.repo}/git/commits",
                {
                    "message": message,
                    "tree": tree_data["sha"],
                    "parents": [commit_sha]
                }
            )
            
            # 更新 ref
            await self._request(
                "PATCH",
                f"/repos/{self.repo}/git/refs/heads/{branch}",
                {"sha": new_commit["sha"]}
            )
            
            return True
        except Exception as e:
            logger.error(f"Failed to commit files: {e}")
            return False
    
    async def create_pull_request(self, request: PRCreateRequest) -> PullRequest:
        """创建 Pull Request"""
        data = {
            "title": request.title,
            "body": request.body,
            "head": request.head_branch,
            "base": request.base_branch,
            "draft": request.draft
        }
        
        if request.labels:
            data["labels"] = request.labels
        
        result = await self._request(
            "POST",
            f"/repos/{self.repo}/pulls",
            data
        )
        
        return PullRequest(
            number=result["number"],
            title=result["title"],
            body=result.get("body", ""),
            state=PRState(result["state"]),
            head_branch=result["head"]["ref"],
            base_branch=result["base"]["ref"],
            html_url=result["html_url"],
            diff_url=result["diff_url"]
        )
    
    async def get_pull_requests(
        self, 
        state: PRState = PRState.OPEN,
        limit: int = 30
    ) -> List[PullRequest]:
        """获取 PR 列表"""
        data = await self._request(
            "GET",
            f"/repos/{self.repo}/pulls?state={state.value}&per_page={limit}"
        )
        
        return [
            PullRequest(
                number=pr["number"],
                title=pr["title"],
                body=pr.get("body", ""),
                state=PRState(pr["state"]),
                head_branch=pr["head"]["ref"],
                base_branch=pr["base"]["ref"],
                html_url=pr["html_url"]
            )
            for pr in data
        ]
    
    async def close(self):
        """关闭客户端"""
        if self._session:
            await self._session.close()


class GitHubIntegration:
    """GitHub 集成"""
    
    def __init__(self, token: str = None, repo: str = None):
        self.client = GitHubClient(token, repo)
    
    async def handle_issue(self, issue_number: int) -> Dict[str, Any]:
        """处理 Issue"""
        # 获取 Issue
        issue = await self.client.get_issue(issue_number)
        
        # 分析 Issue
        analysis = self._analyze_issue(issue)
        
        return {
            "issue": issue.dict(),
            "analysis": analysis,
            "suggested_actions": self._suggest_actions(analysis)
        }
    
    def _analyze_issue(self, issue: Issue) -> Dict[str, Any]:
        """分析 Issue"""
        title_lower = issue.title.lower()
        body_lower = issue.body.lower() if issue.body else ""
        
        # 判断类型
        issue_type = "feature"
        if any(kw in title_lower for kw in ["bug", "fix", "error", "issue"]):
            issue_type = "bug"
        elif any(kw in title_lower for kw in ["improve", "enhance", "optimize"]):
            issue_type = "improvement"
        elif any(kw in title_lower for kw in ["docs", "document", "readme"]):
            issue_type = "documentation"
        
        # 判断优先级
        priority = "medium"
        if any(kw in title_lower for kw in ["urgent", "critical", "blocker"]):
            priority = "high"
        elif any(kw in title_lower for kw in ["minor", "low", "trivial"]):
            priority = "low"
        
        # 提取组件
        components = []
        component_keywords = {
            "frontend": ["ui", "frontend", "react", "vue", "css"],
            "backend": ["api", "backend", "server", "database"],
            "auth": ["auth", "login", "password", "token"],
            "test": ["test", "spec", "coverage"]
        }
        
        for component, keywords in component_keywords.items():
            if any(kw in title_lower or kw in body_lower for kw in keywords):
                components.append(component)
        
        return {
            "type": issue_type,
            "priority": priority,
            "components": components,
            "complexity": self._estimate_complexity(issue)
        }
    
    def _estimate_complexity(self, issue: Issue) -> str:
        """估算复杂度"""
        body_len = len(issue.body) if issue.body else 0
        
        if body_len > 1000:
            return "high"
        elif body_len > 200:
            return "medium"
        else:
            return "low"
    
    def _suggest_actions(self, analysis: Dict[str, Any]) -> List[str]:
        """建议操作"""
        actions = []
        
        if analysis["type"] == "bug":
            actions.append("reproduce_bug")
            actions.append("fix_bug")
            actions.append("write_test")
        elif analysis["type"] == "feature":
            actions.append("design_feature")
            actions.append("implement_feature")
            actions.append("write_tests")
            actions.append("update_docs")
        elif analysis["type"] == "documentation":
            actions.append("update_docs")
        
        return actions
    
    async def auto_fix_issue(
        self, 
        issue_number: int,
        branch_name: str = None
    ) -> Dict[str, Any]:
        """自动修复 Issue"""
        # 获取 Issue
        issue = await self.client.get_issue(issue_number)
        
        # 创建分支
        if not branch_name:
            branch_name = f"fix/issue-{issue_number}"
        
        await self.client.create_branch(branch_name)
        
        # 添加评论
        await self.client.create_issue_comment(
            issue_number,
            f"🤖 VortoCode is working on this issue...\n\n"
            f"Branch: `{branch_name}`\n"
            f"Status: In Progress"
        )
        
        return {
            "issue_number": issue_number,
            "branch": branch_name,
            "status": "in_progress"
        }
    
    async def create_fix_pr(
        self,
        issue_number: int,
        branch_name: str,
        changes: List[FileChange],
        pr_title: str = None,
        pr_body: str = None
    ) -> PullRequest:
        """创建修复 PR"""
        # 提交更改
        await self.client.commit_files(
            branch_name,
            f"fix: Fix issue #{issue_number}",
            changes
        )
        
        # 创建 PR
        if not pr_title:
            pr_title = f"fix: Fix issue #{issue_number}"
        
        if not pr_body:
            pr_body = f"Fixes #{issue_number}\n\nChanges:\n"
            for change in changes:
                pr_body += f"- {change.operation}: `{change.path}`\n"
        
        pr_request = PRCreateRequest(
            title=pr_title,
            body=pr_body,
            head_branch=branch_name,
            labels=["auto-fix"]
        )
        
        pr = await self.client.create_pull_request(pr_request)
        
        # 更新 Issue 评论
        await self.client.create_issue_comment(
            issue_number,
            f"✅ PR created: {pr.html_url}"
        )
        
        return pr

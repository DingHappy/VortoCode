"""github 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# GitHub 集成 API
@router.get("/api/github/issues")
async def get_github_issues(state: str = "open", limit: int = 30):
    """获取 GitHub Issues"""
    from src.github import GitHubIntegration, IssueState
    
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    
    if not token or not repo:
        return {"success": False, "error": "GitHub not configured"}
    
    try:
        github = GitHubIntegration(token, repo)
        issues = await github.client.get_issues(
            IssueState(state), limit=limit
        )
        return {"success": True, "issues": [i.model_dump() for i in issues]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/github/issues/{issue_number}")
async def get_github_issue(issue_number: int):
    """获取单个 Issue"""
    from src.github import GitHubIntegration
    
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    
    if not token or not repo:
        return {"success": False, "error": "GitHub not configured"}
    
    try:
        github = GitHubIntegration(token, repo)
        result = await github.handle_issue(issue_number)
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/github/issues/{issue_number}/fix")
async def auto_fix_issue(issue_number: int):
    """自动修复 Issue"""
    from src.github import GitHubIntegration
    
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPO")
    
    if not token or not repo:
        return {"success": False, "error": "GitHub not configured"}
    
    try:
        github = GitHubIntegration(token, repo)
        result = await github.auto_fix_issue(issue_number)
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}

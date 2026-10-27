"""Isolated, loopback-only browser acceptance. Never loads a live assistant root."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5216)
    parser.add_argument("--control-file", required=True)
    args = parser.parse_args()
    control = Path(args.control_file)
    with tempfile.TemporaryDirectory(prefix="vorto-mobile-browser-") as directory:
        original = Path.cwd()
        os.chdir(directory)
        try:
            token = secrets.token_urlsafe(32)
            os.environ["VORTOCODE_API_TOKEN"] = token
            os.environ["VORTOCODE_WORKSPACE_SCOPE"] = "general"
            from src.gateway.main_conversation import claim_main_chat
            from src.memory.assistant_profile import save_profile
            from src.workflow.workbench.personal_goals import PersonalGoals
            from src.web.server import app
            import uvicorn

            save_profile(directory, name="隔离验收助手")
            claim_main_chat(directory, "sid-mobile-acceptance")
            PersonalGoals(directory).create(
                {
                    "request_id": "mobile-reader-acceptance",
                    "objective": "验收手机入口的配对与只读链路",
                    "next_action": "核对两端代码后，确认这条目标可见",
                    "acceptance_criteria": ["读取有效", "撤销后拒绝下一次请求"],
                }
            )
            fd = os.open(control, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"token": token, "origin": f"http://127.0.0.1:{args.port}"}, handle)
            print(f"Isolated mobile reader: http://127.0.0.1:{args.port}/mobile", flush=True)
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=args.port,
                lifespan="off",
                access_log=False,
                log_level="error",
            )
        finally:
            control.unlink(missing_ok=True)
            os.chdir(original)


if __name__ == "__main__":
    main()

"""Static shells only. Every data/provisioning request uses its own auth route."""

from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
_DIRECTORY = Path(__file__).resolve().parents[3] / "web" / "mobile"
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
}


@router.get("/mobile")
@router.get("/mobile/")
@router.get("/mobile/manage")
def mobile_shell():
    path = _DIRECTORY / "index.html"
    if not path.is_file():
        raise HTTPException(503, "手机入口尚未构建", headers={"Cache-Control": "no-store"})
    return FileResponse(path, media_type="text/html", headers=_HEADERS)


@router.get("/static/mobile/{asset:path}")
def mobile_asset(asset: str):
    if asset != "vorto.png" and not re.fullmatch(r"assets/[A-Za-z0-9_-]+\.(js|css)", asset):
        raise HTTPException(404, "资源不存在")
    path = _DIRECTORY / asset
    if not path.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(path, headers=_HEADERS)

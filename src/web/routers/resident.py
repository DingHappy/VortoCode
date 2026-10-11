"""常驻电脑的项目登记表（只读）：Desktop「在哪做」与助理派活据此找到服务器上的项目运行时。"""
from __future__ import annotations

from fastapi import APIRouter

from src.workflow.workbench.resident_projects import load_resident_projects

router = APIRouter()


@router.get("/api/resident/projects")
async def resident_projects():
    return {"projects": load_resident_projects()}

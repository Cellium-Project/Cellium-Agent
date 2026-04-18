# -*- coding: utf-8 -*-
"""
Skill 管理 API — 前端 Skill 管理界面接口

提供运行时查看、安装、卸载、搜索 Skill 的能力。

接口列表:
  GET    /api/skills              → 已安装 Skill 列表
  GET    /api/skills/{name}       → 指定 Skill 详情
  POST   /api/skills/search       → 搜索 Skill
  POST   /api/skills/install      → 安装 Skill
  DELETE /api/skills/{name}       → 卸载 Skill
  POST   /api/skills/refresh-index → 手动刷新索引
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _get_skill_installer():
    from app.core.di.container import get_container
    container = get_container()
    return container.resolve("skill_installer")


def _get_skill_manager():
    from app.core.di.container import get_container
    container = get_container()
    return container.resolve("skill_manager")


class InstallRequest(BaseModel):
    name: Optional[str] = ""
    source_dir: Optional[str] = ""
    content: Optional[str] = ""
    source: Optional[str] = "local"


class SearchRequest(BaseModel):
    query: str


@router.get("")
async def list_skills(show_details: bool = False):
    """获取已安装 Skill 列表"""
    try:
        manager = _get_skill_manager()
        result = manager._cmd_list(show_details=show_details)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{name}")
async def get_skill(name: str):
    """获取指定 Skill 详情"""
    try:
        manager = _get_skill_manager()
        result = manager._cmd_get_info(name=name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search")
async def search_skills(body: SearchRequest):
    """搜索 Skill"""
    try:
        manager = _get_skill_manager()
        result = manager._cmd_search(query=body.query)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/install")
async def install_skill(body: InstallRequest):
    """安装 Skill"""
    try:
        installer = _get_skill_installer()
        result = installer._cmd_install(
            name=body.name or "",
            source=body.source or "local",
            content=body.content or "",
            source_dir=body.source_dir or "",
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{name}")
async def uninstall_skill(name: str):
    """卸载 Skill"""
    try:
        installer = _get_skill_installer()
        result = installer._cmd_uninstall(name=name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh-index")
async def refresh_index():
    """手动刷新 Skill 索引"""
    try:
        installer = _get_skill_installer()
        updated, removed = installer._scan_skills_dir()
        return {
            "success": True,
            "message": "索引已刷新",
            "updated": updated,
            "removed": removed,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/select-folder")
async def select_folder():
    """打开文件夹选择对话框"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        import os
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "html", "logo.png")
        if os.path.exists(icon_path):
            try:
                root.iconbitmap(icon_path)
            except:
                pass
        folder_path = filedialog.askdirectory(title="选择 Skill 目录")
        root.destroy()
        if folder_path:
            return {"success": True, "path": folder_path}
        return {"success": False, "path": ""}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

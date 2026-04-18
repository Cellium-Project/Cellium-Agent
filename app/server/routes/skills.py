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

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _get_skill_installer():
    from app.core.di.container import get_container
    from components.skill_installer import SkillInstaller
    container = get_container()
    return container.resolve(SkillInstaller)


def _get_skill_manager():
    from app.core.di.container import get_container
    from components.skill_manager import SkillManager
    container = get_container()
    return container.resolve(SkillManager)


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
async def install_skill(
    archive: UploadFile = File(...),
):
    """安装 Skill (通过上传压缩包)"""
    try:
        import tempfile
        from pathlib import Path
        
        archive_filename = archive.filename or ""
        valid_extensions = ['.zip', '.tar.gz', '.tgz', '.tar']
        if not any(archive_filename.lower().endswith(ext) for ext in valid_extensions):
            raise HTTPException(status_code=400, detail="不支持的压缩包格式，请使用 .zip 或 .tar.gz")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / archive_filename
            with open(archive_path, 'wb') as f:
                f.write(await archive.read())
            
            installer = _get_skill_installer()
            result = installer._cmd_install(
                archive_path=str(archive_path),
                source="upload",
            )
            
            if "error" in result:
                raise HTTPException(status_code=400, detail=result["error"])
            return result
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"安装 Skill 失败: {e}")
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
        result = installer._cmd_refresh_index()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

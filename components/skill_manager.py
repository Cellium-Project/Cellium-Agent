# -*- coding: utf-8 -*-
"""
SkillManager — Skill 信息管理组件（只读）

为 LLM 提供已安装 Skill 的列表和元信息。
LLM 通过此组件获取 Skill 名称和简要描述，然后通过 file_tool 读取完整 SKILL.md。

【职责分工】
  skill_installer (写操作):
    - install/uninstall/update: 安装/卸载/更新 Skill
    - 后台自动扫描: 每 2 秒扫描 skills 目录，自动更新索引

  skill_manager (读操作):
    - list: 列出已安装的 Skill
    - get_info: 获取指定 Skill 元信息
    - search: 搜索 Skill

使用示例：
  skill_manager.list()
  skill_manager.get_info(name="git_helper")
  skill_manager.search(query="git")
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)


class SkillManager(BaseCell):
    """
    Skill 信息管理器 — 为 LLM 提供 Skill 列表和元信息（只读索引）
    
    索引由 skill_installer 后台自动维护（每 2 秒扫描一次）。
    手动移动文件夹到 skills 目录也会被自动识别。
    """

    @property
    def cell_name(self) -> str:
        return "skill_manager"

    @staticmethod
    def _get_skills_dir() -> Path:
        """获取 Skill 安装目录"""
        return Path(__file__).resolve().parent / "skills"

    @staticmethod
    def _get_skills_index_path() -> Path:
        """获取 Skill 索引文件路径"""
        return SkillManager._get_skills_dir() / "_index.json"

    def _load_index(self) -> Dict[str, Any]:
        """加载已安装 Skill 索引"""
        index_path = self._get_skills_index_path()
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _cmd_list(self, show_details: bool = False) -> Dict[str, Any]:
        """
        列出所有已安装的 Skill
        
        Args:
            show_details: 是否显示详细信息
        
        Returns:
            {"skills": [{"name": "...", "description": "...", "skill_md_path": "..."}], "total": N}
        
        使用:
          skill_manager.list()
          skill_manager.list(show_details=true)
        """
        skills_dir = self._get_skills_dir()
        index = self._load_index()

        if not skills_dir.exists():
            return {"skills": [], "total": 0}

        result = []
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            
            name = skill_dir.name
            if name.startswith("_"):
                continue

            meta = index.get(name, {})
            frontmatter = meta.get("frontmatter", {})
            
            entry = {
                "name": name,
                "description": frontmatter.get("description", ""),
                "skill_md_path": str(skill_md),
                "frontmatter": frontmatter,
            }
            
            if show_details:
                entry.update({
                    "source": meta.get("source", "manual"),
                    "installed_at": meta.get("installed_at", "unknown"),
                    "updated_at": meta.get("updated_at", "unknown"),
                })

            result.append(entry)

        return {
            "skills": result,
            "total": len(result),
            "hint": "使用 file_tool.read(path='<skill_md_path>') 读取完整 SKILL.md 获取详细使用信息",
        }

    def _cmd_get_info(self, name: str = "") -> Dict[str, Any]:
        """
        获取指定 Skill 的元信息
        
        Args:
            name: Skill 名称
        
        Returns:
            {"name": "...", "frontmatter": {...}, "source": "...", ...}
        
        使用:
          skill_manager.get_info(name="git_helper")
        """
        if not name:
            return {"error": "必须提供 Skill 名称"}

        name = name.lower()
        skill_md = self._get_skill_md_path(name)

        if not skill_md.exists():
            return {
                "error": f"Skill '{name}' 未找到",
                "installed_skills": [
                    d.name for d in self._get_skills_dir().iterdir()
                    if d.is_dir() and (d / "SKILL.md").exists() and not d.name.startswith("_")
                ],
            }

        index = self._load_index()
        meta = index.get(name, {})
        frontmatter = meta.get("frontmatter", {})

        return {
            "name": name,
            "frontmatter": frontmatter,
            "source": meta.get("source", "manual"),
            "installed_at": meta.get("installed_at", "unknown"),
            "updated_at": meta.get("updated_at", "unknown"),
            "skill_md_path": str(skill_md),
            "hint": f"使用 file_tool.read(path='{skill_md}') 读取完整 SKILL.md 获取详细使用信息",
        }

    def _cmd_search(self, query: str = "") -> Dict[str, Any]:
        """
        搜索 Skill（按名称、描述、分类）
        
        Args:
            query: 搜索关键词
        
        Returns:
            {"results": [...], "total": N}
        
        使用:
          skill_manager.search(query="git")
        """
        if not query:
            return {"error": "必须提供搜索关键词"}

        index = self._load_index()
        q = query.lower().strip()

        results = []
        for name, meta in index.items():
            frontmatter = meta.get("frontmatter", {})
            name_match = q in name.lower()
            desc_match = q in str(frontmatter.get("description", "")).lower()
            cat_match = q in str(frontmatter.get("category", "")).lower()

            if name_match or desc_match or cat_match:
                score = 0
                if name_match:
                    score += 10
                if desc_match:
                    score += 5
                if cat_match:
                    score += 2
                
                skill_md = self._get_skill_md_path(name)
                entry = {
                    "name": name,
                    "frontmatter": frontmatter,
                    "skill_md_path": str(skill_md),
                    "_relevance_score": score,
                }
                results.append(entry)

        results.sort(key=lambda x: x["_relevance_score"], reverse=True)

        return {
            "results": results,
            "total": len(results),
            "query": query,
        }

    @staticmethod
    def _get_skill_md_path(name: str) -> Path:
        """获取指定 Skill 的 SKILL.md 路径"""
        return SkillManager._get_skills_dir() / name / "SKILL.md"

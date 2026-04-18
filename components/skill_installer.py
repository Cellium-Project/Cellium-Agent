# -*- coding: utf-8 -*-
"""
SkillInstaller — Skill 包管理器（组件）

管理可安装/卸载/更新的能力扩展包（写操作）。

【Skill vs 组件 的区别】

  组件 (Components):
    - 位于 components/*.py，系统启动自动加载
    - 通过 component.generate() 创建
    - 系统级工具，生命周期与进程一致

  Skills:
    - 位于 components/skills/<skill_name>/SKILL.md，独立目录存放
    - 通过本工具(skill_installer)安装/卸载/更新
    - 插件化能力包，可动态增删
    - 安装后 LLM 可通过 skill_manager 获取列表，通过 file_tool 读取 SKILL.md 获取完整使用信息

【职责分工】

  skill_installer (写操作):
    - install: 安装 Skill（单个/批量）
    - uninstall: 卸载 Skill
    - update: 更新 Skill

  skill_manager (读操作):
    - list: 列出已安装的 Skill
    - get_info: 获取指定 Skill 元信息
    - search: 搜索 Skill

使用示例：
  # 安装 Skill
  skill_installer.install("git_helper")
  # 卸载
  skill_installer.uninstall("git_helper")
  # 更新
  skill_installer.update("git_helper", content="...")
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)


class SkillInstaller(BaseCell):
    """
    Skill 包管理器 — 安装、卸载、更新 Skill 能力扩展包（写操作）
    
    所有 Skill 存放在 components/skills/<skill_name>/ 目录下。
    读操作（列表、查询、搜索）由 skill_manager 负责。
    """

    @property
    def cell_name(self) -> str:
        return "skill_installer"

    @staticmethod
    def _get_skills_dir() -> Path:
        """获取 Skill 安装目录"""
        return Path(__file__).resolve().parent / "skills"

    @staticmethod
    def _get_skill_dir(name: str) -> Path:
        """获取指定 Skill 的目录"""
        return SkillInstaller._get_skills_dir() / name

    @staticmethod
    def _get_skill_md_path(name: str) -> Path:
        """获取指定 Skill 的 SKILL.md 路径"""
        return SkillInstaller._get_skill_dir(name) / "SKILL.md"

    @staticmethod
    def _get_skills_index_path() -> Path:
        """获取 Skill 索引文件路径（记录已安装 Skill 的元信息）"""
        return SkillInstaller._get_skills_dir() / "_index.json"

    def _load_index(self) -> Dict[str, Any]:
        """加载已安装 Skill 索引 {skill_name: {name, source, installed_at, version, description, category}}"""
        index_path = self._get_skills_index_path()
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_index(self, index: Dict[str, Any]) -> None:
        """保存索引到文件"""
        index_path = self._get_skills_index_path()
        skills_dir = self._get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise RuntimeError(f"保存索引失败: {e}") from e

    @staticmethod
    def _parse_skill_md_frontmatter(content: str) -> Dict[str, Any]:
        """解析 SKILL.md 的 YAML frontmatter（通用解析，支持任意键名和嵌套结构）"""
        if not content.startswith("---"):
            return {}
        
        end_match = re.search(r"^---\s*$", content[3:], re.MULTILINE)
        if not end_match:
            return {}
        
        frontmatter_str = content[3:3 + end_match.start()]
        
        try:
            return SkillInstaller._parse_yaml_simple(frontmatter_str)
        except Exception:
            return {}

    @staticmethod
    def _parse_yaml_simple(text: str) -> Dict[str, Any]:
        result = {}
        lines = text.split("\n")
        stack = [(result, -1)]  # (current_dict, indent_level)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()
            
            if not stripped or stripped.lstrip().startswith("#"):
                i += 1
                continue
            
            indent = len(line) - len(line.lstrip())
            content = stripped.lstrip()
            
            while len(stack) > 1 and indent <= stack[-1][1]:
                stack.pop()
            
            current_dict = stack[-1][0]
            
            if ":" not in content:
                i += 1
                continue
            
            colon_pos = content.index(":")
            key = content[:colon_pos].strip()
            value_part = content[colon_pos + 1:].strip()
            
            if value_part == "" or value_part == "|" or value_part == ">":
                new_dict = {}
                current_dict[key] = new_dict
                
                if value_part in ("|", ">"):
                    multiline_lines = []
                    i += 1
                    while i < len(lines):
                        next_line = lines[i]
                        if next_line.strip() == "":
                            multiline_lines.append("")
                            i += 1
                            continue
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if next_indent > indent:
                            multiline_lines.append(next_line.lstrip())
                            i += 1
                        else:
                            break
                    
                    text_value = "\n".join(line for line in multiline_lines if line or (value_part == "|"))
                    if value_part == ">":
                        text_value = " ".join(line for line in multiline_lines if line.strip())
                    current_dict[key] = text_value
                    continue
                else:
                    stack.append((new_dict, indent))
            elif value_part.startswith("- "):
                list_items = []
                item_content = value_part[2:].strip()
                if item_content:
                    list_items.append(item_content.strip('"').strip("'"))
                
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.strip() == "":
                        i += 1
                        continue
                    next_indent = len(next_line) - len(next_line.lstrip())
                    if next_indent > indent:
                        next_content = next_line.lstrip()
                        if next_content.startswith("- "):
                            list_items.append(next_content[2:].strip().strip('"').strip("'"))
                        i += 1
                    else:
                        break
                
                current_dict[key] = list_items
                continue
            else:
                value = value_part.strip('"').strip("'")
                if value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.lower() == "null" or value == "~":
                    value = None
                else:
                    try:
                        if "." in value:
                            value = float(value)
                        else:
                            value = int(value)
                    except ValueError:
                        pass
                
                current_dict[key] = value
            
            i += 1
        
        return result

    # ================================================================
    #  命令方法（写操作：安装/卸载/更新）
    # ================================================================

    def _cmd_install(
        self,
        name: str = "",
        source: str = "",
        content: str = "",
        source_dir: str = "",
        archive_path: str = "",
    ) -> Dict[str, Any]:
        """
        安装一个或多个 Skill 到 skills/ 目录
        
        Args:
            name: Skill 名称（小写英文标识符，如 git_helper）。如果为空且提供 source_dir/archive_path，则自动扫描
            source: 来源描述（如 "builtin" / "local" / "url:https://..."）
            content: 可选的 SKILL.md 内容（如果提供则直接写入；否则生成模板）
            source_dir: 可选的 Skill 包目录路径（包含 SKILL.md 的完整目录结构）
            archive_path: 可选的压缩包路径（支持 .zip, .tar.gz, .tgz, .tar）
        
        Returns:
            {"success": True, "installed": [...], "skipped": [...], "errors": [...]} 或 error
        
        使用:
          skill_installer.install("git_helper")              # 从模板创建单个
          skill_installer.install(name="my_skill", source_dir="D:/skills/my_skill")  # 从目录安装单个
          skill_installer.install(source_dir="D:/skills-main/skills")  # 批量安装目录下所有 Skill
          skill_installer.install(name="my_skill", content="...")  # 从内容创建单个
          skill_installer.install(archive_path="D:/my_skill.zip")  # 从压缩包安装
        """
        skills_dir = self._get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)

        if archive_path and archive_path.strip():
            return self._install_from_archive(archive_path, skills_dir, source, name)

        if source_dir and source_dir.strip() and (not name or not name.strip()):
            return self._batch_install_from_directory(source_dir, skills_dir, source)

        if not name or not name.strip():
            return {
                "error": "Skill 名称不能为空",
                "hint": "提供有效的 Skill 名称，如 git_helper, pr-review",
            }
        
        name = name.strip()
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            return {
                "error": f"无效的 Skill 名称 '{name}'",
                "hint": "名称只能包含字母、数字、下划线和连字符，如 git_helper, pr-review, my-skill-123",
            }
        
        if name.startswith('-') or name.endswith('-'):
            return {
                "error": f"无效的 Skill 名称 '{name}'",
                "hint": "名称不能以连字符开头或结尾",
            }

        name = name.lower()
        skill_dir = self._get_skill_dir(name)
        skill_md_path = self._get_skill_md_path(name)

        if skill_dir.exists():
            return {
                "error": f"Skill '{name}' 已存在",
                "existing_dir": str(skill_dir),
                "hint": (
                    f"如需更新请使用 update 命令，"
                    f"或先 uninstall 再重新 install"
                ),
            }

        if source_dir and source_dir.strip():
            return self._install_from_directory(name, source_dir, skills_dir, skill_dir, skill_md_path, source)
        elif content and content.strip():
            return self._install_from_content(name, content, skills_dir, skill_dir, skill_md_path, source)
        else:
            return self._install_from_template(name, skills_dir, skill_dir, skill_md_path, source)

    def _find_all_skills(self, root_dir: Path) -> List[Path]:
        """
        递归查找所有包含 SKILL.md 的文件夹
        
        Args:
            root_dir: 根目录
        
        Returns:
            所有包含 SKILL.md 的文件夹路径列表
        """
        skill_dirs = []
        
        for dirpath, dirnames, filenames in root_dir.walk():
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            
            if "SKILL.md" in filenames:
                skill_dirs.append(dirpath)
        
        return skill_dirs

    def _batch_install_from_directory(
        self, source_dir: str, skills_dir: Path, source: str
    ) -> Dict[str, Any]:
        """批量安装目录下所有 Skill"""
        src_path = Path(source_dir)
        if not src_path.exists():
            return {"error": f"源目录不存在: {source_dir}"}
        
        if not src_path.is_dir():
            return {"error": f"源路径不是目录: {source_dir}"}

        skill_dirs = self._find_all_skills(src_path)
        
        if not skill_dirs:
            return {
                "error": f"在 {source_dir} 中未找到任何包含 SKILL.md 的文件夹",
                "hint": "确保 Skill 文件夹根目录包含 SKILL.md 文件",
            }

        installed = []
        skipped = []
        errors = []

        for skill_dir_path in skill_dirs:
            skill_name = skill_dir_path.name
            dest_dir = self._get_skill_dir(skill_name)
            dest_md = self._get_skill_md_path(skill_name)

            if dest_dir.exists():
                skipped.append({
                    "name": skill_name,
                    "reason": "已存在",
                    "path": str(dest_dir),
                })
                continue

            try:
                import shutil
                shutil.copytree(str(skill_dir_path), str(dest_dir))

                with open(dest_md, "r", encoding="utf-8") as f:
                    md_content = f.read()

                metadata = self._parse_skill_md_frontmatter(md_content)
                index = self._load_index()
                index[skill_name] = {
                    "name": skill_name,
                    "source": source or "local",
                    "installed_at": datetime.now().isoformat(),
                    "frontmatter": metadata,
                }
                self._save_index(index)

                installed.append({
                    "name": skill_name,
                    "path": str(dest_dir),
                    "description": metadata.get("description", ""),
                })

                logger.info(f"[SkillInstaller] 批量安装 | name={skill_name} | path={dest_dir}")
            except Exception as e:
                errors.append({
                    "name": skill_name,
                    "error": str(e),
                })

        return {
            "success": True,
            "message": f"批量安装完成",
            "installed": installed,
            "skipped": skipped,
            "errors": errors,
            "total_found": len(skill_dirs),
            "total_installed": len(installed),
            "total_skipped": len(skipped),
            "total_errors": len(errors),
        }

    def _install_from_archive(
        self, archive_path: str, skills_dir: Path, source: str, name: str = ""
    ) -> Dict[str, Any]:
        """从压缩包安装 Skill（支持 .zip, .tar.gz, .tgz, .tar）"""
        import tempfile
        import zipfile
        import tarfile
        import shutil
        
        archive = Path(archive_path)
        if not archive.exists():
            return {"error": f"压缩包不存在: {archive_path}"}
        
        archive_name = archive.name.lower()
        if not (archive_name.endswith('.zip') or archive_name.endswith('.tar.gz') or 
                archive_name.endswith('.tgz') or archive_name.endswith('.tar')):
            return {
                "error": "不支持的压缩包格式",
                "hint": "请使用 .zip 或 .tar.gz 格式的压缩包",
            }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()
            
            try:
                if archive_name.endswith('.zip'):
                    with zipfile.ZipFile(archive, 'r') as zf:
                        zf.extractall(extract_dir)
                else:
                    with tarfile.open(archive, 'r:*') as tf:
                        tf.extractall(extract_dir)
            except Exception as e:
                return {"error": f"解压失败: {e}"}
            
            skill_md_files = list(extract_dir.rglob("SKILL.md"))
            if not skill_md_files:
                return {
                    "error": "压缩包中未找到 SKILL.md 文件",
                    "hint": "Skill 包根目录必须包含 SKILL.md 文件",
                }
            
            skill_dirs_found = list(set(f.parent for f in skill_md_files))
            
            if len(skill_dirs_found) == 1:
                skill_src_dir = skill_dirs_found[0]
                skill_name = name.lower() if name and name.strip() else skill_src_dir.name
                dest_dir = self._get_skill_dir(skill_name)
                
                if dest_dir.exists():
                    return {
                        "error": f"Skill '{skill_name}' 已存在",
                        "existing_dir": str(dest_dir),
                        "hint": "如需更新请先卸载再重新安装",
                    }
                
                try:
                    shutil.copytree(str(skill_src_dir), str(dest_dir))
                except Exception as e:
                    return {"error": f"复制目录失败: {e}"}
                
                dest_md = dest_dir / "SKILL.md"
                with open(dest_md, "r", encoding="utf-8") as f:
                    md_content = f.read()
                
                return self._finalize_install(skill_name, md_content, dest_dir, dest_md, source or "archive")
            else:
                installed = []
                skipped = []
                errors = []
                
                for skill_src_dir in skill_dirs_found:
                    skill_name = skill_src_dir.name
                    dest_dir = self._get_skill_dir(skill_name)
                    
                    if dest_dir.exists():
                        skipped.append({
                            "name": skill_name,
                            "reason": "已存在",
                        })
                        continue
                    
                    try:
                        shutil.copytree(str(skill_src_dir), str(dest_dir))
                        dest_md = dest_dir / "SKILL.md"
                        with open(dest_md, "r", encoding="utf-8") as f:
                            md_content = f.read()
                        
                        metadata = self._parse_skill_md_frontmatter(md_content)
                        index = self._load_index()
                        index[skill_name] = {
                            "name": skill_name,
                            "source": source or "archive",
                            "installed_at": datetime.now().isoformat(),
                            "frontmatter": metadata,
                        }
                        self._save_index(index)
                        
                        installed.append({
                            "name": skill_name,
                            "path": str(dest_dir),
                        })
                        logger.info(f"[SkillInstaller] 从压缩包安装 | name={skill_name}")
                    except Exception as e:
                        errors.append({
                            "name": skill_name,
                            "error": str(e),
                        })
                
                return {
                    "success": True,
                    "message": f"从压缩包批量安装完成",
                    "installed": installed,
                    "skipped": skipped,
                    "errors": errors,
                }

    def _install_from_directory(
        self, name: str, source_dir: str, skills_dir: Path, skill_dir: Path, skill_md_path: Path, source: str
    ) -> Dict[str, Any]:
        """从目录安装 Skill（保留完整目录结构）"""
        import shutil
        
        src_path = Path(source_dir)
        if not src_path.exists():
            return {"error": f"源目录不存在: {source_dir}"}
        
        if not src_path.is_dir():
            return {"error": f"源路径不是目录: {source_dir}"}
        
        skill_md_in_src = src_path / "SKILL.md"
        if not skill_md_in_src.exists():
            return {
                "error": f"源目录中未找到 SKILL.md 文件",
                "hint": "Skill 包根目录必须包含 SKILL.md 文件",
            }

        try:
            shutil.copytree(str(src_path), str(skill_dir))
        except Exception as e:
            return {"error": f"复制目录失败: {e}"}

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
        except Exception as e:
            return {"error": f"读取 SKILL.md 失败: {e}"}

        return self._finalize_install(name, md_content, skill_dir, skill_md_path, source or "local")

    def _install_from_content(
        self, name: str, content: str, skills_dir: Path, skill_dir: Path, skill_md_path: Path, source: str
    ) -> Dict[str, Any]:
        """从内容安装 Skill（仅创建 SKILL.md）"""
        md_content = content.strip()
        
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            return {"error": f"写入 Skill 文件失败: {e}"}

        return self._finalize_install(name, md_content, skill_dir, skill_md_path, source or "custom")

    def _install_from_template(
        self, name: str, skills_dir: Path, skill_dir: Path, skill_md_path: Path, source: str
    ) -> Dict[str, Any]:
        """从模板创建 Skill"""
        md_content = self._generate_skill_md_template(name)
        
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            return {"error": f"写入 Skill 文件失败: {e}"}

        return self._finalize_install(name, md_content, skill_dir, skill_md_path, source or "builtin")

    def _finalize_install(
        self, name: str, md_content: str, skill_dir: Path, skill_md_path: Path, source: str
    ) -> Dict[str, Any]:
        """完成安装流程（解析元信息、更新索引、返回结果）"""
        metadata = self._parse_skill_md_frontmatter(md_content)
        index = self._load_index()
        index[name] = {
            "name": name,
            "source": source,
            "installed_at": datetime.now().isoformat(),
            "frontmatter": metadata,
        }
        self._save_index(index)

        logger.info(f"[SkillInstaller] 已安装 Skill | name={name} | path={skill_md_path}")

        return {
            "success": True,
            "message": f"Skill '{name}' 已安装",
            "skill_name": name,
            "skill_dir": str(skill_dir),
            "skill_md_path": str(skill_md_path),
            "source": source,
            "next_step": (
                f"Skill '{name}' 已安装。使用 file_tool 读取 SKILL.md 获取完整使用信息：\n"
                f'file_tool.read(path="{skill_md_path}")'
            ),
        }

    def _cmd_uninstall(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        卸载一个已安装的 Skill（删除目录并从注册表移除）
        
        Args:
            name: 要卸载的 Skill 名称
        
        Returns:
            {"success": True, "uninstalled": "..."} 或 error
        
        使用:
          skill_installer.uninstall("git_helper")
        """
        if not name:
            return {"error": "必须提供要卸载的 Skill 名称"}

        name = name.lower()
        skills_dir = self._get_skills_dir()
        skill_dir = self._get_skill_dir(name)

        if not skill_dir.exists():
            return {
                "error": f"Skill '{name}' 未找到",
                "installed_skills": [
                    d.name for d in skills_dir.iterdir()
                    if d.is_dir() and (d / "SKILL.md").exists() and not d.name.startswith("_")
                ],
            }

        try:
            import shutil
            shutil.rmtree(skill_dir)
        except Exception as e:
            return {"error": f"删除目录失败: {e}"}

        index = self._load_index()
        index.pop(name, None)
        self._save_index(index)

        logger.info("[SkillInstaller] 已卸载 Skill | name=%s", name)

        return {
            "success": True,
            "message": f"Skill '{name}' 已卸载",
            "uninstalled": name,
            "removed_dir": str(skill_dir),
        }

    def _cmd_refresh_index(self) -> Dict[str, Any]:
        """
        手动立即刷新索引（扫描 skills 目录并更新 _index.json）
        
        Returns:
            {"success": True, "updated": [...], "removed": [...], "total": N}
        
        使用:
          skill_installer.refresh_index()
        """
        updated, removed = self._scan_and_update_index(force=True)
        
        return {
            "success": True,
            "message": "索引已刷新",
            "updated": updated,
            "removed": removed,
            "total": len(self._load_index()),
        }

    def _cmd_update(
        self,
        name: str,
        content: str = "",
        source_dir: str = "",
    ) -> Dict[str, Any]:
 
        if not name:
            return {"error": "必须提供要更新的 Skill 名称"}

        name = name.lower()
        skill_dir = self._get_skill_dir(name)
        skill_md_path = self._get_skill_md_path(name)

        if not skill_dir.exists():
            return {
                "error": f"Skill '{name}' 未安装",
                "hint": "请先用 install 命令安装",
            }

        if source_dir and source_dir.strip():
            return self._update_from_directory(name, source_dir, skill_dir, skill_md_path)
        elif content and content.strip():
            return self._update_from_content(name, content, skill_md_path)
        else:
            return self._refresh_metadata(name, skill_md_path)

    def _update_from_directory(
        self, name: str, source_dir: str, skill_dir: Path, skill_md_path: Path
    ) -> Dict[str, Any]:
        """从目录更新 Skill（覆盖整个目录）"""
        import shutil
        
        src_path = Path(source_dir)
        if not src_path.exists():
            return {"error": f"源目录不存在: {source_dir}"}
        
        if not src_path.is_dir():
            return {"error": f"源路径不是目录: {source_dir}"}
        
        skill_md_in_src = src_path / "SKILL.md"
        if not skill_md_in_src.exists():
            return {
                "error": f"源目录中未找到 SKILL.md 文件",
                "hint": "Skill 包根目录必须包含 SKILL.md 文件",
            }

        try:
            shutil.rmtree(str(skill_dir))
            shutil.copytree(str(src_path), str(skill_dir))
        except Exception as e:
            return {"error": f"更新目录失败: {e}"}

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
        except Exception as e:
            return {"error": f"读取 SKILL.md 失败: {e}"}

        return self._finalize_update(name, md_content, skill_dir, skill_md_path)

    def _update_from_content(
        self, name: str, content: str, skill_md_path: Path
    ) -> Dict[str, Any]:
        """从内容更新 SKILL.md"""
        try:
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(content.strip())
        except Exception as e:
            return {"error": f"写入文件失败: {e}"}

        return self._finalize_update(name, content, None, skill_md_path)

    def _refresh_metadata(self, name: str, skill_md_path: Path) -> Dict[str, Any]:
        """仅刷新元数据和时间戳"""
        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                md_content = f.read()
        except Exception as e:
            return {"error": f"读取 SKILL.md 失败: {e}"}

        return self._finalize_update(name, md_content, None, skill_md_path)

    def _finalize_update(
        self, name: str, md_content: str, skill_dir: Optional[Path], skill_md_path: Path
    ) -> Dict[str, Any]:
        """完成更新流程（解析元信息、更新索引、返回结果）"""
        metadata = self._parse_skill_md_frontmatter(md_content)
        index = self._load_index()
        if name in index:
            index[name].update({
                "updated_at": datetime.now().isoformat(),
                "frontmatter": metadata,
            })
            self._save_index(index)

        result = {
            "success": True,
            "message": f"Skill '{name}' 已更新",
            "updated": name,
            "skill_md_path": str(skill_md_path),
            "content_updated": bool(md_content and md_content.strip()),
        }
        
        if skill_dir:
            result["skill_dir"] = str(skill_dir)
        
        return result

    def on_load(self):
        """确保 skills 目录存在，启动后台自动扫描索引"""
        super().on_load()
        skills_dir = self._get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)
        
        self._scan_interval = 5  # 扫描间隔（秒）
        self._scan_thread = None
        self._scan_running = False
        self._start_auto_scan()
        
        installed_count = len([
            d for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists() and not d.name.startswith("_")
        ])
        logger.info(
            "[SkillInstaller] 就绪 | skills_dir=%s | 已安装=%d | 自动扫描=%ds",
            skills_dir, installed_count, self._scan_interval,
        )

    def _start_auto_scan(self):
        """启动后台自动扫描线程"""
        import threading
        
        self._scan_running = True
        self._scan_thread = threading.Thread(target=self._auto_scan_loop, daemon=True)
        self._scan_thread.start()
        logger.info("[SkillInstaller] 后台自动扫描已启动")

    def _auto_scan_loop(self):
        """后台扫描循环"""
        import time
        
        while self._scan_running:
            try:
                self._scan_and_update_index()
            except Exception as e:
                logger.warning(f"[SkillInstaller] 自动扫描异常: {e}")
            time.sleep(self._scan_interval)

    def _scan_and_update_index(self, force: bool = False) -> tuple:
        skills_dir = self._get_skills_dir()
        if not skills_dir.exists():
            return [], []

        index = self._load_index()
        updated_names = []
        removed_names = []

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
            need_update = force or name not in index
            
            if not need_update and not force:
                try:
                    md_mtime = skill_md.stat().st_mtime
                    indexed_at = meta.get("_scanned_at", 0)
                    if md_mtime > indexed_at:
                        need_update = True
                except Exception:
                    pass

            if need_update:
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        md_content = f.read()
                    
                    frontmatter = self._parse_skill_md_frontmatter(md_content)
                    stat = skill_dir.stat()
                    
                    index[name] = {
                        "name": name,
                        "source": meta.get("source", "manual"),
                        "installed_at": meta.get("installed_at", datetime.fromtimestamp(stat.st_ctime).isoformat()),
                        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "frontmatter": frontmatter,
                        "_scanned_at": datetime.now().timestamp(),
                    }
                    updated_names.append(name)
                    logger.debug(f"[SkillInstaller] 扫描更新 | name={name}")
                except Exception as e:
                    logger.warning(f"[SkillInstaller] 扫描 {name} 失败: {e}")

        current_skills = {
            d.name for d in skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists() and not d.name.startswith("_")
        }
        for name in list(index.keys()):
            if name not in current_skills:
                del index[name]
                removed_names.append(name)
                logger.debug(f"[SkillInstaller] 扫描清理 | name={name}")

        if updated_names or removed_names:
            self._save_index(index)
        
        return updated_names, removed_names

    def on_unload(self):
        """停止后台扫描"""
        self._scan_running = False
        if self._scan_thread:
            self._scan_thread.join(timeout=3)
        logger.info("[SkillInstaller] 后台自动扫描已停止")

    # ================================================================
    #  内部方法
    # ================================================================

    @staticmethod
    def _generate_skill_md_template(name: str) -> str:
        """生成标准 SKILL.md 模板"""
        now = datetime.now().strftime("%Y-%m-%d")
        return f'''---
name: {name}
description: >
  {name} — Cellium Skill 能力描述。
  在此描述此 Skill 的用途、使用场景和触发条件。
license: MIT
metadata:
  version: "1.0.0"
  category: general
  created_at: {now}
---

# {name}

## 概述

在此描述此 Skill 的主要功能和用途。

## 使用场景

- 场景1：描述何时使用此 Skill
- 场景2：另一个使用场景
- 场景3：更多使用场景

## 使用方法

### 步骤1

描述第一步操作。

### 步骤2

描述第二步操作。

### 步骤3

描述第三步操作。

## 示例

### 示例1：基本使用

描述基本使用示例。

### 示例2：高级使用

描述高级使用示例。

## 注意事项

- 注意事项1
- 注意事项2
- 注意事项3

## 相关资源

- 相关链接或文档
- 其他相关 Skill
'''

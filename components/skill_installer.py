# -*- coding: utf-8 -*-
"""
SkillInstaller — Skill 包管理器（组件）

管理可安装/卸载的能力扩展包。

【Skill vs 组件 的区别】

  组件 (Components):
    - 位于 components/*.py，系统启动自动加载
    - 通过 component.generate() 创建
    - 系统级工具，生命周期与进程一致

  Skills:
    - 位于 components/skills/*.py，独立目录存放
    - 通过本工具(skill_installer)安装/卸载
    - 插件化能力包，可动态增删
    - 安装后自动注册为 LLM 可调用工具

使用示例：
  # 安装远程 Skill
  skill_installer.install("git_helper")
  # 列出已安装的 Skill
  skill_installer.list_installed()
  # 卸载
  skill_installer.uninstall("git_helper")
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)


class SkillInstaller(BaseCell):
    """
    Skill 包管理器 — 安装、卸载、列表、搜索、更新、查看 Skill 能力扩展包
    
    所有 Skill 文件存放在 components/skills/ 目录下。
    安装后自动触发热重载，LLM 立即可用。
    """

    @property
    def cell_name(self) -> str:
        return "skill_installer"

    @staticmethod
    def _get_skills_dir() -> Path:
        """获取 Skill 安装目录"""
        return Path(__file__).resolve().parent / "skills"

    @staticmethod
    def _get_skills_index_path() -> Path:
        """获取 Skill 索引文件路径（记录已安装 Skill 的元信息）"""
        return SkillInstaller._get_skills_dir() / "_index.json"

    def _load_index(self) -> Dict[str, Any]:
        """加载已安装 Skill 索引 {skill_name: {name, source, installed_at, version}}"""
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

    def _reload_components(self) -> Dict[str, Any]:
        """触发热重载以加载新安装的 Skill"""
        from app.core.util.components_loader import hot_reload
        try:
            container = None
            try:
                from app.core.di.container import get_container as get_di
                container = get_di()
            except Exception:
                pass
            report = hot_reload(container=container)

            # 同步工具注册表
            try:
                from app.core.util.component_tool_registry import get_component_tool_registry
                reg = get_component_tool_registry()
                reg.sync_from_components_loader()
            except Exception:
                pass

            return report
        except Exception as e:
            return {"error": str(e), "hint": "热重载失败，可能需要手动重启"}

    # ================================================================
    #  命令方法
    # ================================================================

    def _cmd_list_installed(
        self,
        show_details: bool = False,
    ) -> Dict[str, Any]:
        """
        列出所有已安装的 Skill
        
        Args:
            show_details: 是否显示详细信息（版本、来源、安装时间等）
        
        Returns:
            {"skills": [...], "total": N}
        
        使用:
          skill_installer.list_installed()
          skill_installer.list_installed(show_details=true)
        """
        skills_dir = self._get_skills_dir()
        index = self._load_index()

        if not skills_dir.exists():
            return {"skills": [], "total": 0, "skills_dir": str(skills_dir)}

        result = []
        for py_file in sorted(skills_dir.glob("*.py")):
            name = py_file.stem
            if name.startswith("_"):
                continue

            entry = {"name": name}
            
            if show_details:
                meta = index.get(name, {})
                entry.update({
                    "source": meta.get("source", "unknown"),
                    "installed_at": meta.get("installed_at", "unknown"),
                    "version": meta.get("version", "?"),
                    "file_size": py_file.stat().st_size,
                    "file_path": str(py_file),
                    "has_help": True,  # 假设都遵循规范带 help 方法
                })

            result.append(entry)

        return {
            "skills": result,
            "total": len(result),
            "skills_dir": str(skills_dir),
        }

    def _cmd_list_available(
        self,
        category: str = "",
    ) -> Dict[str, Any]:
        """
        列出所有可用的 Skill 模板（内置 + 已知仓库）
        
        Args:
            category: 分类过滤（如 code、file、web），空则列出全部
        
        Returns:
            {"skills": [{name, description, category, commands}], "total": N}
        
        使用:
          skill_installer.list_available()
          skill_installer.list_available(category="code")
        """
        # ★ 内置 Skill 模板库（后续可接入远程仓库）
        available = [
            {
                "name": "git_helper",
                "description": "Git 操作增强 — commit/push/pull/branch/log 一站式",
                "category": "code",
                "commands": ["commit", "push", "pull", "branch", "log", "diff", "status"],
            },
            {
                "name": "code_refactor",
                "description": "代码重构助手 — 重命名变量、提取函数、优化结构",
                "category": "code",
                "commands": ["rename_var", "extract_func", "simplify", "add_type_hints"],
            },
            {
                "name": "doc_generator",
                "description": "文档生成器 — 自动生成 README/API 文档/注释",
                "category": "docs",
                "commands": ["readme", "api_doc", "comment_code", "changelog"],
            },
            {
                "name": "web_fetcher",
                "description": "网页内容抓取 — 深度提取页面正文/表格/链接",
                "category": "web",
                "commands": ["fetch_article", "extract_table", "extract_links", "search"],
            },
            {
                "name": "data_analyzer",
                "description": "数据分析工具 — CSV/JSON 统计、可视化建议",
                "category": "data",
                "commands": ["summary", "stats", "compare", "chart_hint"],
            },
        ]

        # 过滤分类
        if category:
            available = [s for s in available if s["category"] == category]

        # 标记已安装状态
        index = self._load_index()
        for s in available:
            s["installed"] = s["name"] in index

        categories = sorted({s["category"] for s in available})

        return {
            "skills": available,
            "total": len(available),
            "categories": categories,
            "note": (
                "这是内置 Skill 模板列表。"
                "install 命令会根据名称生成对应 Skill 并安装。"
                "也可通过 install 从本地 .py 文件或 URL 安装自定义 Skill。"
            ),
        }

    def _cmd_install(
        self,
        name: str,
        source: str = "",
        content: str = "",
    ) -> Dict[str, Any]:
        """
        安装一个 Skill 到 skills/ 目录
        
        Args:
            name: Skill 名称（小写英文标识符，如 git_helper）
            source: 来源描述（如 "builtin" / "local" / "url:https://..."）
            content: 可选的 Skill 源代码（如果提供则直接写入；否则生成模板）
        
        Returns:
            {"success": True, "skill_name": "...", "file_path": "..."} 或 error
        
        使用:
          skill_installer.install("git_helper")              # 从模板创建
          skill_installer.install(name="my_skill", source="local", content="...")
        """
        if not name or not name.isidentifier():
            return {
                "error": f"无效的 Skill 名称 '{name}'：必须是合法 Python 标识符",
                "hint": "使用小写英文和下划线，如 git_helper, code_refactor",
            }

        name = name.lower()
        skills_dir = self._get_skills_dir()
        file_path = skills_dir / f"{name}.py"

        # 检查是否已存在
        if file_path.exists():
            return {
                "error": f"Skill '{name}' 已存在",
                "existing_file": str(file_path),
                "hint": (
                    f"如需更新请使用 update 命令，"
                    f"或先 uninstall 再重新 install"
                ),
            }

        # 确保目录存在
        skills_dir.mkdir(parents=True, exist_ok=True)

        # 决定写入内容
        if content and content.strip():
            # 用户提供了源代码 → 直接使用
            code = content.strip()
            actual_source = source or "custom"
        else:
            # 无源代码 → 生成标准 Skill 模板
            class_name = "".join(word.capitalize() for word in name.split("_"))
            code = self._generate_skill_template(name, class_name)
            actual_source = source or "builtin"

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return {"error": f"写入 Skill 文件失败: {e}"}

        # 更新索引
        index = self._load_index()
        index[name] = {
            "name": name,
            "source": actual_source,
            "installed_at": datetime.now().isoformat(),
            "version": "1.0.0",
        }
        self._save_index(index)

        # 触发热重载
        reload_report = self._reload_components()

        logger.info(f"[SkillInstaller] 已安装 Skill | name={name} | path={file_path}")

        return {
            "success": True,
            "message": f"Skill '{name}' 已安装并激活",
            "skill_name": name,
            "file_path": str(file_path),
            "class_name": "".join(word.capitalize() for word in name.split("_")),
            "source": actual_source,
            "reload_report": reload_report.get("added", []),
            "next_step": (
                f"Skill '{name}' 已注册为 LLM 工具，现在可以直接调用。"
                f"可用 'skill_installer.info(\"{name}\")' 查看详情。"
            ),
        }

    def _cmd_uninstall(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        卸载一个已安装的 Skill（删除文件并从注册表移除）
        
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
        file_path = skills_dir / f"{name}.py"

        if not file_path.exists():
            return {
                "error": f"Skill '{name}' 未找到",
                "installed_skills": [
                    p.stem for p in skills_dir.glob("*.py")
                    if not p.stem.startswith("_")
                ],
            }

        # 删除文件
        try:
            file_path.unlink()
        except Exception as e:
            return {"error": f"删除文件失败: {e}"}

        # 更新索引
        index = self._load_index()
        index.pop(name, None)
        self._save_index(index)

        # 触发热重载（让系统卸载该组件）
        reload_report = self._reload_components()

        logger.info("[SkillInstaller] 已卸载 Skill | name=%s", name)

        return {
            "success": True,
            "message": f"Skill '{name}' 已卸载",
            "uninstalled": name,
            "removed_files": [str(file_path)],
            "reload_report": reload_report.get("removed", []),
        }

    def _cmd_update(
        self,
        name: str,
        content: str = "",
    ) -> Dict[str, Any]:
        """
        更新一个已安装的 Skill（覆盖文件内容）
        
        Args:
            name: Skill 名称
            content: 新的源代码内容（不提供则仅刷新元数据）
        
        Returns:
            {"success": True, "updated": "..."} 或 error
        
        使用:
          skill_installer.update("my_skill", content="新的完整代码...")
        """
        if not name:
            return {"error": "必须提供要更新的 Skill 名称"}

        name = name.lower()
        skills_dir = self._get_skills_dir()
        file_path = skills_dir / f"{name}.py"

        if not file_path.exists():
            return {
                "error": f"Skill '{name}' 未安装",
                "hint": "请先用 install 命令安装",
            }

        if content and content.strip():
            # 更新文件内容
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content.strip())
            except Exception as e:
                return {"error": f"写入文件失败: {e}"}

        # 更新索引时间戳
        index = self._load_index()
        if name in index:
            index[name]["updated_at"] = datetime.now().isoformat()
        self._save_index(index)

        # 触发热重载
        reload_report = self._reload_components()

        return {
            "success": True,
            "message": f"Skill '{name}' 已更新",
            "updated": name,
            "file_path": str(file_path),
            "content_updated": bool(content and content.strip()),
            "reload_report": reload_report,
        }

    def _cmd_info(
        self,
        name: str,
    ) -> Dict[str, Any]:
        """
        查看 Skill 的详细信息
        
        Args:
            name: Skill 名称
        
        Returns:
            Skill 完整信息（元数据、命令、源码预览）或 error
        """
        if not name:
            # 返回 Skill 系统总览
            skills_dir = self._get_skills_dir()
            index = self._load_index()
            installed_count = len([p for p in skills_dir.glob("*.py") if not p.stem.startswith("_")])
            return {
                "system_info": {
                    "skills_dir": str(skills_dir),
                    "installed_count": installed_count,
                    "index_file": str(self._get_skills_index_path()),
                    "difference_from_components": (
                        "Components: 系统内置，components/*.py，随系统启动加载\n"
                        "Skills: 插件式能力包，components/skills/*.py，通过 skill_installer 管理\n"
                        "Skills 安装后也是 BaseCell 子类，会被组件系统发现并注册为 LLM 工具"
                    ),
                },
                "installed_skills": list(index.keys()),
            }

        name = name.lower()
        skills_dir = self._get_skills_dir()
        file_path = skills_dir / f"{name}.py"
        index = self._load_index()

        if not file_path.exists():
            return {
                "error": f"Skill '{name}' 未找到",
                "available": [p.stem for p in skills_dir.glob("*.py") if not p.stem.startswith("_")],
            }

        meta = index.get(name, {})

        # 尝试读取源码预览
        code_preview = None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                code_preview = "".join(lines[:60])
                if len(lines) > 60:
                    code_preview += f"\n... (共 {len(lines)} 行)"
        except Exception:
            pass

        # 尝试获取已加载实例的信息
        loaded_info = {}
        try:
            from app.core.util.components_loader import get_cell
            cell = get_cell(name)
            if cell:
                loaded_info = {
                    "loaded": True,
                    "cell_name": cell.cell_name,
                    "commands": cell.get_commands(),
                    "command_count": len(cell.get_commands()),
                    "is_tool_registered": True,
                }
        except Exception:
            loaded_info = {"loaded": False}

        stat = file_path.stat()

        return {
            "name": name,
            "file_path": str(file_path),
            "meta": {
                "source": meta.get("source", "unknown"),
                "installed_at": meta.get("installed_at", "unknown"),
                "updated_at": meta.get("updated_at"),
                "version": meta.get("version", "?"),
            },
            "size_bytes": stat.st_size,
            **loaded_info,
            "code_preview": code_preview,
        }

    def _cmd_search(
        self,
        query: str,
    ) -> Dict[str, Any]:
        """
        搜索可用的 Skill（模糊匹配名称和描述）
        
        Args:
            query: 搜索关键词
        
        Returns:
            {"results": [...], "query": "...", "total": N}
        """
        all_skills = self._cmd_list_available()["skills"]
        installed_set = set(self._load_index().keys())
        q = query.lower().strip()

        results = []
        for skill in all_skills:
            name_match = q in skill["name"].lower()
            desc_match = q in skill["description"].lower()
            cat_match = q in skill["category"].lower()

            if name_match or desc_match or cat_match:
                score = 0
                if name_match:
                    score += 10
                if desc_match:
                    score += 5
                if cat_match:
                    score += 2
                
                entry = dict(skill)
                entry["_relevance_score"] = score
                results.append(entry)

        results.sort(key=lambda x: x["_relevance_score"], reverse=True)

        return {
            "results": results,
            "query": query,
            "total": len(results),
        }

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询 SkillInstaller 使用帮助"""
        commands = self.get_commands()
        base_info: Dict[str, Any] = {
            "name": self.cell_name,
            "description": (
                "Skill 包管理器 — 管理可安装/卸载的能力扩展包\n\n"
                "[Skill vs 组件]\n"
                "- 组件(Components): 系统内置工具，components/*.py，通过 component.generate 创建\n"
"- Skill(Skills): 插件式能力包，components/skills/*.py，由本工具管理\n"
                "- Skill 安装后也是 BaseCell 子类，自动被组件系统识别为 LLM 工具\n"
                "- 卸载 Skill 只需删除对应文件，不影响其他组件"
            ),
            "available_commands": commands,
            "command_count": len(commands),
            "usage_examples": [
                {"command": "list_installed", "args": {}, "description": "列出所有已安装的 Skill"},
                {"command": "list_available", "args": {"category": ""}, "description": "列出可安装的 Skill 模板"},
                {"command": "install", "args": {"name": "git_helper"}, "description": "安装一个 Skill"},
                {"command": "uninstall", "args": {"name": "git_helper"}, "description": "卸载一个 Skill"},
                {"command": "update", "args": {"name": "git_helper"}, "description": "更新 Skill（可附带新代码）"},
                {"command": "info", "args": {"name": "git_helper"}, "description": "查看 Skill 详情"},
                {"command": "search", "args": {"query": "git"}, "description": "搜索 Skill"},
            ],
            "_notes": [
                "Skills 存放在 components/skills/ 目录中，与 components/*.py 隔离",
                "安装 Skill 后自动触发热重载，无需重启系统",
                "卸载 Skill 会同时删除文件并从组件注册表移除",
                "每个 Skill 本身是一个 BaseCell 子类（即一个微型组件）",
                "调用时必须带 command 字段指定子命令名",
            ],
            "_call_format": {
                "note": "多命令模式工具，每次调用必须带 command 字段",
                "example": {"command": "<子命令名>", "<param>": "<值>"},
                "or_query_help": f'{self.cell_name}.help(topic="<命令名>") 可查看某命令详情',
            },
        }

        if topic and topic in commands:
            hint_map = {
                "list_installed": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "hint": (
                        '调用示例: {"command":"list_installed","show_details":true}\n'
                        'show_details=true 时返回版本、来源、安装时间等信息'
                    ),
                },
                "list_available": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "hint": (
                        '调用示例: {"command":"list_available"} '
                        '或 {"command":"list_available","category":"code"}\n'
                        '可选分类: code / docs / web / data'
                    ),
                },
                "install": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "required_params": ["name"],
                    "optional_params": ["source", "content"],
                    "hint": (
                        '调用示例: {"command":"install","name":"git_helper"}\n'
                        '  - name (必填): Skill 名称\n'
                        '  - source (选填): 来源标记\n'
                        '  - content (选填): 自定义源代码（不提供则生成模板）'
                    ),
                },
                "uninstall": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "required_params": ["name"],
                    "hint": '调用示例: {"command":"uninstall","name":"git_helper"}',
                },
                "update": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "required_params": ["name"],
                    "optional_params": ["content"],
                    "hint": (
                        '调用示例: {"command":"update","name":"my_skill","content":"新代码..."}\n'
                        '不提供 content 则只更新元数据和时间戳'
                    ),
                },
                "info": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "optional_params": ["name"],
                    "hint": (
                        '调用示例: {"command":"info","name":"git_helper"}\n'
                        '不带 name 参数时返回 Skill 系统概览'
                    ),
                },
                "search": {
                    "focused_command": topic,
                    "command_description": commands[topic],
                    "required_params": ["query"],
                    "hint": '调用示例: {"command":"search","query":"git"}',
                },
            }
            return {**base_info, **hint_map.get(topic, {})}

        return base_info

    def on_load(self):
        """确保 skills 目录存在"""
        super().on_load()
        skills_dir = self._get_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[SkillInstaller] 就绪 | skills_dir=%s | 已安装=%d",
            skills_dir, len([p for p in skills_dir.glob("*.py") if not p.stem.startswith("_")]),
        )

    # ================================================================
    #  内部方法
    # ================================================================

    @staticmethod
    def _generate_skill_template(name: str, class_name: str) -> str:
        """生成标准 Skill 模板代码"""
        now = datetime.now().strftime("%Y-%m-%d")
        return f'''# -*- coding: utf-8 -*-
"""
{name} — Cellium Skill

创建时间: {now}
由 SkillInstaller 自动生成

[Skill 规范]
  - 继承 BaseCell
  - 定义 cell_name（小写，唯一标识）
  - 命令方法以 _cmd_ 开头，必须有 docstring
  - 提供 _cmd_help 方法供 LLM 查询用法
  - 文件位于 components/skills/ 下
"""

from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class {class_name}(BaseCell):
    """
    {name} — 能力扩展包
    """

    @property
    def cell_name(self) -> str:
        return "{name}"

    def _cmd_execute(self, input_data: str) -> Dict[str, Any]:
        """
        执行主功能
        
        Args:
            input_data: 输入数据
            
        Returns:
            {{"result": 处理结果}}
        """
        # TODO: 实现 {name} 的具体逻辑
        return {{"status": "ok", "message": "{name} 功能待实现"}}

    def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
        """查询 Skill 使用帮助"""
        cmds = self.get_commands()
        return {{
            "name": self.cell_name,
            "description": "{name} — Cellium Skill",
            "available_commands": cmds,
            "_notes": [
                "这是一个 Skill（插件式能力包），安装在 components/skills/ 下",
                "通过 skill_installer 工具管理（install/uninstall/update）",
            ],
        }}

    def on_load(self):
        """Skill 加载后的初始化"""
        super().on_load()
'''

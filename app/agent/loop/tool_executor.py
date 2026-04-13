# -*- coding: utf-8 -*-
"""
工具执行器 - 从 AgentLoop 中提取

职责：
  1. 工具描述自动生成（Jinja 风格模板引擎）
  2. Shell 命令中文描述生成
  3. 工具调用分发与执行
  4. 工具错误追踪
"""

import json
import logging
import os
import re
import string
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ================================================================
#  每轮注入的精简工具调用规范（不依赖 personality.md 的单次注入）
#  目的：让 LLM 在多轮对话中始终记得正确的工具调用格式
# ================================================================
TOOL_CALL_GUIDE = """
## §TOOL_CALL 工具调用规范

### §_intent 协议 [强制]
- 每次调用工具必须携带 `_intent` 字段
- 格式：`正在{动作}{对象}`
- 长度：15~25字中文
"""


class ToolDescriptionGenerator:
    """工具描述生成器 — 根据工具名和参数自动生成用户友好的中文操作描述"""

    # 模板定义：{变量} 从 arguments 中动态提取
    DESC_TEMPLATES = {
        "file": {
            "read":     "正在读取文件：{basename}",
            "write":    "正在写入文件：{basename}",
            "edit":     "正在编辑文件：{basename}",
            "create":   "正在创建项目：{base_dir}（{file_count} 个文件）",
            "delete":   "正在删除：{target}",
            "list":     "正在查看目录：{dir_path}",
            "exists":   "正在检查是否存在：{basename}",
            "mkdir":    "正在创建目录：{target}",
            "insight":  "{insight_desc}",
            "_default": "正在操作文件：{basename}",
        },
        "memory": {
            "search":   "正在搜索历史记忆：{query}",
            "store":    "正在保存记忆：{title}",
            "_default": "正在操作记忆：{command}",
        },
        "web_search": {
            "search":   "正在搜索：{query}",
            "close":    "正在关闭搜索浏览器",
            "help":     "正在查询搜索帮助",
            "_default": "正在搜索：{query}",
        },
        "web_fetch": {
            "read":            "正在阅读网页：{url_short}",
            "control":         "正在操控页面：{action}",
            "set_mode":        "正在切换浏览器模式为：{headless}",
            "get_screenshot":  "正在截图：{selector}",
            "find_qrcode":     "正在查找页面二维码",
            "close":           "正在关闭浏览器",
            "help":            "正在查询抓取帮助",
            "_default":        "正在获取网页内容：{url_short}",
        },
        "qq_files": {
            "download":    "正在从 QQ 下载文件",
            "send_file":   "正在发送文件到 QQ：{filename}",
            "send_image":  "正在发送图片到 QQ：{filename}",
            "list":        "正在列出 QQ 下载文件",
            "_default":    "正在处理 QQ 文件",
        },
        "_default":    "正在调用 {tool_name}：{param_value}",
    }

    @staticmethod
    def render_template(template: str, context: dict) -> str:
        """安全渲染描述模板，缺失变量用空字符串替代（不会抛 KeyError）"""
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""
        try:
            return string.Formatter().vformat(template, (), _SafeDict(context))
        except Exception:
            return template

    @staticmethod
    def extract_context(tool_name: str, arguments: dict) -> dict:
        """从 arguments 中提取模板变量"""
        ctx = {"tool_name": tool_name}

        path = (
            arguments.get("path")
            or arguments.get("dir_path")
            or arguments.get("base_dir")
            or arguments.get("filePath")
            or ""
        )
        ctx["basename"] = os.path.basename(path) if path else ""
        ctx["dir_path"] = path
        ctx["target"] = ctx["basename"] or path

        ctx["command"] = (arguments.get("command") or "").strip()
        if tool_name == "file":
            files_arg = arguments.get("files")
            if isinstance(files_arg, dict):
                ctx["file_count"] = len(files_arg)
            elif isinstance(files_arg, str):
                try:
                    parsed = json.loads(files_arg)
                    ctx["file_count"] = len(parsed) if isinstance(parsed, dict) else "?"
                except Exception:
                    ctx["file_count"] = "?"
            else:
                ctx["file_count"] = "?"
            ctx["base_dir"] = arguments.get("base_dir") or ""
            ctx["mode"] = arguments.get("mode") or "auto"
            ctx["query"] = (arguments.get("query") or "")[:30]
            if tool_name == "file" and ctx["command"] == "insight":
                mode = ctx["mode"]
                query = ctx["query"]
                basename = ctx["basename"]
                if query:
                    ctx["insight_desc"] = f"正在搜索 {basename} 中的：{query}"
                elif mode == "structure":
                    ctx["insight_desc"] = f"正在分析 {basename} 的代码结构"
                elif mode == "summary":
                    ctx["insight_desc"] = f"正在获取 {basename} 的摘要"
                else:
                    ctx["insight_desc"] = f"正在分析 {basename}（{mode}模式）"
            else:
                ctx["insight_desc"] = ""

        # Memory / Web Search 特有
        ctx["query"] = (arguments.get("query") or arguments.get("q") or arguments.get("keywords") or "")[:30]
        ctx["title"] = (arguments.get("title") or "")[:30]

        # Web 特有
        url = arguments.get("url") or ""
        ctx["url"] = url
        ctx["url_short"] = url[:50] if url else ""

        urls = arguments.get("urls")
        if isinstance(urls, list):
            ctx["url_count"] = len(urls)
        elif isinstance(urls, str):
            try:
                parsed = json.loads(urls)
                ctx["url_count"] = len(parsed) if isinstance(parsed, list) else "?"
            except Exception:
                ctx["url_count"] = "?"
        else:
            ctx["url_count"] = "?"

        # QQ Files 特有
        ctx["filename"] = os.path.basename(arguments.get("url") or arguments.get("file_path") or arguments.get("image_path") or "")
        ctx["target_id"] = arguments.get("target_id") or ""

        # Web 操作特有
        ctx["action"] = arguments.get("action") or ""
        ctx["selector"] = arguments.get("selector") or "页面"
        ctx["headless"] = "后台" if arguments.get("headless", True) else "可视化"

        # Shell 特有
        ctx["cmd"] = (arguments.get("command") or "").strip()
        ctx["cmd_first"] = ctx["cmd"].split()[0] if ctx["cmd"].split() else ""

        # ★ 组件工具通用参数提取（用于兜底描述生成）
        if not any([ctx["basename"], ctx["query"], ctx["title"], ctx["cmd"]]):
            for v in arguments.values():
                if isinstance(v, str) and len(v.strip()) > 2 and len(v.strip()) < 100:
                    ctx["param_value"] = v.strip()[:40]
                    break
                elif isinstance(v, dict) and v:
                    ctx["param_value"] = f"({len(v)}个文件/项)"
                    break

        return ctx

    @classmethod
    def generate(cls, tool_name: str, arguments: dict) -> str:
        """根据工具名和参数，使用模板生成用户友好的中文操作描述。

        模板优先级：
          0. arguments["_intent"]                     — LLM 提供的意图描述（最高优先级）
          1. _DESC_TEMPLATES[tool_name][sub_command]  — 精确匹配
          2. _DESC_TEMPLATES[tool_name]["_default"]   — 工具级默认
          3. _DESC_TEMPLATES["_default"]              — 全局兜底
          4. shell 走独立的命令解析器
        """
        # 0) 优先使用 LLM 提供的 _intent
        intent = arguments.get("_intent")
        if intent and isinstance(intent, str) and len(intent.strip()) > 0:
            return intent.strip()

        # Shell 走独立解析器（正则匹配，无法用简单模板覆盖）
        if tool_name == "shell":
            cmd = (arguments.get("command") or "").strip()
            return cls.describe_shell_command(cmd)

        context = cls.extract_context(tool_name, arguments)
        sub_cmd = context.get("command", "").lower()
        templates = cls.DESC_TEMPLATES

        # 1) 精确匹配：tool_name + sub_command
        if tool_name in templates and sub_cmd in templates[tool_name]:
            tpl = templates[tool_name][sub_cmd]
            return cls.render_template(tpl, context)

        # 2) 兼容旧版命名 (read_file / write_to_file / file_read / file_write)
        if tool_name in ("read_file", "file_read"):
            return cls.render_template(templates["file"]["read"], context)
        if tool_name in ("write_to_file", "file_write"):
            return cls.render_template(templates["file"]["write"], context)

        # 3) 工具级默认模板
        if tool_name in templates and "_default" in templates[tool_name]:
            return cls.render_template(templates[tool_name]["_default"], context)

        # 4) 直接以 tool_name 为 key 的顶级模板（必须是字符串模板）
        if tool_name in templates and isinstance(templates[tool_name], str):
            return cls.render_template(templates[tool_name], context)

        # 5) 全局兜底
        return cls.render_template(templates["_default"], context)

    @staticmethod
    def describe_shell_command(cmd: str) -> str:
        """解析 shell 命令并生成中文描述"""
        cmd_lower = cmd.lower().strip()

        # ── 文件/目录操作 ──
        write_match = re.search(r'(?:>|Out-File|Set-Content|Add-Content)[^"\']*(["\']?)([^"\'>]+?)(\1)\s*$', cmd)
        if write_match:
            filename = os.path.basename(write_match.group(2))
            return f"正在写入文件：{filename}"

        if re.search(r'(?:New-Item|mkdir|md |ni |touch)', cmd_lower):
            name_match = re.search(r'[-"]?\s*(?:Path\s*[= ]|Name\s*[= ])?(\w[\w.\-/\\\]*\.\w+|\w[\w.\-/\\]*)', cmd, re.IGNORECASE)
            name = os.path.basename(name_match.group(1)) if name_match else ""
            if 'dir' in cmd_lower or ('folder' in cmd_lower and 'file' not in cmd_lower) or '-ItemType Directory' in cmd:
                return f"正在创建目录：{name}" if name else "正在创建目录"
            return f"正在创建文件：{name}" if name else "正在创建文件"

        if re.search(r'^(?:cat |type |Get-Content|gc )', cmd_lower.strip()):
            path_match = re.search(r'(?:cat|type|Get-Content|gc)\s+([^\s|>]+)', cmd)
            path = path_match.group(1).strip('"\'') if path_match else ""
            return f"正在读取文件：{os.path.basename(path)}"

        if re.search(r'^(?:Remove-Item|rm |del )', cmd_lower.strip()):
            return "正在删除文件或目录"

        if re.search(r'^(?:Copy-Item|Move-Item|cp |mv |xcopy|robocopy)', cmd_lower.strip()):
            op = "复制" if any(k in cmd_lower for k in ['copy', 'cp ', 'xcopy', 'robocopy']) else "移动"
            return f"正在{op}文件"

        # ── 查看类操作 ──
        if re.match(r'^(?:ls |dir |Get-ChildItem|gci )', cmd_lower.strip()):
            path_match = re.search(r'(?:ls|dir|Get-ChildItem|gci)[^|]*?([A-Za-z]:[/\\][^>\s&&|]*|[/\\]\S+)', cmd)
            target = path_match.group(1) if path_match else ""
            if target:
                return f"正在查看目录：{target}"
            return "正在查看目录内容"

        if re.search(r'(?:Get-Process|tasklist|^ps\s)', cmd_lower):
            return "正在查看运行中的进程"

        if re.search(r'(?:env|systeminfo|ver |hostname|whoami)', cmd_lower):
            return "正在查看系统信息"

        # ── Python 执行 ──
        if re.search(r'^python\s|python3\s|py\s|pip\s|pip3\s', cmd_lower):
            if 'install' in cmd_lower:
                pkg_match = re.search(r'install\s+(.+?)(?:\s|$|>)', cmd_lower)
                pkg = pkg_match.group(1)[:40] if pkg_match else ""
                return f"正在安装 Python 包：{pkg}"
            if '--version' in cmd_lower or '-V' in cmd_lower:
                return "正在查看 Python 版本"
            script_match = re.search(r'^(?:python|python3|py)\s+(\S+\.py)', cmd_lower)
            if script_match:
                return f"正在运行脚本：{os.path.basename(script_match.group(1))}"
            return "正在执行 Python 命令"

        # ── Git 操作 ──
        if cmd_lower.startswith('git '):
            git_cmd = cmd[4:].strip().split()[0] if len(cmd) > 4 else ""
            git_actions = {
                'clone': '克隆代码仓库', 'pull': '拉取最新代码', 'push': '推送代码到远程',
                'commit': '提交代码更改', 'add': '暂存文件', 'status': '查看 Git 状态',
                'log': '查看提交历史', 'diff': '查看代码差异', 'branch': '管理分支',
                'checkout': '切换分支/版本', 'init': '初始化仓库', 'merge': '合并分支',
            }
            action = git_actions.get(git_cmd, f"执行 Git {git_cmd}")
            return f"正在{action}"

        # ── 网络 / 安装 ──
        if re.search(r'^npm\s|yarn\s|pnpm\s', cmd_lower):
            return "正在执行 Node.js 包管理命令"
        if re.search(r'^(?:curl |wget |Invoke-WebRequest)', cmd_lower):
            return "正在请求网络资源"

        # 默认：提取命令关键词
        first_word = cmd.split()[0] if cmd.split() else "执行命令"
        return f"正在执行：{first_word}"


class ToolExecutor:
    """工具执行器 — 负责查找、调度和追踪工具调用"""

    def __init__(self, tools: Dict[str, Any], builtin_tools: Dict[str, Any]):
        self.tools = tools
        self._builtin_tools = builtin_tools
        self._error_tracker: Dict[str, int] = {}
        self._error_threshold: int = 3

    def refresh_tools(self, tools: Dict[str, Any]):
        """更新工具表（热插拔后调用）"""
        self.tools = tools

    async def execute(self, tool_call) -> Dict[str, Any]:
        """执行工具调用"""
        from app.core.util.component_tool_registry import get_component_tool_registry

        tool_name = tool_call.name
        arguments = tool_call.arguments

        # ★ 确保工具表是最新的
        try:
            registry = get_component_tool_registry()
            component_tools = registry.get_component_tools()
            self.tools = {**component_tools, **self._builtin_tools}
        except Exception as e:
            logger.warning("[ToolExecutor] 工具刷新失败: %s", e)

        logger.info(
            "[ToolExecutor] execute | name=%s | available=%s",
            tool_name, list(self.tools.keys()),
        )

        if tool_name in self.tools:
            tool_instance = self.tools[tool_name]
            if hasattr(tool_instance, "execute"):
                return tool_instance.execute(arguments)
            elif callable(tool_instance):
                return tool_instance(**arguments)

        error_msg = f"Unknown tool: {tool_name}. Available: {list(self.tools.keys())}"
        logger.error("[ToolExecutor] %s", error_msg)
        return {"error": error_msg}

    def track_result(self, tool_name: str, result: Dict[str, Any]) -> None:
        """追踪工具调用的成功/失败状态"""
        is_error = (
            isinstance(result, dict) and
            (result.get("error") or result.get("status") == "error" or "Command '" in str(result.get("", "")))
        )

        if is_error:
            self._error_tracker[tool_name] = self._error_tracker.get(tool_name, 0) + 1
            count = self._error_tracker[tool_name]
            if count < self._error_threshold:
                logger.info("[ToolErrorTracker] %s 第%d次失败", tool_name, count)
            else:
                logger.warning(
                    "[ToolErrorTracker] %s 连续 %d 次失败 → 下次将自动注入使用指南",
                    tool_name, count,
                )
        else:
            old_count = self._error_tracker.pop(tool_name, None)
            if old_count and old_count > 0:
                logger.info(
                    "[ToolErrorTracker] %s 成功 OK (此前连续失败 %d 次)",
                    tool_name, old_count,
                )

    def get_failing_tools(self) -> Dict[str, int]:
        """获取达到错误阈值的工具列表（用于自动注入帮助）"""
        return {
            tname: count for tname, count in self._error_tracker.items()
            if count >= self._error_threshold
        }

# -*- coding: utf-8 -*-
"""
工具执行器 - 从 AgentLoop 中提取

职责：
  1. 工具描述自动生成
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


class ToolDescriptionGenerator:
    """工具描述生成器 — 根据工具名和参数自动生成用户友好的中文操作描述"""

    DESC_TEMPLATES = {
        "read": {
            "_default": "{read_desc}",
        },
        "edit": {
            "_default": "{edit_desc}",
        },
        "grep": {
            "_default": "正在搜索：{grep_pattern}",
        },
        "file": {
            "fs":       "{fs_desc}",
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
        "weixin_files": {
            "download":    "正在从微信下载文件",
            "send_file":   "正在发送文件到微信：{filename}",
            "send_image":  "正在发送图片到微信：{filename}",
            "send_video":  "正在发送视频到微信：{filename}",
            "list":        "正在列出微信下载文件",
            "_default":    "正在处理微信文件",
        },
        "_default":    "正在调用 {tool_name}：{param_value}",
    }

    @staticmethod
    def render_template(template: str, context: dict) -> str:
        class _SafeDict(dict):
            def __missing__(self, key):
                return ""
        try:
            return string.Formatter().vformat(template, (), _SafeDict(context))
        except Exception:
            return template

    @staticmethod
    def extract_context(tool_name: str, arguments: dict) -> dict:
        ctx = {"tool_name": tool_name}

        if not isinstance(arguments, dict):
            return ctx

        path = (
            arguments.get("path")
            or arguments.get("file_path")
            or arguments.get("dir_path")
            or arguments.get("base_dir")
            or arguments.get("filePath")
            or ""
        )
        ctx["basename"] = os.path.basename(path) if path else ""
        ctx["dir_path"] = path
        ctx["target"] = ctx["basename"] or path

        ctx["command"] = (arguments.get("command") or "").strip()

        if tool_name == "read":
            needle = arguments.get("needle") or ""
            target = arguments.get("target") or ""
            offset = arguments.get("offset")
            limit = arguments.get("limit")
            if needle:
                ctx["read_desc"] = f"正在定位 {ctx['basename']} 中的匹配位置"
            elif target:
                ctx["read_desc"] = f"正在查找 {ctx['basename']} 中的：{target[:30]}"
            elif offset is not None and limit is not None:
                ctx["read_desc"] = f"正在读取 {ctx['basename']} 第{offset}-{offset + limit}行"
            elif offset is not None:
                ctx["read_desc"] = f"正在读取 {ctx['basename']} 第{offset}行起"
            else:
                ctx["read_desc"] = f"正在读取文件：{ctx['basename']}"

        elif tool_name == "edit":
            replace_all = arguments.get("replace_all")
            old_str = (arguments.get("old_string") or "")[:40]
            if replace_all:
                ctx["edit_desc"] = f"正在批量替换 {ctx['basename']} 中的文本"
            elif old_str:
                ctx["edit_desc"] = f"正在编辑 {ctx['basename']} 中的文本"
            else:
                ctx["edit_desc"] = f"正在编辑文件：{ctx['basename']}"

        elif tool_name == "grep":
            pattern = (arguments.get("pattern") or arguments.get("query") or "")
            path_arg = arguments.get("path") or ""
            glob_arg = arguments.get("glob") or ""
            head_limit = arguments.get("head_limit", "")
            if pattern:
                ctx["grep_pattern"] = pattern[:40]
            if glob_arg:
                ctx["grep_desc"] = f"正在搜索 {glob_arg} 匹配的内容：{pattern[:40]}" if pattern else f"正在搜索 {glob_arg} 匹配的内容"
            else:
                ctx["grep_desc"] = f"正在搜索：{pattern[:40]}" if pattern else f"正在搜索代码"

        elif tool_name == "file":
            cmd = ctx["command"]

            if cmd == "fs":
                action = arguments.get("action", "list")
                if action == "list":
                    ctx["fs_desc"] = f"正在查看目录：{ctx['dir_path'] or ctx['basename']}"
                elif action == "mkdir":
                    ctx["fs_desc"] = f"正在创建目录：{ctx['target']}"
                elif action == "delete":
                    ctx["fs_desc"] = f"正在删除：{ctx['target']}"
                elif action == "exists":
                    ctx["fs_desc"] = f"正在检查：{ctx['target']}"
                elif action == "create":
                    files_arg = arguments.get("files")
                    if isinstance(files_arg, (dict, list)):
                        count = len(files_arg)
                    elif isinstance(files_arg, str):
                        try:
                            parsed = json.loads(files_arg)
                            count = len(parsed) if isinstance(parsed, (dict, list)) else "?"
                        except Exception:
                            count = "?"
                    else:
                        count = "?"
                    base = arguments.get("path") or ""
                    ctx["fs_desc"] = f"正在创建项目：{os.path.basename(base)}（{count} 个文件）"
                else:
                    ctx["fs_desc"] = "正在操作文件系统"

            elif cmd == "insight":
                insight_mode = arguments.get("mode", "grep")
                query = (arguments.get("query") or "")[:30]
                if insight_mode == "grep":
                    ctx["insight_desc"] = f"正在搜索：{query}"
                elif insight_mode == "structure":
                    ctx["insight_desc"] = f"正在分析 {ctx['basename']} 的结构"
                elif insight_mode == "symbol":
                    ctx["insight_desc"] = f"正在搜索符号：{query}"
                elif insight_mode == "files":
                    pattern = (arguments.get("pattern") or arguments.get("query") or "*")[:30]
                    ctx["insight_desc"] = f"正在查找文件：{pattern}"
                else:
                    ctx["insight_desc"] = "正在探索工程"

            else:
                ctx["fs_desc"] = "正在操作文件系统"
                ctx["insight_desc"] = ""

        # Memory / Web Search
        ctx["query"] = (arguments.get("query") or arguments.get("q") or arguments.get("keywords") or "")[:30]
        ctx["title"] = (arguments.get("title") or "")[:30]

        # Web
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

        # QQ/WeChat Files
        ctx["filename"] = os.path.basename(arguments.get("url") or arguments.get("file_path") or arguments.get("image_path") or "")
        ctx["target_id"] = arguments.get("target_id") or ""

        # Web ops
        ctx["action"] = arguments.get("action") or ""
        ctx["selector"] = arguments.get("selector") or "页面"
        ctx["headless"] = "后台" if arguments.get("headless", True) else "可视化"

        # Shell
        ctx["cmd"] = (arguments.get("command") or "").strip()
        ctx["cmd_first"] = ctx["cmd"].split()[0] if ctx["cmd"].split() else ""

        # Component tools fallback
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
        if not isinstance(arguments, dict):
            arguments = {}

        intent = arguments.get("_intent")
        if intent and isinstance(intent, str) and len(intent.strip()) > 0:
            return intent.strip()

        if tool_name == "shell":
            cmd = (arguments.get("cmd") or "").strip()
            argv = arguments.get("argv")
            if argv and isinstance(argv, list) and len(argv) > 0:
                cmd = " ".join(argv)
            sub_cmd = (arguments.get("command") or "").strip()
            if sub_cmd == "run" and cmd:
                return cls.describe_shell_command(cmd)
            elif sub_cmd == "run":
                return "正在执行命令"
            elif sub_cmd == "list":
                return "正在查看后台任务列表"
            elif sub_cmd == "output":
                task_id = arguments.get("task_id", "")
                return f"正在获取任务输出：{task_id}"
            elif sub_cmd == "kill":
                task_id = arguments.get("task_id", "")
                return f"正在终止任务：{task_id}"
            elif cmd:
                return cls.describe_shell_command(cmd)
            return "正在执行 shell 命令"

        context = cls.extract_context(tool_name, arguments)
        sub_cmd = context.get("command", "").lower()
        templates = cls.DESC_TEMPLATES

        if tool_name in templates and sub_cmd and sub_cmd in templates[tool_name]:
            return cls.render_template(templates[tool_name][sub_cmd], context)

        if tool_name in templates and "_default" in templates[tool_name]:
            return cls.render_template(templates[tool_name]["_default"], context)

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
            path_match = re.search(r'(?:ls|dir|Get-ChildItem|gci)[^|]*?([A-Za-z]:[/\\][^>\s&|]*|[/\\]\S+)', cmd)
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

        # ── 文本处理命令 ──
        text_cmds = {
            'wc': '统计文件信息',
            'grep': '搜索文本内容',
            'egrep': '搜索文本内容',
            'fgrep': '搜索固定字符串',
            'sed': '处理文本',
            'awk': '处理文本数据',
            'head': '查看文件开头',
            'tail': '查看文件末尾',
            'sort': '排序文本',
            'uniq': '去除重复行',
            'cut': '提取文本列',
            'tr': '转换字符',
            'diff': '比较文件差异',
            'find': '查找文件',
            'xargs': '批量执行命令',
            'echo': '输出文本',
            'printf': '格式化输出',
        }
        first_cmd = cmd.split()[0] if cmd.split() else ""
        first_cmd_lower = first_cmd.lower()
        if first_cmd_lower in text_cmds:
            file_match = re.search(r'\s+(\S+)\s*$', cmd)
            if file_match:
                target = os.path.basename(file_match.group(1))
                return f"正在{text_cmds[first_cmd_lower]}：{target}"
            return f"正在{text_cmds[first_cmd_lower]}"

        # ── 系统信息命令 ──
        sys_cmds = {
            'df': '查看磁盘空间',
            'du': '查看目录大小',
            'free': '查看内存使用',
            'top': '查看进程状态',
            'htop': '查看进程状态',
            'ps': '查看进程',
            'kill': '终止进程',
            'killall': '终止进程',
            'chmod': '修改文件权限',
            'chown': '修改文件所有者',
            'ln': '创建链接',
            'tar': '压缩/解压文件',
            'zip': '压缩文件',
            'unzip': '解压文件',
            'gzip': '压缩文件',
            'gunzip': '解压文件',
            'date': '查看日期时间',
            'cal': '查看日历',
            'uptime': '查看系统运行时间',
            'ipconfig': '查看网络配置',
            'ifconfig': '查看网络配置',
            'netstat': '查看网络连接',
            'ping': '测试网络连通',
            'nslookup': '查询 DNS',
            'dig': '查询 DNS',
            'traceroute': '追踪网络路由',
            'export': '设置环境变量',
            'source': '加载配置文件',
            'alias': '设置命令别名',
            'history': '查看命令历史',
            'which': '查找命令路径',
            'whereis': '查找程序路径',
            'man': '查看帮助文档',
            'help': '查看帮助',
        }
        if first_cmd_lower in sys_cmds:
            return f"正在{sys_cmds[first_cmd_lower]}"

        # ── 带管道的命令 ──
        if '|' in cmd:
            parts = cmd.split('|')
            first_part = parts[0].strip().split()[0] if parts[0].strip() else ""
            if first_part.lower() in text_cmds:
                return f"正在{text_cmds[first_part.lower()]}"
            if first_part.lower() in sys_cmds:
                return f"正在{sys_cmds[first_part.lower()]}"
            return "正在执行管道命令"

        # 默认：提取命令关键词
        first_word = cmd.split()[0] if cmd.split() else "执行命令"
        return f"正在执行：{first_word}"


class ToolExecutor:
    """工具执行器 — 负责查找、调度和追踪工具调用"""

    def __init__(self, tools: Dict[str, Any], builtin_tools: Dict[str, Any], on_tools_changed=None):
        self.tools = tools
        self._builtin_tools = builtin_tools
        self._error_tracker: Dict[str, int] = {}
        self._error_threshold: int = 3
        self._on_tools_changed = on_tools_changed  # 工具变化时的回调

    def refresh_tools(self, tools: Dict[str, Any]):
        """更新工具表（热插拔后调用）"""
        self.tools = tools

    async def execute(self, tool_call, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工具调用（异步包装，避免阻塞事件循环）
        
        Args:
            tool_call: 工具调用信息
            session_id: 当前会话 ID（可选）
            platform_context: 平台上下文信息（可选，包含 target_id 等）
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from app.core.util.component_tool_registry import get_component_tool_registry

        tool_name = tool_call.name
        arguments = tool_call.arguments

        try:
            registry = get_component_tool_registry()
            component_tools = registry.get_component_tools()
            new_tools = {**component_tools, **self._builtin_tools}
            if set(new_tools.keys()) != set(self.tools.keys()):
                self.tools = new_tools
                if self._on_tools_changed:
                    self._on_tools_changed(self.tools)
                logger.info("[ToolExecutor] 工具列表已更新: %d 个工具", len(self.tools))
        except Exception as e:
            logger.warning("[ToolExecutor] 工具刷新失败: %s", e)

        logger.info(
            "[ToolExecutor] execute | name=%s | session=%s | available=%s",
            tool_name, session_id or "N/A", list(self.tools.keys()),
        )

        if tool_name not in self.tools:
            error_msg = f"Unknown tool: {tool_name}. Available: {list(self.tools.keys())}"
            logger.error("[ToolExecutor] %s", error_msg)
            return {"error": error_msg}

        tool_instance = self.tools[tool_name]

        def _run_tool():
            if hasattr(tool_instance, "execute_with_context"):
                return tool_instance.execute_with_context(arguments, session_id=session_id, platform_context=platform_context)
            elif hasattr(tool_instance, "execute"):
                return tool_instance.execute(arguments)
            elif callable(tool_instance):
                return tool_instance(**arguments)
            return {"error": f"Tool {tool_name} is not callable"}

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _run_tool)
            return result
        except Exception as e:
            logger.error("[ToolExecutor] 工具执行失败: %s | error=%s", tool_name, e)
            return {"error": str(e)}

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

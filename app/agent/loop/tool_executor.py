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

from app.core.util.component_tool_registry import get_component_tool_registry

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
            "update":   "正在更新记忆：{title}",
            "list":     "正在查看记忆列表",
            "delete":   "正在删除记忆",
            "forget":   "正在遗忘记忆：{query}",
            "merge":    "正在合并记忆",
            "profile":  "正在读取用户画像",
            "list_genes":        "正在查看 Gene 列表",
            "get_gene":          "正在查看 Gene 详情",
            "read_archive":      "正在读取归档对话",
            "set_embedding":     "正在配置向量模型",
            "get_embedding_status":             "正在查看向量索引状态",
            "get_embedding_migration_status":   "正在查看向量迁移状态",
            "start_embedding_migration":        "正在启动向量迁移",
            "_default": "正在操作记忆：{command}",
        },
        "web_search": {
            "search":   "正在搜索：{query}",
            "resolve":  "正在解析跳转链接",
            "engines":  "正在查看搜索引擎状态",
            "close":    "正在关闭搜索浏览器",
            "help":     "正在查询搜索帮助",
            "_default": "正在搜索：{query}",
        },
        "web_fetch": {
            "read":            "正在阅读网页：{url_short}",
            "fetch":           "正在获取网页内容：{url_short}",
            "fetch_many":      "正在并行抓取 {url_count} 个网页",
            "insight":         "正在分析网页：{query}",
            "get_viewport_text": "正在读取页面可见文本",
            "find_in_page":    "正在页面内查找：{keyword_text}",
            "find_button":     "正在查找页面按钮：{keyword_text}",
            "js_action":       "正在操作页面元素：{action}",
            "execute_script":  "正在执行页面脚本",
            "get_element_tree": "正在读取页面元素树",
            "save_cookies":    "正在保存页面 Cookie",
            "load_cookies":    "正在加载页面 Cookie",
            "network":         "正在抓取页面网络请求",
            "replay":          "正在重放接口请求",
            "ws":              "正在监听页面 WebSocket",
            "mock":            "正在注入接口 Mock 规则",
            "intercept":       "正在注入请求改写规则",
            "mocks":           "正在查看拦截规则",
            "clear_mocks":     "正在清除拦截规则",
            "export_har":      "正在导出 HAR 抓包文件",
            "set_block":       "正在屏蔽图片等资源加速加载",
            "upload":          "正在上传文件到页面",
            "download":        "正在下载文件",
            "alert":           "正在处理页面弹窗",
            "console":         "正在读取页面控制台日志",
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
            "send_voice":  "正在发送语音到 QQ：{filename}",
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
        "config": {
            "help":             "正在查看配置工具帮助",
            "version":          "正在查看 Agent 版本",
            "list_models":      "正在查看可用模型列表",
            "add_model":        "正在添加模型：{model_name}",
            "switch_model":     "正在切换模型到：{model_name}",
            "enable_intent":    "{intent_action}意图感知",
            "set_intent_model": "正在配置意图感知模型：{model}",
            "list_channels":    "正在查看外部通道配置",
            "set_telegram":     "正在配置 Telegram 通道",
            "set_qq":           "正在配置 QQ 通道",
            "set_feishu":       "正在配置飞书通道",
            "enable_channel":   "正在{toggle_action}外部通道：{channel}",
            "set_show_tool_cards":     "正在{toggle_action}工具卡片展示：{channel}",
            "set_show_thinking_plan":  "正在{toggle_action}执行计划展示：{channel}",
            "_default":         "正在管理配置：{command}",
        },
        "sub_agent": {
            "parallel":  "正在并行运行 {task_count} 个子 Agent{agent_suffix}",
            "spawn":     "正在后台运行 {task_count} 个子 Agent{agent_suffix}",
            "list":      "正在查看子 Agent 列表",
            "status":    "正在查看子 Agent 状态{agent_suffix}",
            "results":   "正在读取子 Agent 结论{agent_suffix}",
            "cancel":    "正在取消子 Agent{agent_suffix}",
            "help":      "正在查看子 Agent 帮助",
            "_default":  "正在调度子 Agent{agent_suffix}",
        },
        "session_agent": {
            "send":      "正在把任务委托给会话{session_suffix}",
            "status":    "正在查看会话委托状态{session_suffix}",
            "results":   "正在读取会话委托结果{session_suffix}",
            "cancel":    "正在取消会话委托{session_suffix}",
            "list":      "正在查看会话委托列表",
            "sessions":  "正在查看会话列表{keyword_suffix}",
            "rename":    "正在给会话命名{session_suffix}",
            "help":      "正在查看会话委托帮助",
            "_default":  "正在处理会话委托{session_suffix}",
        },
        "ls": {
            "_default":  "正在列出目录：{dir_label}",
        },
        "glob": {
            "_default":  "正在查找文件{glob_suffix}",
        },
        "feishu_files": {
            "download":          "正在从飞书下载文件{file_suffix}",
            "download_pending":  "正在检查飞书待下载文件",
            "send_file":         "正在发送文件到飞书{file_suffix}",
            "send_image":        "正在发送图片到飞书{file_suffix}",
            "list":              "正在列出飞书下载文件",
            "_default":          "正在处理飞书文件",
        },
        "telegram_files": {
            "download":     "正在从 Telegram 下载文件{file_suffix}",
            "send_file":    "正在发送文件到 Telegram{file_suffix}",
            "send_image":   "正在发送图片到 Telegram{file_suffix}",
            "list":         "正在列出 Telegram 下载文件",
            "_default":     "正在处理 Telegram 文件",
        },
        "scheduler": {
            "create_interval": "正在创建间隔定时任务{task_suffix}",
            "create_daily":    "正在创建每日定时任务{task_suffix}",
            "create_weekly":   "正在创建每周定时任务{task_suffix}",
            "list":            "正在查看定时任务列表",
            "delete":          "正在删除定时任务{task_id_suffix}",
            "enable":          "正在启用定时任务{task_id_suffix}",
            "disable":         "正在停用定时任务{task_id_suffix}",
            "run_now":         "正在立即执行定时任务{task_id_suffix}",
            "_default":        "正在管理定时任务：{command}",
        },
        "skill_installer": {
            "install":       "正在安装 Skill{skill_suffix}",
            "uninstall":     "正在卸载 Skill{skill_suffix}",
            "update":        "正在更新 Skill{skill_suffix}",
            "refresh_index": "正在刷新 Skill 索引",
            "_default":      "正在管理 Skill：{command}",
        },
        "skill_manager": {
            "list":      "正在查看 Skill 列表",
            "get_info":  "正在查看 Skill 详情{skill_suffix}",
            "search":    "正在搜索 Skill：{query}",
            "_default":  "正在查询 Skill：{command}",
        },
        "component": {
            "generate":         "正在生成组件{component_suffix}",
            "generate_package": "正在生成组件包{component_suffix}",
            "list":             "正在查看组件列表",
            "info":             "正在查看组件详情{component_suffix}",
            "template":         "正在获取组件模板",
            "reload":           "正在热重载组件",
            "cleanup":          "正在清理组件缓存",
            "dir":              "正在获取组件目录",
            "sandbox_list":     "正在查看免沙箱白名单",
            "sandbox_add":      "正在把组件加入免沙箱白名单{component_suffix}",
            "sandbox_remove":   "正在把组件移出免沙箱白名单{component_suffix}",
            "install_local":    "正在安装本地组件：{basename}",
            "market_search":    "正在组件市场搜索：{keyword_text}",
            "market_info":      "正在查看市场组件{component_suffix}",
            "market_install":   "正在从组件市场安装{component_suffix}",
            "help":             "正在查看组件构建帮助",
            "_default":         "正在管理组件：{command}",
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
    def _clean(text: str) -> str:
        """去掉空参数导致的尾部冒号/空格"""
        return text.rstrip("：:，, ")

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
                action = arguments.get("action", "")
                if action == "mkdir":
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
                else:
                    ctx["insight_desc"] = "正在探索工程"

            else:
                ctx["fs_desc"] = "正在操作文件系统"
                ctx["insight_desc"] = ""

        # Config tool
        if tool_name == "config":
            ctx["model_name"] = arguments.get("name") or arguments.get("model_name") or ""
            ctx["model"] = arguments.get("model") or ""
            enabled = arguments.get("enabled")
            if enabled is True:
                ctx["intent_action"] = "正在开启"
            elif enabled is False:
                ctx["intent_action"] = "正在关闭"
            else:
                ctx["intent_action"] = "正在查询"
            ctx["toggle_action"] = (
                "开启" if enabled is True else "关闭" if enabled is False else "调整"
            )
            ctx["channel"] = arguments.get("channel_name") or ""

        # Component builder（工具名是 component，文件为 components/component_builder.py）
        if tool_name == "component":
            cname = str(arguments.get("name") or arguments.get("slug") or "")[:40]
            ctx["component_suffix"] = f"：{cname}" if cname else ""

        # Memory / Web Search
        ctx["query"] = (arguments.get("query") or arguments.get("q") or arguments.get("keywords") or "")[:30]
        ctx["title"] = (arguments.get("title") or "")[:30]
        ctx["keyword_text"] = str(arguments.get("keyword") or "")[:30]

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

        # 平台文件（QQ/微信/飞书/Telegram）
        ctx["filename"] = os.path.basename(
            arguments.get("filename") or arguments.get("url")
            or arguments.get("file_path") or arguments.get("image_path") or ""
        )
        ctx["file_suffix"] = f"：{ctx['filename']}" if ctx["filename"] else ""
        ctx["target_id"] = arguments.get("target_id") or ""

        # Web ops
        ctx["action"] = arguments.get("action") or ""
        ctx["selector"] = arguments.get("selector") or "页面"
        ctx["headless"] = "后台" if arguments.get("headless", True) else "可视化"

        # Shell
        ctx["cmd"] = (arguments.get("command") or "").strip()
        ctx["cmd_first"] = ctx["cmd"].split()[0] if ctx["cmd"].split() else ""

        # Sub Agent
        if tool_name == "sub_agent":
            tasks = arguments.get("tasks")
            if isinstance(tasks, str):
                try:
                    tasks = json.loads(tasks)
                except Exception:
                    tasks = None
            names = []
            if isinstance(tasks, list):
                ctx["task_count"] = len(tasks)
                names = [str(t.get("name")) for t in tasks if isinstance(t, dict) and t.get("name")]
            elif isinstance(tasks, dict):
                ctx["task_count"] = 1
                if tasks.get("name"):
                    names = [str(tasks["name"])]
            elif (arguments.get("task") or "").strip():
                ctx["task_count"] = 1
            else:
                ctx["task_count"] = 0
            single = str(arguments.get("name") or "")
            if single and not names:
                names = [single]
            ctx["agent_names"] = ", ".join([n for n in names if n])[:60]
            ctx["agent_suffix"] = f"：{ctx['agent_names']}" if ctx["agent_names"] else ""

        # Session Agent
        if tool_name == "session_agent":
            # status/results/cancel 允许只传 task_id；send 新建会话时可用 title 兜底
            target = str(
                arguments.get("target") or arguments.get("task_id") or arguments.get("title") or ""
            )
            ctx["target"] = target
            ctx["session_suffix"] = f"：{target}" if target else ""
            keyword = str(arguments.get("keyword") or "")[:40]
            ctx["keyword_suffix"] = f"：{keyword}" if keyword else ""

        # ls / glob
        ctx["dir_label"] = ctx["basename"] or "当前目录"
        glob_pattern = (arguments.get("pattern") or "")[:60]
        ctx["glob_suffix"] = f"：{glob_pattern}" if glob_pattern else ""

        # Skill / 定时任务
        skill_name = str(arguments.get("skill_name") or arguments.get("name") or "")[:40]
        ctx["skill_suffix"] = f"：{skill_name}" if skill_name else ""
        task_name = str(arguments.get("name") or "")[:40]
        ctx["task_suffix"] = f"：{task_name}" if task_name else ""
        task_id = str(arguments.get("task_id") or "")
        ctx["task_id_suffix"] = f"：{task_id}" if task_id else ""

        # Component tools fallback：优先用命令，其次查询词、标题、首个短参数
        if not ctx.get("param_value"):
            for key in ("cmd", "query", "title"):
                if ctx.get(key):
                    ctx["param_value"] = str(ctx[key])[:40]
                    break
            else:
                for v in arguments.values():
                    if isinstance(v, str) and 2 < len(v.strip()) < 100:
                        ctx["param_value"] = v.strip()[:40]
                        break
                    if isinstance(v, dict) and v:
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
            cmd_value = arguments.get("cmd", "")
            if isinstance(cmd_value, list):
                cmd = " ".join(cmd_value)
            else:
                cmd = (cmd_value or "").strip()
            sub_cmd = (arguments.get("command") or "").strip()
            if sub_cmd == "run" and cmd:
                return cls.describe_shell_command(cmd, cmd_value if isinstance(cmd_value, list) else None)
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
            elif sub_cmd == "session":
                action = (arguments.get("action") or "").strip().lower()
                if action == "start":
                    return f"正在启动持久会话：{cmd}" if cmd else "正在启动持久会话"
                elif action == "send":
                    sid = arguments.get("session_id", "")
                    txt = (arguments.get("text") or "")[:40]
                    return f"正在向会话 {sid} 发送：{txt}"
                elif action == "output":
                    sid = arguments.get("session_id", "")
                    return f"正在读取会话 {sid} 输出"
                elif action == "close":
                    sid = arguments.get("session_id", "")
                    return f"正在关闭会话 {sid}"
                elif action == "list":
                    return "正在查看持久会话列表"
                return "正在操作持久会话"
            elif cmd:
                return cls.describe_shell_command(cmd, cmd_value if isinstance(cmd_value, list) else None)
            return "正在执行 shell 命令"

        context = cls.extract_context(tool_name, arguments)
        sub_cmd = context.get("command", "").lower()
        templates = cls.DESC_TEMPLATES

        # 缺任务明细时不再拼 0 个，回落到工具级兜底
        if tool_name == "sub_agent" and sub_cmd in ("spawn", "parallel") and not context.get("task_count"):
            return cls._clean(cls.render_template(templates["sub_agent"]["_default"], context))

        if tool_name in templates and sub_cmd and sub_cmd in templates[tool_name]:
            return cls._clean(cls.render_template(templates[tool_name][sub_cmd], context))

        if tool_name in templates and "_default" in templates[tool_name]:
            return cls._clean(cls.render_template(templates[tool_name]["_default"], context))

        return cls._clean(cls.render_template(templates["_default"], context))

    @staticmethod
    def describe_shell_command(cmd: str, argv: list = None) -> str:
        """解析 shell 命令并生成中文描述
        
        Args:
            cmd: 命令字符串
            argv: 命令参数数组（如果提供，优先使用数组判断）
        """
        if argv and isinstance(argv, list) and len(argv) > 0:
            first_arg = argv[0].lower() if argv[0] else ""
            if first_arg in ("python", "python3", "py"):
                if any("install" in (arg or "").lower() for arg in argv):
                    pkg = argv[-1] if len(argv) > 2 and argv[-1] and not argv[-1].startswith("-") else ""
                    return f"正在安装 Python 包：{pkg}" if pkg else "正在安装 Python 包"
                if any(arg in argv for arg in ["--version", "-V"]):
                    return "正在查看 Python 版本"
                script = next((arg for arg in argv[1:] if arg and arg.endswith(".py")), "")
                if script:
                    return f"正在运行脚本：{os.path.basename(script)}"
                return "正在执行 Python 命令"
            
            if first_arg == "git" and len(argv) > 1:
                git_actions = {
                    'clone': '克隆代码仓库', 'pull': '拉取最新代码', 'push': '推送代码到远程',
                    'commit': '提交代码更改', 'add': '暂存文件', 'status': '查看 Git 状态',
                    'log': '查看提交历史', 'diff': '查看代码差异', 'branch': '管理分支',
                    'checkout': '切换分支/版本', 'init': '初始化仓库', 'merge': '合并分支',
                }
                git_cmd = argv[1].lower()
                action = git_actions.get(git_cmd, f"执行 Git {git_cmd}")
                return f"正在{action}"
            
            if first_arg in ("npm", "yarn", "pnpm"):
                return "正在执行 Node.js 包管理命令"
            
            text_cmds = {
                'grep': '搜索文本内容', 'egrep': '搜索文本内容', 'fgrep': '搜索固定字符串',
                'sed': '处理文本', 'awk': '处理文本数据', 'find': '查找文件',
                'head': '查看文件开头', 'tail': '查看文件末尾', 'sort': '排序文本',
                'echo': '输出文本', 'cat': '查看文件内容', 'ls': '查看目录内容',
                'dir': '查看目录内容',
            }
            if first_arg in text_cmds:
                return f"正在{text_cmds[first_arg]}"
            
            return f"正在执行：{first_arg}"
        
        cmd_lower = cmd.lower().strip()
        first_cmd = cmd.split()[0] if cmd.split() else ""
        first_cmd_lower = first_cmd.lower()

        write_match = re.search(r'(?:>\s*|Out-File\s+|Set-Content\s+|Add-Content\s+)(["\']?)([^\s"\'>]+?)(\1)\s*$', cmd)
        if write_match:
            filename = write_match.group(2)
            if filename and not filename.isdigit() and len(filename) > 1:
                filename = os.path.basename(filename)
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

        # ── 进程管理命令 ──
        proc_cmds = {
            'nohup': '后台运行命令', 'screen': '创建终端会话',
            'tmux': '创建终端复用会话', 'jobs': '查看后台任务',
            'fg': '将后台任务调至前台', 'bg': '将任务放入后台',
            'nice': '设置进程优先级', 'renice': '调整进程优先级',
            'watch': '周期性执行命令', 'at': '定时执行任务',
        }
        if first_cmd_lower in proc_cmds:
            return f"正在{proc_cmds[first_cmd_lower]}"

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

        # ── Docker 命令 ──
        if first_cmd_lower == 'docker' and len(cmd.split()) > 1:
            docker_actions = {
                'ps': '查看容器列表', 'images': '查看镜像列表', 'build': '构建镜像',
                'run': '运行容器', 'exec': '在容器中执行命令', 'stop': '停止容器',
                'start': '启动容器', 'restart': '重启容器', 'rm': '删除容器',
                'rmi': '删除镜像', 'pull': '拉取镜像', 'push': '推送镜像',
                'logs': '查看容器日志', 'inspect': '检查容器详情', 'compose': '管理多容器应用',
            }
            docker_cmd = cmd.split()[1].lower() if len(cmd.split()) > 1 else ""
            action = docker_actions.get(docker_cmd, f"Docker {docker_cmd}")
            return f"正在{action}"

        # ── 数据库命令 ──
        db_cmds = {
            'mysql': '操作 MySQL 数据库', 'psql': '操作 PostgreSQL 数据库',
            'mongo': '操作 MongoDB 数据库', 'redis-cli': '操作 Redis 数据库',
            'sqlite3': '操作 SQLite 数据库', 'sqlplus': '操作 Oracle 数据库',
        }
        if first_cmd_lower in db_cmds:
            return f"正在{db_cmds[first_cmd_lower]}"

        # ── 编译构建命令 ──
        build_cmds = {
            'make': '构建项目', 'cmake': '配置构建环境', 'gcc': '编译 C 代码',
            'g++': '编译 C++ 代码', 'javac': '编译 Java 代码', 'mvn': '构建 Maven 项目',
            'gradle': '构建 Gradle 项目', 'cargo': '构建 Rust 项目', 'go': '构建 Go 项目',
        }
        if first_cmd_lower in build_cmds:
            return f"正在{build_cmds[first_cmd_lower]}"

        # ── 服务管理命令 ──
        service_cmds = {
            'systemctl': '管理系统服务', 'service': '管理系统服务',
            'chkconfig': '配置服务启动项', 'sc': '管理 Windows 服务',
        }
        if first_cmd_lower in service_cmds:
            return f"正在{service_cmds[first_cmd_lower]}"

        # ── 用户管理命令 ──
        user_cmds = {
            'useradd': '创建用户', 'userdel': '删除用户', 'usermod': '修改用户',
            'passwd': '修改密码', 'groupadd': '创建用户组', 'groupdel': '删除用户组',
            'chage': '修改用户密码策略', 'id': '查看用户信息', 'who': '查看登录用户',
            'w': '查看登录用户详情', 'last': '查看登录历史',
        }
        if first_cmd_lower in user_cmds:
            return f"正在{user_cmds[first_cmd_lower]}"

        # ── SSH 相关命令 ──
        ssh_cmds = {
            'ssh': '建立 SSH 连接', 'scp': '通过 SSH 传输文件',
            'rsync': '同步文件/目录', 'sftp': '通过 SFTP 传输文件',
            'ssh-keygen': '生成 SSH 密钥', 'ssh-copy-id': '复制 SSH 公钥',
        }
        if first_cmd_lower in ssh_cmds:
            return f"正在{ssh_cmds[first_cmd_lower]}"

        # ── 磁盘管理命令 ──
        disk_cmds = {
            'fdisk': '磁盘分区', 'parted': '磁盘分区', 'mkfs': '格式化文件系统',
            'mount': '挂载文件系统', 'umount': '卸载文件系统', 'fsck': '检查文件系统',
            'dd': '磁盘复制/转换', 'lsblk': '查看块设备', 'blkid': '查看块设备属性',
        }
        if first_cmd_lower in disk_cmds:
            return f"正在{disk_cmds[first_cmd_lower]}"

        # ── 系统包管理命令 ──
        pkg_cmds = {
            'apt': '管理 Debian 软件包', 'apt-get': '管理 Debian 软件包',
            'yum': '管理 RPM 软件包', 'dnf': '管理 Fedora 软件包',
            'pacman': '管理 Arch 软件包', 'brew': '管理 Homebrew 软件包',
            'choco': '管理 Chocolatey 软件包', 'scoop': '管理 Scoop 软件包',
            'zypper': '管理 openSUSE 软件包', 'apk': '管理 Alpine 软件包',
        }
        if first_cmd_lower in pkg_cmds:
            action = "安装" if "install" in cmd_lower else "更新" if "update" in cmd_lower else "管理"
            return f"正在{action}系统软件包"

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
        self._tool_policy = None
        self._turn_session_id = None
        self._turn_id = None

    def set_turn_context(self, session_id, turn_id) -> None:
        self._turn_session_id = session_id
        self._turn_id = turn_id

    def get_turn_context(self):
        if not self._turn_session_id or not self._turn_id:
            return None
        return (self._turn_session_id, self._turn_id)

    def set_tool_policy(self, policy) -> None:
        """实例级策略，context 里带的优先"""
        self._tool_policy = policy

    def _check_policy(self, tool_name: str, platform_context, session_id: str = None, arguments=None):
        from app.core.interface.tool_policy import ToolPolicy, MODE_WHITELIST
        policy = ToolPolicy.from_context(platform_context) or self._tool_policy
        if policy is None:
            return None
        cmd = arguments.get("command") if isinstance(arguments, dict) else None
        key = f"{tool_name}.{cmd.strip()}" if isinstance(cmd, str) and cmd.strip() else tool_name
        if policy.mode == MODE_WHITELIST:
            allowed = policy.allows(tool_name) or policy.allows(key)
        else:
            allowed = policy.allows(tool_name) and policy.allows(key)
        if allowed:
            return None
        logger.warning("[ToolExecutor] 权限不足，禁止使用工具 | session=%s | tool=%s | mode=%s",
                       session_id, key, policy.mode)
        return {
            "error": policy.block_reason(key),
            "_tool_blocked": True,
            "_source": "permission_check",
        }

    def refresh_tools(self, tools: Dict[str, Any]):
        """更新工具表（热插拔后调用）"""
        self.tools = tools

    def _undo_checkpoint(self, tool_name: str, arguments: dict) -> None:
        if not self._turn_session_id or not self._turn_id:
            return
        try:
            from app.core.util.undo_store import get_undo_store
            store = get_undo_store()
            if tool_name == "edit":
                paths = [arguments.get("file_path") or arguments.get("path")]
            elif tool_name == "file":
                paths = []
                command = (arguments.get("command") or "").strip()
                action = (arguments.get("action") or "").strip()
                base = arguments.get("path")
                if command == "fs" and action == "create":
                    if base:
                        paths.append(base)
                    files = arguments.get("files")
                    if isinstance(files, str):
                        try:
                            files = json.loads(files)
                        except Exception:
                            files = None
                    if isinstance(files, dict):
                        for key in files.keys():
                            if base and not os.path.isabs(str(key)):
                                paths.append(os.path.join(base, str(key)))
                            else:
                                paths.append(str(key))
                elif base:
                    paths.append(base)
            elif tool_name == "shell":
                shell_tool = self._builtin_tools.get("shell")
                cwd = (
                    getattr(shell_tool, "_cwd", None)
                    or getattr(getattr(shell_tool, "shell", None), "_cwd", None)
                    or os.getcwd()
                )
                store.checkpoint_shell(self._turn_session_id, self._turn_id, cwd)
                return
            else:
                return
            store.checkpoint_paths(self._turn_session_id, self._turn_id, [p for p in paths if p])
        except Exception as e:
            logger.warning("[ToolExecutor] undo checkpoint 失败: %s", e)

    async def execute(self, tool_call, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工具调用（异步包装，避免阻塞事件循环）
        
        Args:
            tool_call: 工具调用信息
            session_id: 当前会话 ID（可选）
            platform_context: 平台上下文信息（可选，包含 target_id 等）
        """
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        tool_name = tool_call.name
        arguments = tool_call.arguments

        if isinstance(arguments, dict):
            arguments = {k: v for k, v in arguments.items() if k != "_intent"}
        else:
            arguments = dict(arguments or {})

        blocked = self._check_policy(tool_name, platform_context, session_id, arguments)
        if blocked is not None:
            return blocked

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

        self._undo_checkpoint(tool_name, arguments)

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, self._dispatch, tool_instance, arguments, session_id, platform_context
            )
            return result
        except Exception as e:
            logger.error("[ToolExecutor] 工具执行失败: %s | error=%s", tool_name, e)
            return {"error": str(e)}

    def execute_sync(self, tool_call, session_id: str = None, platform_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """同步执行工具（供可强制中断的专用线程调用）

        与 execute 逻辑一致，但不经 run_in_executor 包装——由调用方在线程中
        直接运行，从而支持 PyThreadState_SetAsyncExc 强制中断。
        """
        tool_name = tool_call.name
        arguments = tool_call.arguments

        if isinstance(arguments, dict):
            arguments = {k: v for k, v in arguments.items() if k != "_intent"}
        else:
            arguments = dict(arguments or {})

        platform_context = platform_context or {}
        blocked = self._check_policy(tool_name, platform_context, session_id, arguments)
        if blocked is not None:
            return blocked

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
            "[ToolExecutor] execute_sync | name=%s | session=%s | available=%s",
            tool_name, session_id or "N/A", list(self.tools.keys()),
        )

        if tool_name not in self.tools:
            error_msg = f"Unknown tool: {tool_name}. Available: {list(self.tools.keys())}"
            logger.error("[ToolExecutor] %s", error_msg)
            return {"error": error_msg}

        tool_instance = self.tools[tool_name]
        self._undo_checkpoint(tool_name, arguments)
        try:
            return self._dispatch(tool_instance, arguments, session_id, platform_context)
        except Exception as e:
            logger.error("[ToolExecutor] 工具执行失败: %s | error=%s", tool_name, e)
            return {"error": str(e)}

    @staticmethod
    def _dispatch(tool_instance, arguments: Dict[str, Any], session_id: str, platform_context: Dict[str, Any]) -> Dict[str, Any]:
        """分发工具调用到具体执行方法（同步调用）"""
        if hasattr(tool_instance, "execute_with_context"):
            return tool_instance.execute_with_context(arguments, session_id=session_id, platform_context=platform_context)
        elif hasattr(tool_instance, "execute"):
            return tool_instance.execute(arguments)
        elif callable(tool_instance):
            return tool_instance(**arguments)
        return {"error": f"Tool {tool_instance} is not callable"}

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

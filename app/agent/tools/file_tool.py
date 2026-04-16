# -*- coding: utf-8 -*-
"""
文件操作工具 — 读写删查

设计原则：
  - 所有文件操作走 Python 原生 IO，避免 PowerShell 转义/编码问题
  - 自动处理 UTF-8 编码
  - 路径安全检查（防路径穿越）
  - 支持大文件自动截断
  - 原子写入（temp + rename）
  - 文件读取缓存 + 外部修改检测
"""

import os
import re
import shutil
import tempfile
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

from .base_tool import BaseTool

logger = logging.getLogger(__name__)

# 安全限制
_MAX_FILE_SIZE = 2 * 1024 * 1024   # 单次读取最大 2MB
_MAX_LIST_ENTRIES = 200            # 列目录最多返回 200 条
_ALLOWED_ROOTS = None              # 允许的根目录列表（None=不限制）

_STRUCTURE_PATTERNS = {
    '.py': re.compile(r'^\s*(class|def|async\s+def)\s+(?P<name>[\w\.]+)'),
    '.cpp': re.compile(r'^\s*(?:[\w\d<>*&:]+\s+)?(?P<name>[\w\d_:]+(?:<[\w\d\s,:]+>)?(?:::[\w\d_:]+)*)\s*(?:\{|:)'),
    '.h': re.compile(r'^\s*(class|struct|namespace|enum|typedef|using)\s+(?P<name>[\w\d_:]+(?:<.*>)?)'),
    '.c': re.compile(r'^\s*(struct|class|enum|namespace|typedef|union)\s+(?P<name>[\w\d_]+)'),
    '.cs': re.compile(r'^\s*(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:internal\s+)?(?:static\s+)?(?:abstract\s+)?(?:sealed\s+)?(?:partial\s+)?(?:override\s+)?(?:virtual\s+)?(?:async\s+)?(?:[\w\d<>\[\]]+\s+)?(?:class\s+|struct\s+|interface\s+|namespace\s+|enum\s+|record\s+|delegate\s+|void\s+|[\w\d<>\[\]]+\s+)(?P<name>[\w\d_]+)'),
    '.java': re.compile(r'^\s*(?:public\s+)?(class|interface|enum|record)\s+(?P<name>[\w\d_]+)|^\s*(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:static\s+)?(?:abstract\s+)?(?:final\s+)?(?:synchronized\s+)?(?:[\w\d<>\[\]]+\s+)?(?P<name2>[\w\d_]+)\s*\('),
    '.js': re.compile(r'^\s*(export\s+(default\s+)?)?(class|function|const|async)\s+(?P<name>\w+)'),
    '.ts': re.compile(r'^\s*(export\s+)?(interface|type|class|function)\s+(?P<name>\w+)'),
    '.go': re.compile(r'^\s*(func(?:\s+\([^)]+\))?|type|const|var|interface)\s+(?P<name>[\w\d_]+)'),
    '.rs': re.compile(r'^\s*(pub\s+)?(struct|enum|(?:async\s+)?fn|impl|trait|const|static(?:\s+mut)?)\s+(?P<name>[\w\d_]+)'),
    '.php': re.compile(r'^\s*(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:static\s+)?(?:abstract\s+)?(?:final\s+)?(?:const\s+|class\s+|interface\s+|trait\s+|enum\s+|function\s+)(?P<name>[\w\d_]+)'),
    '.rb': re.compile(r'^\s*(class|module|def|attr_accessor|attr_reader|attr_writer|include|extend|prepend)\s+(?P<name>[\w\d_:]+)'),
    '.kt': re.compile(r'^\s*(class|interface|sealed\s+class|enum\s+class|object|fun|data\s+class)\s+(?P<name>[\w\d_]+)'),
    '.css': re.compile(r'^\s*(?P<name>(?:@media|@keyframes|@font-face|@supports)[\s\S]*?|[@:.]?[\w.-]+)\s*(?:\{|$)'),
    '.html': re.compile(r'^\s*<(?P<name>[a-z]+)(?:\s|>)'),
    '.md': re.compile(r'^(#{1,6})\s+(?P<name>.+)'),
}
_DEFAULT_PATTERN = re.compile(r'^\s*(class|def|struct|function|namespace)\s+(?P<name>[\w\d_:]+)')


@dataclass
class FileState:
    """文件读取状态（用于检测外部修改）"""
    path: str
    content: str
    timestamp: float
    offset: Optional[int] = None
    limit: Optional[int] = None


class FileTool(BaseTool):

    name = "file"
    description = (
        "文件读写删查。原子写入防损坏，自动 UTF-8。\n\n"
        "| 命令 | 说明 | 注意 |\n"
        "|------|------|------|\n"
        "| read | 读文件，支持 offset/limit 分页 | 大文件先用 insight |\n"
        "| write | 写文件 | mode: overwrite/append/create |\n"
        "| edit | 编辑文件 | **必须先 read** |\n"
        "| create | 批量创建多文件 | files 为 {路径:内容} 字典 |\n"
        "| delete | 删除文件或目录 | recursive=True 删除非空目录 |\n"
        "| list | 列目录 | pattern 支持 glob 如 *.py |\n"
        "| exists | 检查路径存在 | - |\n"
        "| mkdir | 创建目录 | parents=True 自动建父目录 |\n"
        "| insight | 搜索/结构/摘要 | 先用 insight 看骨架再精准 read |\n"
        "\n\n"
        "**insight**: structure 返回骨架（breadcrumb/visual_tree），search 返回命中（breadcrumb/match_pos）\n"
        "**read**: 默认 offset=0, limit=500，建议先用 insight 看结构再精准读取"
    )

    def __init__(self, allowed_roots=None):
        """
        Args:
            allowed_roots: 允许访问的根目录列表，如 ["F:\\", "D:\\project"]
                          None 或不传参时默认为项目下的 workspace 目录（跨平台）
        """
        super().__init__()
        global _ALLOWED_ROOTS
        if allowed_roots is None:
            workspace_path = Path(__file__).resolve().parent.parent.parent.parent / "workspace"
            _ALLOWED_ROOTS = [str(workspace_path)]
        else:
            _ALLOWED_ROOTS = allowed_roots
        self._read_cache: Dict[str, FileState] = {}
        self._edit_history: list = []

    @property
    def tool_name(self) -> str:
        return "file"

    # ================================================================
    #  子命令实现（_cmd_ 前缀自动注册）
    # ================================================================

    def _cmd_read(self, path: str, offset: int = 0, limit: int = 500) -> Dict[str, Any]:
        """读取文件内容（UTF-8）

        Args:
            path: 文件路径（绝对或相对）
            offset: 从第几行开始读（默认0从头开始）
            limit: 最多读取行数（默认500，防止token爆炸）
        """
        if not path:
            return {
                "error": "未提供 path 参数",
                "hint": (
                    '调用 file 工具读取文件时使用 command="read"\n'
                    '  - path (必填): 文件路径，如 "D:\\project\\main.py"\n'
                    '\n'
                    '示例: {"command":"read","path":"D:\\\\test.py"}'
                ),
            }

        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        try:
            file_size = os.path.getsize(abs_path)
            if file_size > _MAX_FILE_SIZE:
                ext = os.path.splitext(abs_path)[1].lower()
                is_code = ext in ['.py', '.js', '.ts', '.java', '.go', '.cpp', '.c', '.rs', '.rb', '.php']
                hint_msg = (
                    f"文件过大 ({_to_mb(file_size):.1f}MB > {_to_mb(_MAX_FILE_SIZE):.1f}MB)。"
                    f"建议先用 `file insight` 获取结构大纲，再用 `read(offset=X, limit=Y)` 精准读取。"
                    if is_code else
                    f"文件过大 ({_to_mb(file_size):.1f}MB > {_to_mb(_MAX_FILE_SIZE):.1f}MB)。"
                    f"建议先用 `file insight` 搜索关键词定位，再用 `read(offset=X, limit=Y)` 分段读取。"
                )
                return {
                    "success": False,
                    "error": hint_msg,
                    "size": file_size,
                    "hint_command": f'file insight(path="{path}", mode="structure")' if is_code else f'file insight(path="{path}", mode="search", query="关键字")',
                }

            encoding = self._detect_encoding(abs_path)
            with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            if offset < 0:
                offset = 0
            if offset >= total_lines:
                return {"success": False, "error": f"Offset {offset} 超出文件总行数 {total_lines}"}

            end = offset + limit if limit else total_lines
            selected = all_lines[offset:end]
            content = "".join(selected).rstrip("\n")

            # 缓存完整文件状态（供 Edit 使用）
            full_content = "".join(all_lines)
            self._set_file_state(abs_path, FileState(
                path=abs_path,
                content=full_content,
                timestamp=os.path.getmtime(abs_path),
                offset=offset,
                limit=limit,
            ))

            return {
                "success": True,
                "data": content,
                "path": abs_path,
                "lines": len(selected),
                "total_lines": total_lines,
                "truncated": total_lines > len(selected),
                "encoding": encoding,
            }
        except UnicodeDecodeError as e:
            return {"success": False, "error": f"文件编码不是 UTF-8: {e}", "hint": "可尝试 shell: Get-Content -Encoding Default"}
        except Exception as e:
            return {"success": False, "error": f"读取失败 ({type(e).__name__}): {e}"}

    def _cmd_write(self, path: str, content: str, mode: str = "overwrite") -> Dict[str, Any]:
        """写入文件（原子写入）

        Args:
            path: 文件路径
            content: 要写入的文本内容
            mode: 写入模式
                  overwrite — 覆盖（默认）
                  append     — 追加
                  create     — 仅创建新文件（已存在则返回错误）
        """
        if not path:
            return {
                "error": "未提供 path 参数",
                "hint": '示例: {"command":"write","path":"D:\\\\test.py","content":"hello"}',
            }
        if content is None:
            return {
                "error": "未提供 content 参数",
                "hint": '示例: {"command":"write","path":"D:\\\\test.py","content":"文件内容"}',
            }

        abs_path = self._resolve_path(path)

        try:
            parent_dir = os.path.dirname(abs_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            if mode == "create" and os.path.exists(abs_path):
                return {"success": False, "error": f"文件已存在（mode=create 不允许覆盖）: {abs_path}"}

            if mode == "append":
                if os.path.exists(abs_path):
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        existing = f.read()
                    content = existing + content

            self._atomic_write(abs_path, content)

            return {
                "success": True,
                "message": f"{'追加到' if mode=='append' else '写入'}文件成功",
                "path": abs_path,
                "bytes_written": len(content.encode("utf-8")),
                "mode": mode,
            }
        except Exception as e:
            return {"success": False, "error": f"写入失败 ({type(e).__name__}): {e}"}

    def _cmd_edit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> Dict[str, Any]:
        """编辑文件

        Args:
            path: 文件路径
            old_string: 要替换的字符串
            new_string: 替换后的字符串
            replace_all: 是否替换所有匹配（默认 False）
        """
        if not path:
            return {"success": False, "error": "未提供 path 参数", "hint": '示例: {"command":"edit","path":"D:\\\\test.py","old_string":"hello","new_string":"hello world"}'}
        if not old_string:
            return {"success": False, "error": "未提供 old_string 参数"}
        if new_string is None:
            return {"success": False, "error": "未提供 new_string 参数"}
        if old_string == new_string:
            return {"success": False, "error": "old_string 和 new_string 相同，无需替换"}

        abs_path = self._resolve_path(path)

        if not os.path.exists(abs_path):
            if old_string == "":
                try:
                    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
                    self._atomic_write(abs_path, new_string)
                    return {"success": True, "message": f"创建新文件: {abs_path}", "path": abs_path}
                except Exception as e:
                    return {"success": False, "error": f"创建文件失败: {e}"}
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        file_state = self._get_file_state(abs_path)
        if not file_state:
            return {"success": False, "error": "文件尚未读取，请先使用 read 命令读取文件后再进行编辑"}

        current_mtime = os.path.getmtime(abs_path)
        if current_mtime > file_state.timestamp:
            encoding = self._detect_encoding(abs_path)
            try:
                with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                    current_content = f.read()
                if current_content != file_state.content:
                    return {"success": False, "error": "文件已被外部修改，请重新读取后再编辑"}
            except Exception:
                return {"success": False, "error": "文件已被外部修改，请重新读取后再编辑"}

        actual_old_string = self._find_actual_string(file_state.content, old_string)
        if not actual_old_string:
            return {"success": False, "error": f"在文件中未找到要替换的字符串: {repr(old_string[:50])}"}

        matches = file_state.content.count(actual_old_string)
        if matches > 1 and not replace_all:
            return {"success": False, "error": f"找到 {matches} 处匹配，但 replace_all 为 false。设置 replace_all: true 可替换所有匹配"}

        old_content = file_state.content
        if replace_all:
            new_content = old_content.replace(actual_old_string, new_string)
            action = f"替换了 {matches} 处"
        else:
            new_content = old_content.replace(actual_old_string, new_string, 1)
            action = "替换了 1 处"

        diff = self._generate_diff(old_content, new_content, abs_path)

        try:
            self._atomic_write(abs_path, new_content)
        except Exception as e:
            return {"success": False, "error": f"写入文件失败: {e}"}

        self._set_file_state(abs_path, FileState(
            path=abs_path,
            content=new_content,
            timestamp=os.path.getmtime(abs_path),
        ))

        self._edit_history.append({
            "path": abs_path,
            "old_content": old_content,
            "new_content": new_content,
            "timestamp": os.path.getmtime(abs_path),
        })

        return {
            "success": True,
            "message": f"{action} in {abs_path}",
            "path": abs_path,
            "diff": diff,
            "replacements": matches if replace_all else 1,
        }

    def _cmd_delete(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        """删除文件或目录

        Args:
            path: 要删除的文件或目录路径
            recursive: 是否递归删除目录（默认False只删空目录）
        """
        if not path:
            return {
                "error": "未提供 path 参数",
                "hint": '示例: {"command":"delete","path":"D:\\\\test.py"}',
            }

        abs_path = self._resolve_path(path)

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"不存在: {abs_path}"}

        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
                op = "文件"
            elif os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                else:
                    os.rmdir(abs_path)
                op = "目录"
            else:
                return {"success": False, "error": f"无法识别的类型（非文件非目录）: {abs_path}"}

            return {
                "success": True,
                "message": f"{op}删除成功",
                "path": abs_path,
            }
        except OSError as e:
            if "Directory not empty" in str(e) or "目录非空" in str(e):
                return {
                    "error": f"目录非空（需要 recursive=True 才能删除非空目录）",
                    "path": abs_path,
                }
            return {"success": False, "error": f"删除失败 ({type(e).__name__}): {e}"}
        except Exception as e:
            return {"success": False, "error": f"删除失败 ({type(e).__name__}): {e}"}

    def _cmd_list(self, dir_path: str = ".", show_hidden: bool = False,
                  pattern: str = None, detail: bool = False) -> Dict[str, Any]:
        """列出目录内容

        Args:
            dir_path: 目录路径（默认当前工作目录）
            show_hidden: 是否显示隐藏文件/目录（默认不显示）
            pattern: 文件名过滤（glob 模式，如 "*.py"、"*.md"）
            detail: 是否显示详细信息（大小、修改时间）
        """
        abs_path = self._resolve_path(dir_path)

        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"目录不存在: {abs_path}"}

        try:
            entries = []
            count = 0
            _skip_pattern = pattern in ("*", "*.*", "**", "*.*", "", None)
            _import_fnmatch = False

            for name in sorted(os.listdir(abs_path)):
                if not show_hidden and name.startswith("."):
                    continue
                if not _skip_pattern and pattern:
                    if not _import_fnmatch:
                        import fnmatch
                        _import_fnmatch = True
                    if not fnmatch.fnmatch(name, pattern):
                        continue

                full_path = os.path.join(abs_path, name)
                stat_info = os.stat(full_path)
                is_dir = os.path.isdir(full_path)

                entry = {"name": name, "type": "dir" if is_dir else "file"}

                if detail:
                    import datetime
                    mtime = datetime.datetime.fromtimestamp(stat_info.st_mtime)
                    if is_dir:
                        try:
                            item_count = len(os.listdir(full_path))
                            size_display = f"{item_count} items"
                        except PermissionError:
                            size_display = "?"
                    else:
                        size_display = stat_info.st_size
                    entry.update({
                        "size": size_display,
                        "modified": mtime.isoformat(timespec="seconds"),
                    })

                entries.append(entry)
                count += 1
                if count >= _MAX_LIST_ENTRIES:
                    entries.append({"name": f"...(共{count+1}条，已截断)", "type": "info"})
                    break

            return {
                "success": True,
                "data": entries,
                "path": abs_path,
                "count": min(count, _MAX_LIST_ENTRIES),
            }
        except PermissionError:
            return {"success": False, "error": f"无权限访问: {abs_path}"}
        except Exception as e:
            return {"success": False, "error": f"列出失败 ({type(e).__name__}): {e}"}

    def _cmd_exists(self, path: str) -> Dict[str, Any]:
        """检查路径是否存在及类型

        Args:
            path: 文件或目录路径
        """
        if not path:
            return {
                "error": "未提供 path 参数",
                "hint": '示例: {"command":"exists","path":"D:\\\\test.py"}',
            }

        abs_path = self._resolve_path(path)

        exists = os.path.exists(abs_path)
        info = {}
        if exists:
            info["type"] = "directory" if os.path.isdir(abs_path) else "file"
            info["size"] = os.path.getsize(abs_path) if not info["type"] == "directory" else None

        return {
            "success": True,
            "exists": exists,
            "path": abs_path,
            **info,
        }

    def _cmd_mkdir(self, path: str, parents: bool = True) -> Dict[str, Any]:
        """创建目录

        Args:
            path: 目录路径
            parents: 是否自动创建父目录（默认True）
        """
        if not path:
            return {
                "error": "未提供 path 参数",
                "hint": '示例: {"command":"mkdir","path":"D:\\\\new_dir"}',
            }

        abs_path = self._resolve_path(path)

        try:
            os.makedirs(abs_path, exist_ok=parents)
            return {
                "success": True,
                "message": "目录创建成功" if not os.path.exists(abs_path) else "目录已存在",
                "path": abs_path,
            }
        except Exception as e:
            return {"success": False, "error": f"创建失败 ({type(e).__name__}): {e}"}

    def _cmd_create(self, base_dir: str, files: Dict[str, str], auto_mkdir: bool = True) -> Dict[str, Any]:
        """批量创建文件

        Args:
            base_dir: 基础目录（如 F:\\计算器），所有文件相对于此目录
            files: 文件字典 {相对路径: 内容}，如 {"index.html":"...", "style.css":"..."}
            auto_mkdir: 是否自动创建不存在的父目录（默认True）

        """
        if not base_dir:
            return {
                "error": "未提供 base_dir 参数",
                "hint": (
                    '调用 file create 批量创建文件:\n'
                    '  - base_dir (必填): 基础目录\n'
                    '  - files (必填): 文件字典 JSON，如 {"index.html":"<html>..."}\n'
                    '\n'
                    '示例: {"command":"create","base_dir":"D:\\\\project","files":{"main.py":"print(1)","README.md":"# test"}}'
                ),
            }

        import json as _json
        if isinstance(files, str):
            try:
                files = _json.loads(files)
            except (_json.JSONDecodeError, TypeError) as _e:
                return {"success": False, "error": f"files 参数格式错误（期望字典或JSON字符串）: {_e}"}

        if not files or not isinstance(files, dict):
            return {
                "error": "未提供 files 参数或格式错误，需要字典: {'path': 'content', ...}",
                "hint": (
                    'files 必须是字典或 JSON 字符串:\n'
                    '{"index.html":"<html>...</html>","style.css":"body{margin:0}","app.js":"console.log(1)"}\n'
                    '\n'
                    '完整调用示例: {"command":"create","base_dir":"D:\\\\project","files":{"main.py":"print(1)"}}'
                ),
            }

        abs_base = self._resolve_path(base_dir)

        try:
            os.makedirs(abs_base, exist_ok=True)
        except Exception as e:
            return {"success": False, "error": f"无法创建目录 {abs_base}: {e}"}

        results = []
        success_count = 0
        total_bytes = 0

        for rel_path, content in files.items():
            if not rel_path:
                continue

            full_path = os.path.join(abs_base, rel_path.replace('/', os.sep).replace('\\', os.sep))

            try:
                parent = os.path.dirname(full_path)
                if parent and auto_mkdir and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)

                self._atomic_write(full_path, content or "")

                file_size = len((content or "").encode("utf-8"))
                total_bytes += file_size
                success_count += 1
                results.append({"path": full_path, "status": "ok", "size": file_size})
            except Exception as e:
                results.append({"path": full_path, "status": "error", "error": str(e)})

        failed = [r for r in results if r["status"] != "ok"]
        return {
            "success": len(failed) == 0,
            "message": f"已创建 {success_count}/{len(files)} 个文件，共 {total_bytes} 字节",
            "base_dir": abs_base,
            "files_created": success_count,
            "total_files": len(files),
            "total_bytes": total_bytes,
            "details": results,
            **({"errors": failed} if failed else {}),
        }

    def _cmd_insight(self, path: str, mode: str = "auto", query: str = None, offset: int = 0) -> Dict[str, Any]:
        """
        理解层工具：返回文件大纲或搜索索引。
        原则：绝不返回超过 2KB 的数据，强制 Agent 进行分层阅读。
        """
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        file_size = os.path.getsize(abs_path)
        ext = os.path.splitext(abs_path)[1].lower()
        encoding = self._detect_encoding(abs_path)

        if mode == "auto":
            if ext in ['.py', '.js', '.ts', '.java', '.go', '.cpp', '.c', '.h', '.cs', '.rs', '.php', '.rb', '.kt', '.css', '.html', '.md']:
                mode = "structure"
            elif query or ext in ['.log', '.txt']:
                mode = "search"
            else:
                mode = "summary"

        try:
            with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                if mode == "structure":
                    return self._extract_structure(f, ext)
                elif mode == "search":
                    return self._stream_search(f, query, ext=ext, offset=offset)
                else:
                    return self._file_summary(f, abs_path, file_size)
        except Exception as e:
            return {"success": False, "error": f"Insight failed: {str(e)}"}

    # ================================================================
    #  理解层私有实现
    # ================================================================

    def _extract_structure(self, f, ext: str) -> Dict[str, Any]:
        regex = _STRUCTURE_PATTERNS.get(ext, _DEFAULT_PATTERN)
        is_markdown = (ext == '.md')

        _CONTROL_FLOW = {"if", "for", "while", "switch", "elif", "else", "try", "except", "finally"}
        _ACCESS_SPECIFIERS = {"public:", "private:", "protected:"}
        _CLASS_KEYWORDS = {"class", "struct", "interface"}

        symbols = []
        stack = []
        current_access = ""

        f.seek(0)
        for i, line in enumerate(f):
            stripped = line.strip()
            if not stripped or stripped.startswith(('/', '*')):
                continue

            if is_markdown:
                match = _STRUCTURE_PATTERNS['.md'].match(stripped)
                if not match:
                    continue
                level = len(match.group(1))
                name = match.group("name").strip()
                breadcrumb = " > ".join([s["name"] for s in stack])
                symbols.append({
                    "line": i + 1,
                    "level": level,
                    "breadcrumb": breadcrumb,
                    "name": name,
                    "indent": (level - 1) * 4,
                })
                stack = stack[:level - 1]
                stack.append({"name": name})
                continue

            indent = self._normalize_indent(line)

            while stack and indent <= stack[-1]['indent']:
                stack.pop()
                if not stack:
                    current_access = ""

            first_word = stripped.split()[0].split('(')[0] if stripped else ""

            if first_word in _ACCESS_SPECIFIERS:
                current_access = first_word.rstrip(':')
                continue

            match = regex.search(line)
            if match:
                name = match.group("name") or match.group("name2") or match.group("name3") or ""
                if not name:
                    continue
                should_push = (first_word not in _CONTROL_FLOW) and (first_word not in _ACCESS_SPECIFIERS)

                breadcrumb = " > ".join([node['name'] for node in stack])
                display_name = f"[{current_access}] {name}" if current_access else name

                symbols.append({
                    "line": i + 1,
                    "breadcrumb": breadcrumb,
                    "name": display_name,
                    "indent": indent,
                    "raw": stripped[:60],
                })

                if should_push and stripped.endswith((':', '{', '[')):
                    if any(kw in stripped for kw in _CLASS_KEYWORDS):
                        current_access = ""
                    stack.append({"indent": indent, "name": name})

            if len(symbols) >= 150:
                break

        return {
            "success": True,
            "type": "structure",
            "language": ext,
            "visual_tree": self._render_visual_tree(symbols),
            "symbols": symbols,
            "total_symbols": len(symbols),
        }

    def _normalize_indent(self, line: str, tab_size: int = 4) -> int:
        leading = line[:len(line) - len(line.lstrip())]
        return len(leading.replace('\t', ' ' * tab_size))

    def _render_visual_tree(self, symbols: List[Dict], max_length: int = 2000) -> str:
        parts = []
        for sym in symbols[:60]:
            ctx = f"{sym['breadcrumb']} > " if sym['breadcrumb'] else ""
            part = f"{ctx}{sym['name']}:{sym['line']}"
            parts.append(part)
        
        result = " | ".join(parts)
        if len(result) > max_length:
            result = result[:max_length] + "..."
        return result

    def _stream_search(self, f, query: str, ext: str = None, offset: int = 0) -> Dict[str, Any]:
        if not query:
            return {"success": False, "error": "Search 模式必须提供 query 参数"}

        _CONTROL_FLOW = {"if", "for", "while", "switch", "elif", "else", "try", "except", "finally"}
        _ACCESS_SPECIFIERS = {"public:", "private:", "protected:"}
        _CLASS_KEYWORDS = {"class", "struct", "namespace", "interface", "def", "void", "int", "auto"}

        hits = []
        stack = []
        current_access = ""

        def _is_regex(q: str) -> bool:
            return bool(set(q) & set('*+?^$[\\]|().{}'))

        use_regex = _is_regex(query)
        f.seek(0)

        for i, line in enumerate(f):
            stripped = line.strip()
            if not stripped:
                continue

            indent = self._normalize_indent(line)
            while stack and indent <= stack[-1]['indent']:
                stack.pop()
                if not stack:
                    current_access = ""

            first_word = stripped.split('(')[0].split()[0] if stripped else ""
            if first_word in _ACCESS_SPECIFIERS:
                current_access = first_word.rstrip(':')

            if any(k in stripped for k in _CLASS_KEYWORDS) and stripped.endswith((':', '{')):
                if first_word not in _CONTROL_FLOW:
                    name_hint = stripped.split('(')[0].split('{')[0].split()[-1]
                    stack.append({"indent": indent, "name": name_hint})

            if use_regex:
                matched = re.search(query, line, re.IGNORECASE)
            else:
                matched = query.lower() in line.lower()

            if matched:
                breadcrumb = " > ".join([node['name'] for node in stack])
                match_start = line.lower().find(query.lower()) if not use_regex else (matched.start() if matched else 0)
                hits.append({
                    "line": i + 1,
                    "breadcrumb": breadcrumb,
                    "access": current_access,
                    "content": stripped,
                    "match_pos": match_start,
                })

        total = len(hits)
        page = hits[offset:offset + 20]

        return {
            "success": True,
            "type": "search_with_context",
            "query": query,
            "hits": page,
            "total": total,
            "offset": offset,
            "has_more": offset + 20 < total,
        }

    def _file_summary(self, f, path: str, size: int) -> Dict[str, Any]:
        """模式3：快速摘要（适用于配置和文档）"""
        f.seek(0)
        lines = f.readlines()
        head = [line.strip() for line in lines[:5] if line.strip()]
        total_lines = len(lines)
        hint = f"文件共 {total_lines} 行，建议使用 search 模式定位关键内容"
        if total_lines <= 5:
            hint = "文件较短，可直接 read"
        return {
            "success": True,
            "type": "summary",
            "path": path,
            "size_kb": round(size / 1024, 2),
            "head": head,
            "total_lines": total_lines,
            "hint": hint,
        }

    # ================================================================
    #  内部工具方法
    # ================================================================

    def _resolve_path(self, path: str) -> str:
        """解析为绝对路径，空路径或无效路径时使用默认 workspace"""
        if not path or not isinstance(path, str):

            return str(Path(__file__).resolve().parent.parent.parent.parent / "workspace")

        if not os.path.isabs(path):
            workspace_path = Path(__file__).resolve().parent.parent.parent.parent / "workspace"
            relative_path = workspace_path / path
            if relative_path.exists():
                return str(relative_path)

        abs_path = os.path.abspath(path)
        return abs_path

    def _detect_encoding(self, file_path: str) -> str:
        """检测文件编码（BOM 检测）"""
        try:
            with open(file_path, "rb") as f:
                raw = f.read(4)
            if raw[:2] == b"\xff\xfe":
                return "utf-16-le"
            if raw[:2] == b"\xfe\xff":
                return "utf-16"
            if raw[:3] == b"\xef\xbb\xbf":
                return "utf-8-sig"
        except Exception:
            pass
        return "utf-8"

    def _get_file_state(self, path: str) -> Optional[FileState]:
        """获取文件读取状态"""
        return self._read_cache.get(os.path.abspath(path))

    def _set_file_state(self, path: str, state: FileState):
        """设置文件读取状态"""
        self._read_cache[os.path.abspath(path)] = state

    def _normalize_quotes(self, text: str) -> str:
        """标准化引号样式（处理中文弯引号）"""
        replacements = {
            '"': '"',
            '"': '"',
            ''': "'",
            ''': "'",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _find_actual_string(self, content: str, old_string: str) -> Optional[str]:
        """查找实际匹配字符串（处理引号样式差异）"""
        if old_string in content:
            return old_string
        normalized_old = self._normalize_quotes(old_string)
        normalized_content = self._normalize_quotes(content)
        if normalized_old in normalized_content:
            idx = content.find(old_string, 0)
            if idx != -1:
                return old_string
        return None

    def _generate_diff(self, old_content: str, new_content: str, file_path: str, max_lines: int = 50) -> str:
        """生成 unified diff 格式"""
        old_lines = old_content.split("\n")
        new_lines = new_content.split("\n")
        diff_lines = [f"--- {file_path}", f"+++ {file_path}"]
        i = j = 0
        changes = []
        while i < len(old_lines) or j < len(new_lines):
            old_line = old_lines[i] if i < len(old_lines) else None
            new_line = new_lines[j] if j < len(new_lines) else None
            if old_line == new_line:
                i += 1
                j += 1
            elif old_line is None:
                changes.append(f"+ {new_line}")
                j += 1
            elif new_line is None:
                changes.append(f"- {old_line}")
                i += 1
            else:
                changes.append(f"- {old_line}")
                changes.append(f"+ {new_line}")
                i += 1
                j += 1
            if len(changes) >= max_lines:
                changes.append("... (truncated)")
                break
        if changes:
            diff_lines.extend(changes)
            return "\n".join(diff_lines)
        return ""

    def _atomic_write(self, file_path: str, content: str):
        """原子写入（先写临时文件，再 rename）"""
        dir_path = os.path.dirname(file_path) or "."
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            encoding = self._detect_encoding(file_path) if os.path.exists(file_path) else "utf-8"
            with os.fdopen(temp_fd, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            temp_fd = None
            if os.path.exists(file_path):
                stat_info = os.stat(file_path)
                os.replace(temp_path, file_path)
                os.chmod(file_path, stat_info.st_mode)
            else:
                os.replace(temp_path, file_path)
        except Exception:
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise


def _to_mb(n: int) -> float:
    return n / (1024 * 1024)

# -*- coding: utf-8 -*-
import json
import os
import re
import shutil
import tempfile
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

from .base_tool import BaseTool
from ..runtime.context import ReadTracker, ContextCompact, SymbolSummary
from ..runtime.transaction import EditTransaction
from ..runtime.patch_applier import PatchApplier

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 2 * 1024 * 1024
_MAX_LIST_ENTRIES = 200


def _unescape_string(value: str) -> str:
    """处理双重转义的字符串（如 \\n -> 换行符）
    """
    if not isinstance(value, str):
        return value

    if '\\' not in value:
        return value

    result = value
    escape_map = [
        ('\\n', '\n'),
        ('\\t', '\t'),
        ('\\r', '\r'),
        ('\\"', '"'),
        ("\\'", "'"),
        ('\\\\', '\\'),  
    ]

    has_escape = any(seq in value for seq in ['\\n', '\\t', '\\r', '\\"', "\\'"])

    if not has_escape:
        if '\\\\' in value:
            return value.replace('\\\\', '\\')
        return value

    for escaped, unescaped in escape_map[:-1]:
        if escaped in result and escaped != '\\\\':
            result = result.replace(escaped, unescaped)

    if '\\\\' in value:
        result = result.replace('\\\\', '\\')

    return result


@dataclass
class FileState:
    path: str
    content: str
    timestamp: float


class FileTool(BaseTool):

    name = "file"
    description = (
        "文件操作。4 个核心命令：read（读取）、insight（探索）、edit（修改）、fs（文件系统）\n\n"
        "**read**: 读取具体目标\n"
        "  - mode=full: 完整读取\n"
        "  - mode=context: 只读目标附近（target + 前后 N 行）\n"
        "  - mode=summary: 只返回类/函数签名\n"
        "  - mode=compact: 压缩读取（折叠 imports/docstrings）\n\n"
        "**insight**: 探索工程（不知道目标在哪时使用）\n"
        "  - mode=grep: 搜索内容（query=搜索词, pattern=文件过滤如*.py）\n"
        "  - mode=structure: 文件结构大纲\n"
        "  - mode=symbol: 搜索符号\n"
        "  - mode=files: 搜索文件\n\n"
        "**edit**: 修改文件（自动验证，失败回滚）\n"
        "  - mode=replace: 替换文本（old_text → new_text）\n"
        "  - mode=replace_all: 替换所有匹配\n"
        "  - mode=insert: 在指定行号前插入（line, content）\n"
        "  - mode=append: 追加内容\n"
        "  - mode=regex: 正则替换（pattern, replacement）\n"
        "  - mode=range: 按行号编辑（start_line, end_line, new_text）\n"
        "  - mode=delete: 删除行范围（start_line, end_line）\n\n"
        "**fs**: 文件系统操作\n"
        "  - action=list: 列目录\n"
        "  - action=mkdir: 创建目录\n"
        "  - action=delete: 删除文件/目录\n"
        "  - action=exists: 检查存在\n"
        "  - action=create: 批量创建文件\n\n"
        "**推荐流程**: insight → read(mode=context) → edit"
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []
        self._read_cache: Dict[str, FileState] = {}
        self._read_tracker = ReadTracker()
        self._transaction: Optional[EditTransaction] = None

    @property
    def tool_name(self) -> str:
        return "file"

    def _cmd_read(
        self,
        path: str,
        mode: str = "full",
        target: str = None,
        before: int = 3,
        after: int = 3,
        offset: int = 0,
        limit: int = 500,
    ) -> Dict[str, Any]:
        if not path:
            return {"success": False, "error": "需要 path 参数"}

        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        file_size = os.path.getsize(abs_path)

        if file_size > _MAX_FILE_SIZE and mode == "full":
            return {
                "success": False,
                "error": f"文件过大 ({file_size/1024/1024:.1f}MB)，建议使用 mode=summary 或 mode=context",
                "size": file_size,
            }

        try:
            with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                content = f.read()
            lines = content.split('\n')
            total_lines = len(lines)

            self._cache_file(abs_path, content)

            read_info = self._read_tracker.record(abs_path, offset, limit)

            if mode == "full":
                if offset < 0:
                    offset = 0
                if offset >= total_lines:
                    return {"success": False, "error": f"offset {offset} 超出文件总行数 {total_lines}"}

                end = min(offset + limit, total_lines)
                selected_lines = lines[offset:end]
                result_content = '\n'.join(selected_lines)

                return {
                    "success": True,
                    "data": result_content,
                    "path": abs_path,
                    "lines": len(selected_lines),
                    "total_lines": total_lines,
                    "offset": offset,
                    "truncated": end < total_lines,
                    "encoding": encoding,
                    "read_count": read_info.get("read_count", 1),
                }

            elif mode == "context":
                if not target:
                    return {"success": False, "error": "mode=context 需要提供 target 参数"}

                target_lower = target.lower()
                target_line = None
                for i, line in enumerate(lines):
                    if target_lower in line.lower():
                        target_line = i
                        break

                if target_line is None:
                    return {"success": False, "error": f"未找到目标: {target[:50]}"}

                start = max(0, target_line - before)
                end = min(total_lines, target_line + after + 1)
                context_content = '\n'.join(lines[start:end])

                return {
                    "success": True,
                    "data": context_content,
                    "path": abs_path,
                    "target_line": target_line + 1,
                    "start_line": start + 1,
                    "end_line": end,
                    "context_lines": end - start,
                    "total_lines": total_lines,
                    "encoding": encoding,
                }

            elif mode == "summary":
                ext = os.path.splitext(abs_path)[1].lower()
                result = SymbolSummary.extract(content, ext)

                return {
                    "success": True,
                    "data": result["summary"],
                    "path": abs_path,
                    "symbols": result["symbols"],
                    "total_symbols": result["total_symbols"],
                    "total_lines": total_lines,
                }

            elif mode == "compact":
                result = ContextCompact.compact(content)

                return {
                    "success": True,
                    "data": result["content"],
                    "path": abs_path,
                    "original_lines": result["original_lines"],
                    "new_lines": result["new_lines"],
                    "imports_collapsed": result["imports_collapsed"],
                    "docstrings_collapsed": result["docstrings_collapsed"],
                    "blank_lines_removed": result["blank_lines_removed"],
                    "compression_ratio": result["compression_ratio"],
                }

            else:
                return {"success": False, "error": f"未知 mode: {mode}"}

        except Exception as e:
            return {"success": False, "error": f"读取失败: {e}"}

    def _cmd_insight(
        self,
        query: str = None,
        mode: str = "grep",
        path: str = ".",
        pattern: str = None,
        ext: str = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)

        if mode == "grep":
            if not query:
                return {"success": False, "error": "mode=grep 需要提供 query 参数"}
            return self._grep_search(abs_path, query, pattern, ext, offset)

        elif mode == "structure":
            if os.path.isfile(abs_path):
                return self._extract_structure(abs_path)
            elif os.path.isdir(abs_path):
                return self._fs_list(abs_path, pattern or "*", True)
            else:
                return {"success": False, "error": f"路径不存在: {abs_path}"}

        elif mode == "symbol":
            if not query:
                return {"success": False, "error": "mode=symbol 需要提供 query 参数"}
            return self._symbol_search(abs_path, query, pattern, ext, offset)

        elif mode == "files":
            return self._file_search(abs_path, query or pattern or "*", offset)

        else:
            return {"success": False, "error": f"未知 mode: {mode}"}

    def _grep_search(self, root: str, query: str, pattern: str, ext_filter: str, offset: int) -> Dict[str, Any]:
        hits = []
        use_regex = any(c in query for c in '*+?^$[]|(){}')

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            for filename in filenames:
                if ext_filter and not filename.endswith(ext_filter):
                    continue
                if pattern and not self._match_pattern(filename, pattern):
                    continue

                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f):
                            if use_regex:
                                match = re.search(query, line, re.IGNORECASE)
                                if match:
                                    hits.append({
                                        "file": filepath,
                                        "line": i + 1,
                                        "content": line.strip()[:100],
                                        "match": match.group(),
                                    })
                            else:
                                if query.lower() in line.lower():
                                    hits.append({
                                        "file": filepath,
                                        "line": i + 1,
                                        "content": line.strip()[:100],
                                    })
                except Exception:
                    continue

                if len(hits) >= 100:
                    break
            if len(hits) >= 100:
                break

        total = len(hits)
        page = hits[offset:offset + 20]

        return {
            "success": True,
            "mode": "grep",
            "query": query,
            "hits": page,
            "total": total,
            "offset": offset,
            "has_more": offset + 20 < total,
        }

    def _symbol_search(self, root: str, query: str, pattern: str, ext_filter: str, offset: int) -> Dict[str, Any]:
        hits = []
        ext = ext_filter or ".py"

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            for filename in filenames:
                if not filename.endswith(ext):
                    continue

                filepath = os.path.join(dirpath, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()

                    result = SymbolSummary.extract(content, ext)
                    for sym in result["symbols"]:
                        if query.lower() in sym["name"].lower():
                            hits.append({
                                "file": filepath,
                                "line": sym["line"],
                                "name": sym["name"],
                                "type": sym["type"],
                            })
                except Exception:
                    continue

        total = len(hits)
        page = hits[offset:offset + 20]

        return {
            "success": True,
            "mode": "symbol",
            "query": query,
            "hits": page,
            "total": total,
            "offset": offset,
        }

    def _file_search(self, root: str, pattern: str, offset: int) -> Dict[str, Any]:
        hits = []
        pattern_lower = pattern.lower().replace('*', '')

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            for filename in filenames:
                if pattern_lower in filename.lower():
                    filepath = os.path.join(dirpath, filename)
                    hits.append({
                        "file": filepath,
                        "name": filename,
                    })

                if len(hits) >= 100:
                    break
            if len(hits) >= 100:
                break

        total = len(hits)
        page = hits[offset:offset + 20]

        return {
            "success": True,
            "mode": "files",
            "pattern": pattern,
            "hits": page,
            "total": total,
        }

    def _extract_structure(self, filepath: str) -> Dict[str, Any]:
        ext = os.path.splitext(filepath)[1].lower()
        encoding = self._detect_encoding(filepath)

        try:
            with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()

            result = SymbolSummary.extract(content, ext)

            return {
                "success": True,
                "mode": "structure",
                "path": filepath,
                "data": result["summary"],
                "symbols": result["symbols"],
                "total_symbols": result["total_symbols"],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _match_pattern(self, filename: str, pattern: str) -> bool:
        import fnmatch
        return fnmatch.fnmatch(filename, pattern)

    def _cmd_edit(
        self,
        path: str,
        mode: str = "replace",
        old_text: str = None,
        new_text: str = None,
        line: int = None,
        start_line: int = None,
        end_line: int = None,
        content: str = None,
        pattern: str = None,
        replacement: str = None,
        validate: bool = True,
        preview: bool = False,
    ) -> Dict[str, Any]:
        old_text = _unescape_string(old_text)
        new_text = _unescape_string(new_text)
        content = _unescape_string(content)
        pattern = _unescape_string(pattern)
        replacement = _unescape_string(replacement)

        if mode == "replace" and not old_text and start_line is not None and end_line is not None:
            mode = "range"

        if not path:
            return {"success": False, "error": "需要 path 参数"}

        abs_path = self._resolve_path(path)

        if not os.path.exists(abs_path):
            if mode == "append" and content:
                return self._write_new_file(abs_path, content)
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        file_state = self._read_cache.get(abs_path)
        if not file_state:
            return {"success": False, "error": "请先使用 read 命令读取文件"}

        current_mtime = os.path.getmtime(abs_path)
        if current_mtime > file_state.timestamp:
            return {"success": False, "error": "文件已被外部修改，请重新读取"}

        if preview:
            return self._preview_edit(abs_path, mode, old_text, new_text, line,
                                      start_line, end_line, content, pattern, replacement)

        tx = EditTransaction(workspace=os.path.dirname(abs_path) or os.getcwd())
        tx.begin()

        try:
            if mode == "replace":
                if not old_text:
                    return {"success": False, "error": "mode=replace 需要 old_text 参数"}
                if new_text is None:
                    return {"success": False, "error": "mode=replace 需要 new_text 参数"}
                if old_text == new_text:
                    return {"success": False, "error": "old_text 和 new_text 相同"}
                step = tx.create_step(abs_path, old_text, new_text, replace_all=False)

            elif mode == "replace_all":
                if not old_text:
                    return {"success": False, "error": "mode=replace_all 需要 old_text 参数"}
                if new_text is None:
                    return {"success": False, "error": "mode=replace_all 需要 new_text 参数"}
                step = tx.create_step(abs_path, old_text, new_text, replace_all=True)

            elif mode == "insert":
                if line is None:
                    return {"success": False, "error": "mode=insert 需要 line 参数"}
                if content is None:
                    return {"success": False, "error": "mode=insert 需要 content 参数"}
                step = tx.create_step_for_insert(abs_path, line, content)

            elif mode == "append":
                if content is None:
                    return {"success": False, "error": "mode=append 需要 content 参数"}
                step = tx.create_step_for_append(abs_path, content)

            elif mode == "regex":
                if not pattern:
                    return {"success": False, "error": "mode=regex 需要 pattern 参数"}
                if replacement is None:
                    return {"success": False, "error": "mode=regex 需要 replacement 参数"}
                step = tx.create_step_for_regex(abs_path, pattern, replacement)

            elif mode == "range":
                if start_line is None or end_line is None:
                    return {"success": False, "error": "mode=range 需要 start_line 和 end_line"}
                if new_text is None:
                    return {"success": False, "error": "mode=range 需要 new_text"}
                step = tx.create_step_by_range(abs_path, start_line, end_line, new_text)

            elif mode == "delete":
                if start_line is None or end_line is None:
                    return {"success": False, "error": "mode=delete 需要 start_line 和 end_line"}
                step = tx.create_step_by_range(abs_path, start_line, end_line, "")

            else:
                return {"success": False, "error": f"未知 mode: {mode}"}

            result = tx.commit_step(step, validate=validate)

            if result["success"]:
                with open(abs_path, 'r', encoding='utf-8') as f:
                    new_file_content = f.read()
                self._cache_file(abs_path, new_file_content)

                return {
                    "success": True,
                    "path": abs_path,
                    "mode": mode,
                    "step_id": step.step_id,
                    "count": result.get("count", 0),
                    "diff": result.get("diff", ""),
                    "diagnostics": result.get("diagnostics", []),
                    "validated": validate,
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error"),
                    "rolled_back": result.get("rolled_back", False),
                    "diagnostics": result.get("diagnostics", []),
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _preview_edit(
        self,
        abs_path: str,
        mode: str,
        old_text: str = None,
        new_text: str = None,
        line: int = None,
        start_line: int = None,
        end_line: int = None,
        content: str = None,
        pattern: str = None,
        replacement: str = None,
    ) -> Dict[str, Any]:
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"文件不存在: {abs_path}"}

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        except Exception as e:
            return {"success": False, "error": str(e)}

        if mode == "replace":
            patch = {"mode": "replace", "old_text": old_text, "new_text": new_text, "replace_all": False}
        elif mode == "replace_all":
            patch = {"mode": "replace", "old_text": old_text, "new_text": new_text, "replace_all": True}
        elif mode == "insert":
            patch = {"mode": "insert", "line": line, "content": content}
        elif mode == "append":
            patch = {"mode": "append", "content": content}
        elif mode == "regex":
            patch = {"mode": "regex", "pattern": pattern, "replacement": replacement}
        elif mode == "range":
            patch = {"mode": "range", "start_line": start_line, "end_line": end_line, "new_text": new_text}
        elif mode == "delete":
            patch = {"mode": "delete", "start_line": start_line, "end_line": end_line}
        else:
            return {"success": False, "error": f"未知 mode: {mode}"}

        preview_info = PatchApplier.preview(file_content, patch)

        return {
            "success": True,
            "preview": True,
            "path": abs_path,
            "diff": preview_info["diff"],
            "count": preview_info["count"],
            "matches": preview_info.get("matches", []),
            "error": preview_info.get("error"),
        }

    def _write_new_file(self, path: str, content: str) -> Dict[str, Any]:
        try:
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)

            self._atomic_write(path, content)
            self._cache_file(path, content)

            return {
                "success": True,
                "path": path,
                "bytes_written": len(content.encode('utf-8')),
                "message": "新文件已创建",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cmd_fs(
        self,
        action: str,
        path: str = None,
        dir_path: str = None,
        pattern: str = None,
        recursive: bool = False,
        parents: bool = True,
        files: Dict[str, str] = None,
        detail: bool = False,
    ) -> Dict[str, Any]:
        if action == "list":
            target = dir_path or path or "."
            return self._fs_list(target, pattern, detail)

        elif action == "mkdir":
            if not path:
                return {"success": False, "error": "需要 path 参数"}
            return self._fs_mkdir(path, parents)

        elif action == "delete":
            if not path:
                return {"success": False, "error": "需要 path 参数"}
            return self._fs_delete(path, recursive)

        elif action == "exists":
            if not path:
                return {"success": False, "error": "需要 path 参数"}
            return self._fs_exists(path)

        elif action == "create":
            if not path or not files:
                return {"success": False, "error": "需要 path 和 files 参数"}
            return self._fs_create(path, files)

        else:
            return {"success": False, "error": f"未知 action: {action}"}

    def _fs_list(self, path: str, pattern: str, detail: bool) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"目录不存在: {abs_path}"}

        entries = []
        try:
            for name in sorted(os.listdir(abs_path)):
                if name.startswith('.'):
                    continue

                if pattern and not self._match_pattern(name, pattern):
                    continue

                full_path = os.path.join(abs_path, name)
                is_dir = os.path.isdir(full_path)

                entry = {"name": name, "type": "dir" if is_dir else "file"}

                if detail:
                    stat_info = os.stat(full_path)
                    import datetime
                    entry["size"] = stat_info.st_size if not is_dir else None
                    entry["modified"] = datetime.datetime.fromtimestamp(stat_info.st_mtime).isoformat(timespec="seconds")

                entries.append(entry)

                if len(entries) >= _MAX_LIST_ENTRIES:
                    break

            return {
                "success": True,
                "data": entries,
                "path": abs_path,
                "count": len(entries),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _fs_mkdir(self, path: str, parents: bool) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        try:
            os.makedirs(abs_path, exist_ok=parents)
            return {
                "success": True,
                "path": abs_path,
                "message": "目录创建成功" if not os.path.exists(abs_path) else "目录已存在",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _fs_delete(self, path: str, recursive: bool) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"不存在: {abs_path}"}

        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
            elif os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                else:
                    os.rmdir(abs_path)

            return {"success": True, "path": abs_path, "message": "删除成功"}
        except OSError as e:
            if "Directory not empty" in str(e):
                return {"success": False, "error": "目录非空，需要 recursive=True"}
            return {"success": False, "error": str(e)}

    def _fs_exists(self, path: str) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        exists = os.path.exists(abs_path)

        result = {"success": True, "exists": exists, "path": abs_path}
        if exists:
            result["type"] = "directory" if os.path.isdir(abs_path) else "file"
            result["size"] = os.path.getsize(abs_path) if result["type"] == "file" else None

        return result

    def _fs_create(self, base_dir: str, files: Dict[str, str]) -> Dict[str, Any]:
        abs_base = self._resolve_path(base_dir)

        if isinstance(files, str):
            try:
                files = json.loads(files)
            except json.JSONDecodeError as e:
                return {"success": False, "error": f"files 参数 JSON 解析失败: {e}"}

        if not isinstance(files, dict):
            return {"success": False, "error": f"files 参数必须是字典或 JSON 字符串，当前类型: {type(files).__name__}"}

        try:
            os.makedirs(abs_base, exist_ok=True)
        except Exception as e:
            return {"success": False, "error": f"无法创建目录: {e}"}

        results = []
        success_count = 0

        for rel_path, content in files.items():
            full_path = os.path.join(abs_base, rel_path.replace('/', os.sep).replace('\\', os.sep))

            try:
                parent = os.path.dirname(full_path)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)

                self._atomic_write(full_path, content or "")
                success_count += 1
                results.append({"path": full_path, "status": "ok"})
            except Exception as e:
                results.append({"path": full_path, "status": "error", "error": str(e)})

        return {
            "success": success_count == len(files),
            "base_dir": abs_base,
            "files_created": success_count,
            "total_files": len(files),
            "details": results,
        }

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)

    def _detect_encoding(self, path: str) -> str:
        try:
            with open(path, 'rb') as f:
                raw = f.read(4)

            if raw[:3] == b'\xef\xbb\xbf':
                return 'utf-8-sig'
            if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
                return 'utf-16'

            with open(path, 'rb') as f:
                content = f.read(65536)
            try:
                content.decode('utf-8')
                return 'utf-8'
            except UnicodeDecodeError:
                pass
            for enc in ('gbk', 'gb2312', 'big5'):
                try:
                    content.decode(enc)
                    return enc
                except UnicodeDecodeError:
                    continue
        except Exception:
            pass
        return 'utf-8'

    def _cache_file(self, path: str, content: str):
        self._read_cache[path] = FileState(
            path=path,
            content=content,
            timestamp=os.path.getmtime(path),
        )

    def _atomic_write(self, path: str, content: str):
        dir_path = os.path.dirname(path) or '.'
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')

        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                if hasattr(os, 'fsync'):
                    os.fsync(f.fileno())

            if os.path.exists(path):
                stat_info = os.stat(path)
                os.replace(temp_path, path)
                os.chmod(path, stat_info.st_mode)
            else:
                os.replace(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

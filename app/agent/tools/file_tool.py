# -*- coding: utf-8 -*-

import json
import os
import logging
from typing import Dict, Any, Optional

from .base_tool import BaseTool

logger = logging.getLogger(__name__)

_MAX_LIST_ENTRIES = 200


class FileTool(BaseTool):

    name = "file"
    description = (
        "File system operations and project structure exploration.\n\n"
        "Usage:\n"
        "- command=fs, action=list: list directory contents\n"
        "- command=fs, action=mkdir: create directory\n"
        "- command=fs, action=delete: delete file or directory\n"
        "- command=fs, action=exists: check if path exists\n"
        "- command=fs, action=create: create files from a dict of path->content. "
        "REQUIRED: files must be a dict[str, str] mapping relative file paths to content\n"
        "- command=insight, mode=structure: view file or directory structure outline\n"
        "- command=insight, mode=symbol: search for symbol definitions\n"
        "- command=insight, mode=files: search for file names\n\n"
        "Use fs for directory/file system operations. Use insight to explore project structure when you don't know where things are."
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "file"

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
                return {"success": False, "error": "path is required"}
            return self._fs_mkdir(path, parents)

        elif action == "delete":
            if not path:
                return {"success": False, "error": "path is required"}
            return self._fs_delete(path, recursive)

        elif action == "exists":
            if not path:
                return {"success": False, "error": "path is required"}
            return self._fs_exists(path)

        elif action == "create":
            if not path or not files:
                return {"success": False, "error": "path and files are required. Example: file(command='fs', action='create', path='/target/dir', files={'relative/path.py': 'content'})"}
            return self._fs_create(path, files)

        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    def _cmd_insight(
        self,
        query: str = None,
        mode: str = "structure",
        path: str = ".",
        pattern: str = None,
        ext: str = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)

        if mode == "structure":
            if os.path.isfile(abs_path):
                return self._extract_structure(abs_path)
            elif os.path.isdir(abs_path):
                return self._dir_structure(abs_path, offset)
            else:
                return {"success": False, "error": f"Path not found: {abs_path}"}

        elif mode == "symbol":
            if not query:
                return {"success": False, "error": "mode=symbol requires query"}
            return self._symbol_search(abs_path, query, pattern, ext, offset)

        elif mode == "files":
            return self._file_search(abs_path, query or pattern or "*", offset)

        else:
            return {"success": False, "error": f"Unknown mode: {mode}"}

    def _fs_list(self, path: str, pattern: str, detail: bool) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"Directory not found: {abs_path}"}

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
            if parents:
                os.makedirs(abs_path, exist_ok=True)
            else:
                os.mkdir(abs_path)
            return {"success": True, "path": abs_path}
        except FileExistsError:
            return {"success": False, "error": f"Directory already exists: {abs_path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _fs_delete(self, path: str, recursive: bool) -> Dict[str, Any]:
        import shutil
        abs_path = self._resolve_path(path)
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"Path not found: {abs_path}"}
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
            elif os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                else:
                    os.rmdir(abs_path)
            return {"success": True, "path": abs_path}
        except OSError as e:
            if "Directory not empty" in str(e):
                return {"success": False, "error": "Directory not empty, use recursive=True"}
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
                return {"success": False, "error": f"files JSON parse failed: {e}"}
        if not isinstance(files, dict):
            return {"success": False, "error": f"files must be a dict, got {type(files).__name__}"}
        try:
            os.makedirs(abs_base, exist_ok=True)
        except Exception as e:
            return {"success": False, "error": f"Cannot create directory: {e}"}
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

    def _extract_structure(self, filepath: str) -> Dict[str, Any]:
        from ..runtime.context import SymbolSummary
        ext = os.path.splitext(filepath)[1].lower()
        encoding = self._detect_encoding(filepath)
        try:
            with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()
            result = SymbolSummary.extract(content, ext)
            return {
                "success": True,
                "path": filepath,
                "data": result["summary"],
                "symbols": result["symbols"],
                "total_symbols": result["total_symbols"],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _dir_structure(self, path: str, offset: int = 0, max_depth: int = 3) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        lines = []
        total_count = 0

        def walk(curr, depth, listed):
            nonlocal total_count
            if depth > max_depth:
                return
            try:
                entries = sorted(os.listdir(curr))
            except PermissionError:
                return
            for name in entries:
                if name.startswith('.'):
                    continue
                full = os.path.join(curr, name)
                is_dir = os.path.isdir(full)
                prefix = "  " * depth + ("+ " if is_dir else "  ")
                line = prefix + name
                if not listed:
                    lines.append((line, is_dir, full))
                    total_count += 1
                if is_dir and depth < max_depth:
                    walk(full, depth + 1, listed)

        walk(abs_path, 0, False)

        limit = 200
        page = [l for l, _, _ in lines[offset:offset + limit]]
        return {
            "success": True,
            "path": abs_path,
            "data": '\n'.join(page),
            "total": total_count,
            "returned": len(page),
            "offset": offset,
        }

    def _symbol_search(self, root: str, query: str, pattern: str, ext_filter: str, offset: int) -> Dict[str, Any]:
        from ..runtime.context import SymbolSummary
        hits = []
        ext = ext_filter or ".py"
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for filename in filenames:
                if not filename.endswith(ext):
                    continue
                filepath = os.path.join(dirpath, filename)
                try:
                    encoding = self._detect_encoding(filepath)
                    with open(filepath, 'r', encoding=encoding, errors='replace') as f:
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
            "query": query,
            "hits": page,
            "total": total,
            "offset": offset,
        }

    def _file_search(self, root: str, pattern: str, offset: int) -> Dict[str, Any]:
        import fnmatch
        hits = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for filename in filenames:
                if pattern and fnmatch.fnmatch(filename, pattern):
                    filepath = os.path.join(dirpath, filename)
                    hits.append({"file": filepath, "name": filename})
                if len(hits) >= 100:
                    break
            if len(hits) >= 100:
                break
        total = len(hits)
        page = hits[offset:offset + 20]
        return {
            "success": True,
            "pattern": pattern or "*",
            "hits": page,
            "total": total,
        }

    def _match_pattern(self, filename: str, pattern: str) -> bool:
        import fnmatch
        return fnmatch.fnmatch(filename, pattern)

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

    def _atomic_write(self, path: str, content: str):
        import tempfile
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

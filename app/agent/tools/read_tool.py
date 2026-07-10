# -*- coding: utf-8 -*-

import os
import logging
from typing import Dict, Any

from .base_tool import BaseTool
from .file_cache import cache_read

logger = logging.getLogger(__name__)


class ReadTool(BaseTool):

    name = "read"
    description = (
        "Read a file from the local filesystem. You can access any file directly using this tool.\n\n"
        "Usage:\n"
        "- file_path must be an absolute path\n"
        "- By default, reads up to 2000 lines starting from the beginning\n"
        "- Use offset and limit to paginate large files\n"
        "- Use target to read content around a specific string\n"
        "- Results include line numbers\n\n"
        "NEVER use shell commands (cat, type, Get-Content) to read files. ALWAYS use this tool."
    )

    _MAX_FILE_SIZE = 2 * 1024 * 1024

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "read"

    def _cmd_read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        target: str = None,
    ) -> Dict[str, Any]:
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        abs_path = self._resolve_path(file_path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"File not found: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        file_size = os.path.getsize(abs_path)

        if file_size > self._MAX_FILE_SIZE and not target:
            return {
                "success": False,
                "error": f"File too large ({file_size/1024/1024:.1f}MB). Use target to read specific sections",
                "size": file_size,
            }

        try:
            with open(abs_path, "r", encoding=encoding, errors="replace") as f:
                content = f.read()
            lines = content.split('\n')
            cache_read(abs_path, content)
            total_lines = len(lines)

            if target:
                target_lower = target.lower()
                target_line = None
                for i, line in enumerate(lines):
                    if target_lower in line.lower():
                        target_line = i
                        break
                if target_line is None:
                    return {"success": False, "error": f"Target not found: {target[:50]}"}
                start = max(0, target_line - 3)
                end = min(total_lines, target_line + 4)
                selected_lines = lines[start:end]
                result_content = '\n'.join(f"{start + j + 1}\t{selected_lines[j]}" for j in range(len(selected_lines)))
                return {
                    "success": True,
                    "data": result_content,
                    "path": abs_path,
                    "lines": len(selected_lines),
                    "total_lines": total_lines,
                    "target_line": target_line + 1,
                    "start_line": start + 1,
                    "end_line": end,
                    "encoding": encoding,
                }

            if offset < 0:
                offset = 0
            if offset >= total_lines:
                return {"success": False, "error": f"offset {offset} exceeds total lines {total_lines}"}
            end = min(offset + limit, total_lines)
            selected_lines = lines[offset:end]
            result_content = '\n'.join(f"{offset + j + 1}\t{selected_lines[j]}" for j in range(len(selected_lines)))
            return {
                "success": True,
                "data": result_content,
                "path": abs_path,
                "lines": len(selected_lines),
                "total_lines": total_lines,
                "offset": offset,
                "truncated": end < total_lines,
                "encoding": encoding,
            }

        except Exception as e:
            return {"success": False, "error": f"Read failed: {e}"}

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

# -*- coding: utf-8 -*-

import os
import logging
from typing import Dict, Any, Optional

from .base_tool import BaseTool

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 200


class LSTool(BaseTool):

    name = "ls"
    description = (
        "Lists files and directories in a given path.\n\n"
        "Usage:\n"
        "- The path parameter must be an absolute path, not relative\n"
        "- You can optionally provide an array of glob patterns to ignore with the ignore parameter\n"
        "- Returns directory contents sorted alphabetically\n\n"
        "Use this for quick directory exploration. "
        "For pattern-based file search, use `glob` instead."
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "ls"

    def _cmd_ls(
        self,
        path: str,
        ignore: list = None,
    ) -> Dict[str, Any]:
        if not path:
            path = os.getcwd()

        abs_path = self._resolve_path(path)

        if not os.path.isdir(abs_path):
            return {"success": False, "error": f"Directory not found: {abs_path}"}

        ignore_patterns = ignore or []

        try:
            entries = []
            for name in sorted(os.listdir(abs_path)):
                # Skip hidden
                if name.startswith('.'):
                    continue
                # Apply ignore patterns
                if self._matches_any(name, ignore_patterns):
                    continue

                full = os.path.join(abs_path, name)
                try:
                    is_dir = os.path.isdir(full)
                except OSError:
                    continue

                entries.append({
                    "name": name,
                    "type": "dir" if is_dir else "file",
                })
                if len(entries) >= _MAX_ENTRIES:
                    break

            return {
                "success": True,
                "path": abs_path,
                "entries": entries,
                "count": len(entries),
                "truncated": len(entries) >= _MAX_ENTRIES,
            }
        except PermissionError:
            return {"success": False, "error": f"Permission denied: {abs_path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _matches_any(self, name: str, patterns: list) -> bool:
        import fnmatch
        for pat in patterns:
            if fnmatch.fnmatch(name, pat):
                return True
        return False

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)

# -*- coding: utf-8 -*-

import os
import logging
from typing import Dict, Any, Optional

from .base_tool import BaseTool

logger = logging.getLogger(__name__)


class GlobTool(BaseTool):

    name = "glob"
    description = (
        "Fast file pattern matching tool.\n\n"
        "Usage:\n"
        "- Supports glob patterns like `**/*.js` or `src/**/*.ts`\n"
        "- Returns matching file paths sorted by modification time (most recent first)\n"
        "- Default follows .gitignore\n\n"
        "Use this when you need to find files by name pattern. "
        "For searching file contents, use `grep` instead."
    )

    _MAX_RESULTS = 500

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "glob"

    def _cmd_glob(
        self,
        pattern: str,
        path: str = ".",
        limit: int = 100,
    ) -> Dict[str, Any]:
        if not pattern:
            return {"success": False, "error": "pattern is required. Example: glob(pattern='**/*.py')"}

        abs_path = self._resolve_path(path)

        import glob as py_glob
        try:
            search_pattern = os.path.join(abs_path, pattern.replace('/', os.sep))
            results = []
            for filepath in py_glob.iglob(search_pattern, recursive=True):
                if os.path.isfile(filepath):
                    results.append(filepath)
                if len(results) >= self._MAX_RESULTS:
                    break
        except Exception as e:
            return {"success": False, "error": f"Glob failed: {e}"}

        # Sort by mtime (most recent first)
        try:
            results.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except OSError:
            pass

        # Make paths relative to search root
        rel_results = []
        for r in results[:limit]:
            try:
                rel_results.append(os.path.relpath(r, abs_path))
            except ValueError:
                rel_results.append(r)

        return {
            "success": True,
            "path": abs_path,
            "pattern": pattern,
            "files": rel_results,
            "count": len(rel_results),
            "truncated": len(results) > limit,
        }

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)

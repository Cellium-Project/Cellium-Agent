# -*- coding: utf-8 -*-

import os
import re
import logging
from typing import Dict, Any, Optional

from .base_tool import BaseTool

logger = logging.getLogger(__name__)


class GrepTool(BaseTool):

    name = "grep"
    description = (
        "Search file contents with keywords or regex patterns.\n\n"
        "Usage:\n"
        "- ALWAYS use Grep for content search. NEVER use shell grep/rg/findstr.\n"
        "- path defaults to current directory\n"
        "- Use pattern to filter file names (glob, e.g. \"*.py\")\n"
        "- Use ext to filter by extension (e.g. \".py\")\n"
        "- The query supports basic regex\n\n"
        "Typical flow: Grep (find files) -> Read (read them) -> Edit (modify them)"
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "grep"

    def _cmd_grep(
        self,
        query: str,
        path: str = ".",
        pattern: str = None,
        ext: str = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        if not query:
            return {"success": False, "error": "query is required"}

        abs_path = self._resolve_path(path)
        hits = []
        use_regex = any(c in query for c in '*+?^$[]|(){}')

        for dirpath, dirnames, filenames in os.walk(abs_path):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]

            for filename in filenames:
                if ext and not filename.endswith(ext):
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
            "query": query,
            "hits": page,
            "total": total,
            "offset": offset,
            "has_more": offset + 20 < total,
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

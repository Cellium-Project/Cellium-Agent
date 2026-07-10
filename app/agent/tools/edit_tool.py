# -*- coding: utf-8 -*-

import os
import logging

from .base_tool import BaseTool
from .file_cache import is_file_read

logger = logging.getLogger(__name__)


def _unescape_string(value: str) -> str:
    if not isinstance(value, str):
        return value
    if '\\' not in value:
        return value
    chars = []
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == 'n':
                chars.append('\n')
                i += 2
            elif nxt == 't':
                chars.append('\t')
                i += 2
            elif nxt == 'r':
                chars.append('\r')
                i += 2
            elif nxt == '"':
                chars.append('"')
                i += 2
            elif nxt == "'":
                chars.append("'")
                i += 2
            elif nxt == '\\':
                chars.append('\\')
                i += 2
            else:
                chars.append(value[i])
                i += 1
        else:
            chars.append(value[i])
            i += 1
    return ''.join(chars)


class EditTool(BaseTool):

    name = "edit"
    description = (
        "Performs exact string replacements in files.\n\n"
        "Usage:\n"
        "- When editing text from Read tool output, preserve the exact indentation (tabs/spaces) as it appears.\n"
        "- ALWAYS prefer editing existing files. NEVER write new files unless explicitly required.\n"
        "- The edit will FAIL if old_string is not unique in the file. Either provide a larger string with more surrounding context or use replace_all to change every instance.\n"
        "- Use replace_all for replacing and renaming all occurrences.\n\n"
        "ALWAYS prefer this tool over shell commands for file modifications."
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "edit"

    def _cmd_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ):
        old_string = _unescape_string(old_string)
        new_string = _unescape_string(new_string)

        if not file_path:
            return {"success": False, "error": "file_path is required"}
        if not old_string:
            return {"success": False, "error": "old_string is required"}
        if new_string is None:
            return {"success": False, "error": "new_string is required"}
        if old_string == new_string:
            return {"success": False, "error": "old_string and new_string are identical"}

        abs_path = self._resolve_path(file_path)

        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File not found: {abs_path}"}

        if not is_file_read(abs_path):
            return {"success": False, "error": "File has not been read yet. Use the Read tool first before editing."}

        try:
            with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Cannot read file: {e}"}

        file_content = file_content.replace('\r\n', '\n')
        old_string = old_string.replace('\r\n', '\n')
        new_string = new_string.replace('\r\n', '\n')

        matches = file_content.count(old_string)
        if matches == 0:
            return {"success": False, "error": f"old_string not found in file. Make sure it matches exactly, including whitespace and indentation."}
        if matches > 1 and not replace_all:
            return {"success": False, "error": f"Found {matches} matches of old_string but replace_all is false. Add more surrounding context to make it unique, or set replace_all=true to replace all."}

        from ..runtime.transaction import EditTransaction

        tx = EditTransaction(workspace=os.path.dirname(abs_path) or os.getcwd())
        tx.begin()

        try:
            step = tx.create_step(abs_path, old_string, new_string, replace_all=replace_all)
            result = tx.commit_step(step, validate=True)

            if result["success"]:
                return {
                    "success": True,
                    "path": abs_path,
                    "count": result.get("count", 0),
                    "diff": result.get("diff", ""),
                }
            else:
                return {
                    "success": False,
                    "error": result.get("error"),
                    "rolled_back": result.get("rolled_back", False),
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)

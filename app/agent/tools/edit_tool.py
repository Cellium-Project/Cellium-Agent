# -*- coding: utf-8 -*-

import os
import logging

from .base_tool import BaseTool
from .file_cache import get_read_state, touch_read_state

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


def _normalize_quotes(text: str) -> str:
    replacements = [
        ('\u2018', "'"),
        ('\u2019', "'"),
        ('\u201c', '"'),
        ('\u201d', '"'),
        ('\u2013', '-'),
        ('\u2014', '--'),
        ('\u2026', '...'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _strip_trailing_whitespace(text: str) -> str:
    return '\n'.join(line.rstrip() for line in text.split('\n'))


def _trim_blank_lines(text: str) -> str:
    lines = text.split('\n')
    start = 0
    end = len(lines)
    while start < end and lines[start].strip() == '':
        start += 1
    while end > start and lines[end - 1].strip() == '':
        end -= 1
    return '\n'.join(lines[start:end])


def _strip_leading_ws_lines(text: str) -> str:
    return '\n'.join(line.lstrip() for line in text.split('\n'))


def _find_match_with_tolerance(file_content: str, old_string: str):

    strategies = [
        old_string,
        _trim_blank_lines(old_string),
    ]

    for candidate in strategies:
        if candidate and candidate in file_content:
            return candidate, file_content.count(candidate), False

    norm_old = _normalize_quotes(old_string)
    norm_content = _normalize_quotes(file_content)
    for candidate in [old_string, _trim_blank_lines(old_string)]:
        nc = _normalize_quotes(candidate)
        if nc and nc in norm_content:
            idx = norm_content.index(nc)
            actual = file_content[idx:idx + len(candidate)]
            return actual, file_content.count(actual), True

    stripped_old_lines = [l.lstrip() for l in old_string.split('\n')]
    stripped_file_lines = [l.lstrip() for l in file_content.split('\n')]
    n_old = len(stripped_old_lines)
    if n_old and stripped_old_lines[0]:
        file_lines = file_content.split('\n')
        for i in range(len(stripped_file_lines) - n_old + 1):
            if stripped_file_lines[i:i + n_old] == stripped_old_lines:
                actual = '\n'.join(file_lines[i:i + n_old])
                return actual, file_content.count(actual), False

    first_line = old_string.split('\n')[0].strip()
    if first_line and first_line in file_content:
        for i, line in enumerate(file_content.split('\n')):
            if line.strip() == first_line:
                candidate = '\n'.join(file_content.split('\n')[i:i + len(old_string.split('\n'))])
                if candidate == old_string:
                    return old_string, file_content.count(old_string), False

    return None, 0, False


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

        read_state = get_read_state(abs_path)
        if read_state is None:
            return {"success": False, "error": "File has not been read yet. Use the Read tool first before editing."}
        cached_encoding = read_state.get("encoding") if read_state else None
        if cached_encoding:
            file_encoding = cached_encoding
        else:
            try:
                file_encoding = self._detect_encoding(abs_path)
            except Exception:
                file_encoding = "utf-8"

        if read_state:
            try:
                current_mtime = os.path.getmtime(abs_path)
            except OSError:
                return {"success": False, "error": "Cannot access file mtime"}

            if current_mtime > read_state["timestamp"]:
                is_full = read_state.get("offset") is None and read_state.get("limit") is None
                if is_full:
                    try:
                        with open(abs_path, 'r', encoding=file_encoding, errors='replace') as f:
                            file_content = f.read()
                        if read_state["content"] != file_content.replace('\r\n', '\n'):
                            return {"success": False, "error": "File has been modified since read. Read it again before editing."}
                    except Exception:
                        return {"success": False, "error": "Cannot re-verify file content"}
                else:
                    return {"success": False, "error": "File timestamp changed since partial read. Read the full file first."}

        try:
            with open(abs_path, 'r', encoding=file_encoding, errors='replace') as f:
                file_content = f.read()
        except Exception as e:
            return {"success": False, "error": f"Cannot read file: {e}"}

        file_content = file_content.replace('\r\n', '\n')
        old_string = old_string.replace('\r\n', '\n')
        new_string = new_string.replace('\r\n', '\n')

        old_string = _strip_trailing_whitespace(old_string) if old_string else old_string

        if old_string == new_string:
            return {"success": False, "error": "old_string and new_string are identical"}

        matched, matches, used_normalize = _find_match_with_tolerance(file_content, old_string)
        if matches == 0:
            return {"success": False, "error": "old_string not found in file. Make sure it matches exactly, including whitespace and indentation."}

        old_string = matched

        if matches > 1 and not replace_all:
            locations = []
            first_line = old_string.split('\n')[0].rstrip()
            if first_line:
                for idx, line in enumerate(file_content.split('\n'), 1):
                    if line.startswith(first_line) or first_line in line:
                        locations.append(str(idx))
                        if len(locations) >= 5:
                            break
            loc_hint = f" (lines: {', '.join(locations)})" if locations else ""
            return {"success": False, "error": f"Found {matches} matches of old_string but replace_all is false{loc_hint}. Add more surrounding context to make it unique, or set replace_all=true to replace all."}

        current_content = self._re_read_file(abs_path, file_encoding)
        if current_content is None:
            return {"success": False, "error": "File disappeared before edit could be applied."}

        current_content = current_content.replace('\r\n', '\n')
        reconciled, re_matches, _ = _find_match_with_tolerance(current_content, old_string)
        if re_matches == 0:
            return {"success": False, "error": "File has changed since read — old_string no longer found. Read the file again."}

        from ..runtime.transaction import EditTransaction

        patch = {"mode": "replace", "old_text": reconciled, "new_text": new_string, "replace_all": replace_all}
        result = EditTransaction.apply_edit(abs_path, current_content, patch, encoding=file_encoding)

        if result["success"]:
            touch_read_state(abs_path, current_content.replace('\r\n', '\n'), encoding=file_encoding)
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

    def _re_read_file(self, abs_path: str, encoding: str = "utf-8"):
        try:
            with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                return f.read()
        except Exception:
            return None

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

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)

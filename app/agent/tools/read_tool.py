# -*- coding: utf-8 -*-

import os
import logging
from typing import Dict, Any, Optional, Tuple

from .base_tool import BaseTool
from .file_cache import cache_read, get_read_state

logger = logging.getLogger(__name__)

_CHUNK = 8 * 1024
_MAX_SCAN = 10 * 1024 * 1024


def _find_needle_position(bytes_data: bytes, needle: bytes) -> int:
    pos = bytes_data.find(needle)
    if pos != -1:
        return pos, len(needle)
    if b'\n' in needle:
        crlf = needle.replace(b'\n', b'\r\n')
        pos = bytes_data.find(crlf)
        if pos != -1:
            return pos, len(crlf)
    return -1, 0


def _scan_file_for_needle(file_path: str, needle: str) -> Tuple[int, int, int]:

    needle_bytes = needle.encode('utf-8')
    nl_count = needle_bytes.count(ord('\n'))
    overlap = len(needle_bytes) + nl_count - 1
    if overlap < 0:
        overlap = 0

    with open(file_path, 'rb') as f:
        buf = bytearray(_CHUNK + overlap)
        pos = 0
        lines_before_pos = 0
        prev_tail = 0

        while pos < _MAX_SCAN:
            buf_view = memoryview(buf)
            read_bytes = f.readinto(buf_view[prev_tail:prev_tail + _CHUNK])
            if read_bytes == 0:
                break
            view_len = prev_tail + read_bytes
            view = buf_view[:view_len]

            match_at, match_len = _find_needle_position(bytes(view), needle_bytes)
            if match_at != -1:
                abs_match = pos - prev_tail + match_at
                lines_before_needle = lines_before_pos + bytes(view[:match_at]).count(ord('\n'))
                return abs_match, match_len, lines_before_needle

            pos += read_bytes

            next_tail = min(overlap, view_len)
            newline_count = bytes(view[:view_len - next_tail]).count(ord('\n'))
            lines_before_pos += newline_count
            prev_tail = next_tail
            buf[:prev_tail] = view[view_len - prev_tail:view_len]

        return -1, 0, 0


def _read_byte_range(file_path: str, byte_start: int, byte_end: int) -> str:
    with open(file_path, 'rb') as f:
        f.seek(byte_start)
        data = f.read(byte_end - byte_start)
    text = data.decode('utf-8', errors='replace')
    if '\r' in text:
        text = text.replace('\r\n', '\n')
    return text


def _extract_context_around(file_path: str, needle: str, context_lines: int) -> Dict[str, Any]:
    match_start, match_len, lines_before = _scan_file_for_needle(file_path, needle)
    if match_start == -1:
        return {"found": False, "error": f"needle not found in file (scanned first {_MAX_SCAN // 1024 // 1024}MB)"}

    match_end = match_start + match_len

    with open(file_path, 'rb') as f:
        f.seek(max(0, match_start - _CHUNK))
        back_data = f.read(min(match_start, _CHUNK))
        nl_seen = 0
        ctx_start = match_start
        for i in range(len(back_data) - 1, -1, -1):
            if back_data[i] == ord('\n'):
                nl_seen += 1
                if nl_seen > context_lines:
                    break
            ctx_start -= 1

        f.seek(match_end)
        fwd_data = f.read(_CHUNK)
        ctx_end = match_end
        nl_seen = 0
        for i in range(len(fwd_data)):
            ctx_end += 1
            if fwd_data[i] == ord('\n'):
                nl_seen += 1
                if nl_seen >= context_lines + 1:
                    break

    back_len = len(back_data)
    walked_back = match_start - ctx_start
    start_in_back = back_len - walked_back
    if start_in_back < 0:
        start_in_back = 0
    nl_in_walked = back_data[start_in_back:].count(ord('\n'))
    line_offset = lines_before - nl_in_walked + 1

    content = _read_byte_range(file_path, ctx_start, ctx_end)
    match_line = lines_before + 1

    return {
        "found": True,
        "context": content,
        "line_offset": max(1, line_offset),
        "match_line": match_line,
        "match_column": 1,
    }


def _read_file_streaming(file_path: str, offset: int, limit: int, encoding: str = "utf-8") -> Dict[str, Any]:
    with open(file_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        total_bytes = f.tell()

        scan_end = min(total_bytes, _MAX_SCAN * 2)

        f.seek(0)
        buf = b''
        pos = 0
        while pos < scan_end:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            buf += chunk
            pos += len(chunk)
        text = buf.decode(encoding, errors='replace')
        if '\r' in text:
            text = text.replace('\r\n', '\n')

        f.seek(0)
        full_text = f.read(_MAX_SCAN * 2).decode(encoding, errors='replace')
        if '\r' in full_text:
            full_text = full_text.replace('\r\n', '\n')

    lines = text.split('\n')
    full_text_lines = full_text.split('\n')

    if offset < 0:
        offset = 0
    if offset >= len(lines):
        return {"success": False, "error": f"offset {offset} exceeds total lines {len(lines)}"}

    total_lines = len(full_text_lines)
    end = min(offset + limit, total_lines)
    selected = full_text_lines[offset:end]
    result_content = '\n'.join(f"{offset + j + 1}\t{selected[j]}" for j in range(len(selected)))

    read_all_bytes = pos >= total_bytes
    is_partial = (offset > 0) or not read_all_bytes

    cache_read(file_path, full_text,
               offset=None if not is_partial else offset,
               limit=None if not is_partial else limit,
               encoding=encoding)

    return {
        "success": True,
        "path": file_path,
        "data": result_content,
        "lines": len(selected),
        "total_lines": total_lines,
        "offset": offset,
        "truncated": end < total_lines,
    }


class ReadTool(BaseTool):

    name = "read"
    description = (
        "Read a file from the local filesystem. You can access any file directly using this tool.\n\n"
        "Usage:\n"
        "- file_path must be an absolute path\n"
        "- By default, reads up to 2000 lines starting from the beginning\n"
        "- Use offset and limit to paginate large files\n"
        "- Use target to read content around a specific string\n"
        "- Use needle to find exact match context for editing (returns ±3 lines around match with line numbers)\n\n"
        "NEVER use shell commands (cat, type, Get-Content) to read files. ALWAYS use this tool."
    )

    _MAX_FILE_SIZE = 20 * 1024 * 1024

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []
        self._dedup_entries = {}

    @property
    def tool_name(self) -> str:
        return "read"

    def _check_dedup(self, abs_path: str, offset: int, limit: int) -> Optional[Dict[str, Any]]:
        cached = get_read_state(abs_path)
        if not cached:
            return None
        key = (abs_path, offset, limit)
        prev = self._dedup_entries.get(key)
        if prev:
            return {"success": True, "file_unchanged": True, "path": abs_path, "lines": prev["lines"], "_dedup": True}
        return None

    def _mark_dedup(self, abs_path: str, offset: int, limit: int, lines: int):
        self._dedup_entries[(abs_path, offset, limit)] = {"lines": lines}

    def _cmd_read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        target: str = None,
        needle: str = None,
    ) -> Dict[str, Any]:
        if not file_path:
            return {"success": False, "error": "file_path is required"}

        abs_path = self._resolve_path(file_path)
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"File not found: {abs_path}"}

        dedup_result = self._check_dedup(abs_path, offset, limit)
        if dedup_result:
            return dedup_result

        file_size = os.path.getsize(abs_path)

        if needle:
            return self._cmd_read_edit_context(abs_path, needle)

        if file_size > self._MAX_FILE_SIZE and not target:
            return {
                "success": False,
                "error": f"File too large ({file_size / 1024 / 1024:.1f}MB). Use offset/limit or target to read specific sections",
                "size": file_size,
            }

        try:
            result = self._do_read(abs_path, file_size, offset, limit, target)
            return result
        except Exception as e:
            return {"success": False, "error": f"Read failed: {e}"}

    def _cmd_read_edit_context(
        self,
        file_path: str,
        needle: str,
        context_lines: int = 3,
    ) -> Dict[str, Any]:
        if not needle:
            return {"success": False, "error": "needle is required"}

        result = _extract_context_around(file_path, needle, context_lines)
        if not result["found"]:
            return {"success": False, "error": result["error"]}

        context = result["context"]
        lines = context.split('\n')
        numbered = '\n'.join(f"{result['line_offset'] + j}\t{lines[j]}" for j in range(len(lines)))

        cache_read(file_path, context, offset=0, limit=0)

        return {
            "success": True,
            "mode": "edit_context",
            "path": file_path,
            "data": numbered,
            "lines": len(lines),
            "line_offset": result["line_offset"],
            "match_line": result["match_line"],
            "match_column": result["match_column"],
        }

    def _do_read(
        self,
        abs_path: str,
        file_size: int,
        offset: int,
        limit: int,
        target: str,
    ) -> Dict[str, Any]:
        if file_size > _CHUNK * 4:
            encoding = self._detect_encoding(abs_path)
            return _read_file_streaming(abs_path, offset, limit, encoding=encoding)

        encoding = self._detect_encoding(abs_path)
        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        content = content.replace('\r\n', '\n')
        lines = content.split('\n')
        total_lines = len(lines)

        is_partial = (offset is not None and offset > 0) or (limit is not None and limit < total_lines)
        cache_read(abs_path, content, offset=None if not is_partial else offset, limit=None if not is_partial else limit, encoding=encoding)

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
        self._mark_dedup(abs_path, offset, limit, len(selected_lines))
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

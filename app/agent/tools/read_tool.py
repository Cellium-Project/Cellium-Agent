# -*- coding: utf-8 -*-

import os
import logging
import threading
from typing import Dict, Any, Optional, Tuple

from .base_tool import BaseTool
from .file_cache import cache_read, get_read_state, is_file_read
from app.core.util.image_info import VISION_EXTS

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


def _scan_file_for_needle(file_path: str, needle: str) -> Tuple[int, int, int, bool]:

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
        truncated = False

        while True:
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
                return abs_match, match_len, lines_before_needle, False

            pos += read_bytes

            if pos >= _MAX_SCAN:
                truncated = True
                break

            next_tail = min(overlap, view_len)
            newline_count = bytes(view[:view_len - next_tail]).count(ord('\n'))
            lines_before_pos += newline_count
            prev_tail = next_tail
            buf[:prev_tail] = view[view_len - prev_tail:view_len]

        return -1, 0, 0, truncated


def _read_byte_range(file_path: str, byte_start: int, byte_end: int, encoding: str = "utf-8") -> str:
    with open(file_path, 'rb') as f:
        f.seek(byte_start)
        data = f.read(byte_end - byte_start)
    text = data.decode(encoding, errors='replace')
    if '\r' in text:
        text = text.replace('\r\n', '\n')
    return text


def _extract_context_around(file_path: str, needle: str, context_lines: int, encoding: str = "utf-8") -> Dict[str, Any]:
    match_start, match_len, lines_before, truncated = _scan_file_for_needle(file_path, needle)
    if match_start == -1:
        if truncated:
            return {"found": False, "error": f"needle not found in file (scanned first {_MAX_SCAN // 1024 // 1024}MB, scan truncated)"}
        return {"found": False, "error": f"needle not found in file (full scan completed)"}

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

    content = _read_byte_range(file_path, ctx_start, ctx_end, encoding)
    match_line = lines_before + 1

    return {
        "found": True,
        "context": content,
        "line_offset": max(1, line_offset),
        "match_line": match_line,
        "match_column": 1,
    }


def _count_lines_in_file(file_path: str) -> int:
    count = 0
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(_CHUNK * 16)
            if not chunk:
                break
            count += chunk.count(b'\n')
    with open(file_path, 'rb') as f:
        last = f.seek(0, os.SEEK_END)
        if last > 0:
            f.seek(last - 1)
            if f.read(1) != b'\n':
                count += 1
    return max(count, 1)


def _read_file_streaming(file_path: str, offset: int, limit: int, encoding: str = "utf-8", session_id: str = None) -> Dict[str, Any]:
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
    scanned_lines = len(lines)

    if total_bytes > _MAX_SCAN * 2:
        total_lines = _count_lines_in_file(file_path)
    else:
        total_lines = scanned_lines

    if offset < 0:
        offset = 0
    if offset >= total_lines:
        return {"success": False, "error": f"offset {offset} exceeds total lines {total_lines}"}
    if offset >= scanned_lines:
        return {
            "success": False,
            "error": f"offset {offset} 超出当前可流式扫描范围（前 {_MAX_SCAN * 2 // 1024 // 1024}MB 共 {scanned_lines} 行），文件实际共 {total_lines} 行。请用 needle 或缩小读取范围。",
            "scanned_lines": scanned_lines,
            "total_lines": total_lines,
            "truncated_at_bytes": _MAX_SCAN * 2,
        }

    end = min(offset + limit, total_lines)
    end_in_scan = min(end, scanned_lines)
    selected = full_text_lines[offset:end_in_scan]
    result_content = '\n'.join(f"{offset + j + 1}\t{selected[j]}" for j in range(len(selected)))

    read_all_bytes = pos >= total_bytes
    is_partial = (offset > 0) or not read_all_bytes

    cache_read(file_path, full_text,
               offset=None if not is_partial else offset,
               limit=None if not is_partial else limit,
               encoding=encoding,
               session_id=session_id)

    result = {
        "success": True,
        "path": file_path,
        "data": result_content,
        "lines": len(selected),
        "total_lines": total_lines,
        "offset": offset,
        "truncated": end < total_lines,
    }
    if total_bytes > _MAX_SCAN * 2:
        result["truncated_at_bytes"] = _MAX_SCAN * 2
        result["scanned_lines"] = scanned_lines
    return result


class ReadTool(BaseTool):

    name = "read"
    description = (
        "Read a file from the local filesystem. You can access any file directly using this tool.\n\n"
        "Usage:\n"
        "- file_path must be an absolute path\n"
        "- By default, reads up to 2000 lines starting from the beginning\n"
        "- Use offset and limit to paginate large files\n"
        "- Use target to read content around a specific string\n"
        "- Use needle to find exact match context for editing (returns ±3 lines around match with line numbers)\n"
        "- 图片文件（jpg/jpeg/png/gif/webp）会直接加入模型视野，只对下一次模型调用生效\n\n"
        "NEVER use shell commands (cat, type, Get-Content) to read files. ALWAYS use this tool."
    )

    _MAX_FILE_SIZE = 20 * 1024 * 1024

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []
        self._tls = threading.local()

    def execute_with_context(self, arguments, session_id=None, platform_context=None):
        self._tls.session_id = session_id
        try:
            return self.execute(arguments)
        finally:
            self._tls.session_id = None

    def _session_id(self):
        return getattr(self._tls, "session_id", None)

    def _check_dedup(self, abs_path: str, offset: int, limit: int) -> Optional[Dict[str, Any]]:
        session_id = self._session_id()
        cached = get_read_state(abs_path, session_id=session_id)
        if not cached:
            return None
        if not is_file_read(abs_path, session_id=session_id):
            return None
        cached_offset = cached.get("offset")
        cached_limit = cached.get("limit")
        cached_content = cached.get("content", "")
        lines = cached_content.split('\n')
        total_lines = len(lines)
        
        if cached_offset is None and cached_limit is None:
            pass
        elif cached_offset is None and cached_limit is not None:
            if offset != 0:
                return None
            if limit > cached_limit:
                return None
        elif cached_offset is not None and cached_limit is None:
            if offset < cached_offset:
                return None
        else:
            if cached_offset != offset or cached_limit != limit:
                return None
        
        end = min(offset + limit, total_lines)
        selected_lines = lines[offset:end]
        result_content = '\n'.join(f"{offset + j + 1}\t{selected_lines[j]}" for j in range(len(selected_lines)))
        return {
            "success": True,
            "file_unchanged": True,
            "path": abs_path,
            "data": result_content,
            "lines": len(selected_lines),
            "total_lines": total_lines,
            "offset": offset,
            "truncated": end < total_lines,
            "_dedup": True,
        }

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
        if os.path.isdir(abs_path):
            return {"success": False, "error": f"Is a directory: {abs_path}"}
        if not os.path.exists(abs_path):
            return {"success": False, "error": f"File not found: {abs_path}"}
        if not os.path.isfile(abs_path):
            return {"success": False, "error": f"Not a regular file: {abs_path}"}

        if os.path.splitext(abs_path)[1].lower() in VISION_EXTS:
            return self._read_image(abs_path)

        # 带 target/needle 时不能走内容去重，否则会返回上次缓存的整份内容
        if not target and not needle:
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

    def _read_image(self, abs_path: str) -> Dict[str, Any]:
        from app.core.util.attachment_store import image_to_part

        part = image_to_part(abs_path)
        if part is None:
            return {"success": False, "error": f"图片读取失败或超过 4MB: {abs_path}"}
        return {
            "success": True,
            "path": abs_path,
            "image": {
                "path": abs_path,
                "filename": part["filename"],
                "width": part["width"],
                "height": part["height"],
            },
            "data": f"[图片] {part['filename']} ({part['width']}x{part['height']})",
            "_image_refs": [abs_path],
        }

    def _cmd_read_edit_context(
        self,
        file_path: str,
        needle: str,
        context_lines: int = 3,
    ) -> Dict[str, Any]:
        if not needle:
            return {"success": False, "error": "needle is required"}

        encoding = self._detect_encoding(file_path)
        result = _extract_context_around(file_path, needle, context_lines, encoding)
        if not result["found"]:
            return {"success": False, "error": result["error"]}

        context = result["context"]
        lines = context.split('\n')
        numbered = '\n'.join(f"{result['line_offset'] + j}\t{lines[j]}" for j in range(len(lines)))

        cache_read(file_path, context,
                   offset=result["line_offset"],
                   limit=len(context.split('\n')),
                   encoding=encoding,
                   session_id=self._session_id())

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

    def _read_target_streaming(
        self,
        abs_path: str,
        target: str,
        encoding: str,
        context_lines: int = 3,
    ) -> Dict[str, Any]:
        """大文件按 target 流式定位，只保留命中行前后 N 行"""
        target_lower = target.lower()
        before, after = [], []
        match_no = 0
        match_text = ""
        total_lines = 0

        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            for i, raw in enumerate(f, 1):
                total_lines = i
                line = raw.rstrip("\n")
                if not match_no:
                    if target_lower in line.lower():
                        match_no = i
                        match_text = line
                    else:
                        before.append(line)
                        if len(before) > context_lines:
                            before.pop(0)
                elif len(after) < context_lines:
                    after.append(line)

        if not match_no:
            return {"success": False, "error": f"Target not found: {target[:50]}"}

        selected = before + [match_text] + after
        start_no = match_no - len(before)
        data = "\n".join(f"{start_no + j}\t{selected[j]}" for j in range(len(selected)))
        return {
            "success": True,
            "data": data,
            "path": abs_path,
            "lines": len(selected),
            "total_lines": total_lines,
            "target_line": match_no,
            "start_line": start_no,
            "end_line": start_no + len(selected) - 1,
            "encoding": encoding,
        }

    def _do_read(
        self,
        abs_path: str,
        file_size: int,
        offset: int,
        limit: int,
        target: str,
    ) -> Dict[str, Any]:
        encoding = self._detect_encoding(abs_path)

        if target and file_size > _CHUNK * 4:
            return self._read_target_streaming(abs_path, target, encoding)

        if file_size > _CHUNK * 4:
            return _read_file_streaming(abs_path, offset, limit, encoding=encoding, session_id=self._session_id())

        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        content = content.replace('\r\n', '\n')
        lines = content.split('\n')
        total_lines = len(lines)

        is_partial = (offset is not None and offset > 0) or (limit is not None and limit < total_lines)
        cache_read(abs_path, content, offset=None if not is_partial else offset, limit=None if not is_partial else limit, encoding=encoding, session_id=self._session_id())

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
        result = {
            "success": True,
            "data": result_content,
            "path": abs_path,
            "lines": len(selected_lines),
            "total_lines": total_lines,
            "offset": offset,
            "truncated": end < total_lines,
            "encoding": encoding,
        }
        return result

    def _resolve_path(self, path: str) -> str:
        from app.core.util.workdir import resolve_path
        return resolve_path(path)

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

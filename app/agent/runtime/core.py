# -*- coding: utf-8 -*-
import os
import uuid
import tempfile
import shutil
from dataclasses import dataclass
from typing import Dict, Optional, Any
from pathlib import Path

@dataclass
class Snapshot:
    id: str
    path: str
    content: str
    timestamp: float

class CodeRuntime:
    def __init__(self, workspace_root: str = None):
        self.workspace_root = workspace_root or os.getcwd()
        self._snapshots: Dict[str, Snapshot] = {}
        self._encoding_cache: Dict[str, str] = {}

    def _resolve_path(self, path: str) -> str:
        if not os.path.isabs(path):
            return os.path.join(self.workspace_root, path)
        return path

    def _detect_encoding(self, path: str) -> str:
        if path in self._encoding_cache:
            return self._encoding_cache[path]

        try:
            with open(path, 'rb') as f:
                raw = f.read(4)
            if raw[:2] == b"\xff\xfe":
                return "utf-16-le"
            if raw[:2] == b"\xfe\xff":
                return "utf-16"
            if raw[:3] == b"\xef\xbb\xbf":
                return "utf-8-sig"
        except Exception:
            pass

        try:
            with open(path, 'rb') as f:
                raw = f.read(65536)
            if raw:
                try:
                    raw.decode("utf-8")
                    return "utf-8"
                except UnicodeDecodeError:
                    pass
                for enc in ["gbk", "gb2312", "big5", "shift_jis", "euc-kr"]:
                    try:
                        raw.decode(enc)
                        return enc
                    except UnicodeDecodeError:
                        continue
        except Exception:
            pass

        return "utf-8"

    def read(self, path: str, offset: int = 0, limit: int = 500) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"File not found: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        self._encoding_cache[abs_path] = encoding

        try:
            with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                all_lines = f.readlines()

            total_lines = len(all_lines)
            if offset < 0:
                offset = 0
            if offset >= total_lines:
                return {"error": f"Offset {offset} exceeds total lines {total_lines}"}

            end = offset + limit if limit else total_lines
            selected = all_lines[offset:end]
            content = "".join(selected).rstrip("\n")

            return {
                "success": True,
                "data": content,
                "path": abs_path,
                "lines": len(selected),
                "total_lines": total_lines,
                "offset": offset,
                "encoding": encoding,
            }
        except Exception as e:
            return {"error": f"Read failed: {e}"}

    def read_context(self, path: str, needle: str, context_lines: int = 3) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"File not found: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        try:
            with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()

            if needle not in content:
                return {"error": f"Needle not found in file", "needle_preview": needle[:50]}

            lines = content.split('\n')
            needle_lines = needle.split('\n')
            first_line = needle_lines[0] if needle_lines else needle

            start_idx = None
            for i, line in enumerate(lines):
                if first_line in line:
                    start_idx = i
                    break

            if start_idx is None:
                return {"error": f"Could not locate needle start line"}

            context_start = max(0, start_idx - context_lines)
            context_end = min(len(lines), start_idx + len(needle_lines) + context_lines)

            context_content = '\n'.join(lines[context_start:context_end])

            return {
                "success": True,
                "data": context_content,
                "path": abs_path,
                "line_offset": context_start,
                "needle_line": start_idx,
                "context_lines": context_end - context_start,
            }
        except Exception as e:
            return {"error": f"Read context failed: {e}"}

    def snapshot(self, path: str) -> str:
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return ""

        encoding = self._detect_encoding(abs_path)
        try:
            with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()

            snap_id = str(uuid.uuid4())[:8]
            snap = Snapshot(
                id=snap_id,
                path=abs_path,
                content=content,
                timestamp=os.path.getmtime(abs_path),
            )
            self._snapshots[snap_id] = snap

            return snap_id
        except Exception:
            return ""

    def rollback(self, snapshot_id: str, path: str = None, keep_snapshot: bool = False) -> Dict[str, Any]:
        if snapshot_id not in self._snapshots:
            return {"success": False, "error": f"Snapshot not found: {snapshot_id}"}

        snap = self._snapshots[snapshot_id]
        target_path = path or snap.path

        try:
            encoding = self._detect_encoding(target_path)
            self._atomic_write(target_path, snap.content, encoding)

            if not keep_snapshot:
                del self._snapshots[snapshot_id]

            return {
                "success": True,
                "message": f"Rolled back to snapshot {snapshot_id}",
                "path": target_path,
            }
        except Exception as e:
            return {"success": False, "error": f"Rollback failed: {e}"}

    def edit_range(self, path: str, start_byte: int, end_byte: int, new_text: str) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"File not found: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        try:
            with open(abs_path, 'rb') as f:
                content = f.read()

            new_content = content[:start_byte] + new_text.encode(encoding) + content[end_byte:]
            self._atomic_write(abs_path, new_content.decode(encoding), encoding)

            return {
                "success": True,
                "path": abs_path,
                "bytes_removed": end_byte - start_byte,
                "bytes_added": len(new_text.encode(encoding)),
            }
        except Exception as e:
            return {"error": f"Edit range failed: {e}"}

    def edit_string(self, path: str, old_string: str, new_string: str) -> Dict[str, Any]:
        abs_path = self._resolve_path(path)
        if not os.path.isfile(abs_path):
            return {"error": f"File not found: {abs_path}"}

        encoding = self._detect_encoding(abs_path)
        try:
            with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()

            if old_string not in content:
                normalized_old = self._normalize_quotes(old_string)
                normalized_content = self._normalize_quotes(content)
                if normalized_old in normalized_content:
                    idx = normalized_content.find(normalized_old)
                    old_string = content[idx:idx + len(old_string)]
                else:
                    return {"error": f"Old string not found", "preview": old_string[:50]}

            matches = content.count(old_string)
            if matches > 1:
                return {"error": f"Multiple matches ({matches}), use occurrence parameter", "matches": matches}

            new_content = content.replace(old_string, new_string, 1)
            self._atomic_write(abs_path, new_content, encoding)

            return {
                "success": True,
                "path": abs_path,
                "old_string_len": len(old_string),
                "new_string_len": len(new_string),
            }
        except Exception as e:
            return {"error": f"Edit string failed: {e}"}

    def write(self, path: str, content: str, mode: str = "overwrite") -> Dict[str, Any]:
        abs_path = self._resolve_path(path)

        try:
            parent = os.path.dirname(abs_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)

            if mode == "create" and os.path.exists(abs_path):
                return {"error": f"File already exists (mode=create): {abs_path}"}

            if mode == "append" and os.path.exists(abs_path):
                encoding = self._detect_encoding(abs_path)
                with open(abs_path, 'r', encoding=encoding, errors='replace') as f:
                    existing = f.read()
                content = existing + content

            self._atomic_write(abs_path, content, "utf-8")

            return {
                "success": True,
                "path": abs_path,
                "mode": mode,
                "bytes_written": len(content.encode('utf-8')),
            }
        except Exception as e:
            return {"error": f"Write failed: {e}"}

    def _normalize_quotes(self, text: str) -> str:
        replacements = {'"': '"', '"': '"', ''': "'", ''': "'"}
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _atomic_write(self, path: str, content: str, encoding: str = "utf-8"):
        dir_path = os.path.dirname(path) or "."
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(temp_fd, 'w', encoding=encoding) as f:
                f.write(content)
                f.flush()
                if hasattr(os, 'fsync'):
                    os.fsync(f.fileno())
            temp_fd = None
            if os.path.exists(path):
                stat_info = os.stat(path)
                os.replace(temp_path, path)
                os.chmod(path, stat_info.st_mode)
            else:
                os.replace(temp_path, path)
        except Exception:
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def get_snapshot(self, snapshot_id: str) -> Optional[Snapshot]:
        return self._snapshots.get(snapshot_id)

    def list_snapshots(self) -> Dict[str, Any]:
        return {
            "snapshots": [
                {"id": s.id, "path": s.path, "timestamp": s.timestamp}
                for s in self._snapshots.values()
            ],
            "total": len(self._snapshots),
        }

    def clear_snapshots(self) -> Dict[str, Any]:
        count = len(self._snapshots)
        self._snapshots.clear()
        return {"success": True, "cleared": count}
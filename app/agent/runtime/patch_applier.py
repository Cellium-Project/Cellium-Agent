# -*- coding: utf-8 -*-
import re
import difflib
from typing import Dict, Any, Tuple


class PatchApplier:

    HANDLERS = {
        "replace": "_apply_replace",
        "insert": "_apply_insert",
        "append": "_apply_append",
        "regex": "_apply_regex",
        "range": "_apply_range",
        "delete": "_apply_delete",
    }

    @classmethod
    def apply(cls, content: str, patch: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        mode = patch.get("mode", "replace")
        handler_name = cls.HANDLERS.get(mode)
        if not handler_name:
            return content, {"count": 0, "error": f"未知 patch mode: {mode}"}
        handler = getattr(cls, handler_name)
        return handler(content, patch)

    @classmethod
    def preview(cls, content: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        new_content, info = cls.apply(content, patch)
        diff = cls._generate_diff(content, new_content)
        return {
            "new_content": new_content,
            "diff": diff,
            "count": info.get("count", 0),
            "matches": info.get("matches", []),
            "error": info.get("error"),
        }

    @classmethod
    def _apply_replace(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        old_text = patch.get("old_text", "")
        new_text = patch.get("new_text", "")
        replace_all = patch.get("replace_all", False)

        if not old_text:
            return content, {"count": 0, "error": "old_text 为空"}

        content = content.replace('\r\n', '\n')
        old_text = old_text.replace('\r\n', '\n')
        new_text = new_text.replace('\r\n', '\n')

        if old_text not in content:
            normalized_old = cls._normalize_quotes(old_text)
            normalized_content = cls._normalize_quotes(content)
            if normalized_old in normalized_content:
                idx = normalized_content.index(normalized_old)
                actual_old = content[idx:idx + len(old_text)]
                if replace_all:
                    count = content.count(actual_old)
                    new_content = content.replace(actual_old, new_text)
                else:
                    count = 1
                    new_content = content.replace(actual_old, new_text, 1)
                return new_content, {"count": count}
            else:
                preview = old_text[:50] + "..." if len(old_text) > 50 else old_text
                first_line = old_text.split('\n')[0] if '\n' in old_text else old_text
                if first_line and first_line in content:
                    error_msg = f"未找到完整匹配，但找到首行: '{first_line[:30]}...'。请确保 old_text 与文件内容完全一致。"
                else:
                    error_msg = f"未找到匹配文本: {preview}"
                return content, {"count": 0, "error": error_msg}

        if replace_all:
            count = content.count(old_text)
            new_content = content.replace(old_text, new_text)
        else:
            count = 1
            new_content = content.replace(old_text, new_text, 1)

        return new_content, {"count": count}

    @staticmethod
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

    @classmethod
    def _apply_insert(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        line = patch.get("line", 1)
        insert_content = patch.get("content", "")

        lines = content.splitlines(keepends=True)

        if line <= 0:
            insert_idx = 0
        elif line > len(lines) + 1:
            insert_idx = len(lines)
        else:
            insert_idx = line - 1

        insert_lines = insert_content.splitlines(keepends=True)

        if not insert_lines:
            insert_lines = [insert_content] if insert_content else []

        if insert_lines and insert_content and not insert_lines[-1].endswith('\n'):
            if lines and insert_idx < len(lines):
                insert_lines[-1] += '\n'

        new_lines = lines[:insert_idx] + insert_lines + lines[insert_idx:]
        new_content = ''.join(new_lines)

        return new_content, {"count": len(insert_lines), "line": insert_idx + 1}

    @classmethod
    def _apply_append(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        append_content = patch.get("content", "")
        if content and not content.endswith('\n'):
            content = content + '\n'
        new_content = content + append_content
        return new_content, {"count": 1}

    @classmethod
    def _apply_regex(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        pattern = patch.get("pattern", "")
        replacement = patch.get("replacement", "")

        if not pattern:
            return content, {"count": 0, "error": "pattern 为空"}

        replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)

        try:
            new_content, count = re.subn(
                pattern, replacement, content, flags=re.MULTILINE
            )
        except re.error as e:
            return content, {"count": 0, "error": f"正则表达式错误: {e}"}

        matches = []
        try:
            for m in re.finditer(pattern, content, flags=re.MULTILINE):
                matches.append({
                    "start": m.start(),
                    "end": m.end(),
                    "group": m.group(0)[:50] if m.group(0) else "",
                })
        except re.error:
            pass

        return new_content, {"count": count, "matches": matches[:10]}

    @classmethod
    def _apply_range(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        start_line = patch.get("start_line", 0)
        end_line = patch.get("end_line", 0)
        new_text = patch.get("new_text", "")

        lines = content.splitlines(keepends=True)

        if start_line < 0:
            start_line = 0
        if end_line > len(lines):
            end_line = len(lines)
        if start_line > end_line:
            start_line = end_line

        new_lines = []
        if new_text:
            new_lines = new_text.splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith('\n'):
                new_lines[-1] += '\n'

        result_lines = lines[:start_line] + new_lines + lines[end_line:]
        new_content = ''.join(result_lines)

        return new_content, {
            "count": 1,
            "lines_removed": end_line - start_line,
            "lines_added": len(new_lines),
        }

    @classmethod
    def _apply_delete(cls, content: str, patch: Dict) -> Tuple[str, Dict]:
        start_line = patch.get("start_line", 0)
        end_line = patch.get("end_line", 0)

        lines = content.splitlines(keepends=True)

        if start_line < 0:
            start_line = 0
        if end_line > len(lines):
            end_line = len(lines)
        if start_line > end_line:
            start_line = end_line

        result_lines = lines[:start_line] + lines[end_line:]
        new_content = ''.join(result_lines)

        return new_content, {"count": 1, "lines_removed": end_line - start_line}

    @classmethod
    def _generate_diff(cls, old: str, new: str) -> str:
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        if old_lines and not old_lines[-1].endswith('\n'):
            old_lines[-1] += '\n'
        if new_lines and not new_lines[-1].endswith('\n'):
            new_lines[-1] += '\n'

        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile='before', tofile='after',
            lineterm=''
        ))
        return ''.join(diff_lines)

    @classmethod
    def validate_patch(cls, patch: Dict[str, Any]) -> Tuple[bool, str]:
        mode = patch.get("mode", "replace")

        if mode not in cls.HANDLERS:
            return False, f"未知 mode: {mode}"

        if mode == "replace":
            if not patch.get("old_text"):
                return False, "replace 模式需要 old_text"
        elif mode == "insert":
            if patch.get("line") is None:
                return False, "insert 模式需要 line"
            if patch.get("content") is None:
                return False, "insert 模式需要 content"
        elif mode == "append":
            if patch.get("content") is None:
                return False, "append 模式需要 content"
        elif mode == "regex":
            if not patch.get("pattern"):
                return False, "regex 模式需要 pattern"
            if patch.get("replacement") is None:
                return False, "regex 模式需要 replacement"
        elif mode in ("range", "delete"):
            if patch.get("start_line") is None:
                return False, f"{mode} 模式需要 start_line"
            if patch.get("end_line") is None:
                return False, f"{mode} 模式需要 end_line"

        return True, ""

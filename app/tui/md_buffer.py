import re

class MarkdownBuffer:

    def __init__(self):
        self._content = ""
        self._last_rendered = ""

    def append(self, text: str) -> None:
        self._content += text

    def is_complete(self) -> bool:
        content = self._content
        if content.count("```") % 2 != 0:
            return False
        if self._has_unclosed_table(content):
            return False
        if self._has_unclosed_blockquote(content):
            return False
        return True

    def _has_unclosed_table(self, content: str) -> bool:
        lines = content.split("\n")
        if not lines:
            return False
        last_line = lines[-1].strip()
        if last_line.startswith("|") and last_line.endswith("|"):
            return True
        return False

    def _has_unclosed_blockquote(self, content: str) -> bool:
        lines = content.split("\n")
        if not lines:
            return False
        last_line = lines[-1].strip()
        if last_line.startswith(">"):
            return True
        return False

    def get_content(self) -> str:
        return self._content

    def mark_rendered(self) -> None:
        self._last_rendered = self._content

    def has_changed(self) -> bool:
        return self._content != self._last_rendered

    def reset(self) -> None:
        self._content = ""
        self._last_rendered = ""

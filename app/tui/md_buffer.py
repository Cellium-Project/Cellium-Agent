class MarkdownBuffer:

    def __init__(self):
        self._content = ""
        self._last_rendered = ""
        self._version = 0

    def append(self, text: str) -> None:
        self._content += text
        self._version += 1

    def get_content(self) -> str:
        return self._content

    def mark_rendered(self) -> None:
        self._last_rendered = self._content

    def has_changed(self) -> bool:
        return self._content != self._last_rendered

    def reset(self) -> None:
        self._content = ""
        self._last_rendered = ""

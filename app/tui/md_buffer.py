class MarkdownBuffer:

    def __init__(self, max_defer: float = 1.2):
        self._content = ""
        self._last_rendered = ""
        self._wait_started = None
        self.max_defer = max_defer

    def append(self, text: str) -> None:
        self._content += text

    def is_complete(self) -> bool:
        content = self._content
        if content.count("```") % 2 != 0:
            return False
        return True

    def should_force_render(self) -> bool:
        if self.is_complete():
            return False
        if self._wait_started is None:
            return False
        import time
        return (time.time() - self._wait_started) >= self.max_defer

    def get_content(self) -> str:
        return self._content

    def mark_rendered(self) -> None:
        self._last_rendered = self._content
        self._wait_started = None

    def note_waiting(self) -> None:
        if self._wait_started is None:
            import time
            self._wait_started = time.time()

    def has_changed(self) -> bool:
        return self._content != self._last_rendered

    def reset(self) -> None:
        self._content = ""
        self._last_rendered = ""
        self._wait_started = None

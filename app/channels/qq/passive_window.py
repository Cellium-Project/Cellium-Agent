import time
from collections import deque

PASSIVE_WINDOW_SECONDS = 270
PASSIVE_MAX_REPLIES = 5
PENDING_TTL_SECONDS = 1800
PENDING_MAX_ITEMS = 20
RESERVE_SLOTS = 1
QUEUE_MODE_SECONDS = 30
EXPIRED_CODES = frozenset({40034005, 40034031, 40034128})
MARKDOWN_FAIL_CODES = frozenset({40034008, 50037, 50041, 50055, 50056})


class PassiveWindowTracker:

    def __init__(self):
        self._windows = {}
        self._usage = {}
        self._used_at = {}
        self._exhausted = set()
        self._pending = {}
        self._queue_mode_until = {}

    def note_inbound(self, group_id: str, msg_id: str):
        if not group_id or not msg_id:
            return
        if msg_id.startswith(("scheduler_", "component_")):
            return
        self._windows[group_id] = {"msg_id": msg_id, "received_at": time.time()}
        self._queue_mode_until.pop(group_id, None)
        self._drop_expired_pending(group_id, time.time())

    def resolve(self, group_id: str, preferred: str = "") -> str:
        now = time.time()
        if preferred:
            win = self._window_by_msg_id(preferred)
            if win and self._window_valid(win, now):
                return preferred
        win = self._windows.get(group_id)
        if win and self._window_valid(win, now):
            return win["msg_id"]
        return ""

    def remaining(self, group_id: str, msg_id: str = None) -> int:
        target = msg_id or self.resolve(group_id)
        if not target:
            return 0
        win = self._window_by_msg_id(target)
        if not win:
            return 0
        if not self._window_valid(win, time.time()):
            return 0
        return max(0, PASSIVE_MAX_REPLIES - self._usage.get(target, 0))

    def mark_used(self, msg_id: str):
        if not msg_id:
            return
        now = time.time()
        self._usage[msg_id] = self._usage.get(msg_id, 0) + 1
        self._used_at[msg_id] = now

    def mark_exhausted(self, msg_id: str):
        if not msg_id:
            return
        self._exhausted.add(msg_id)

    def enqueue(self, group_id: str, content: str):
        if not group_id or not (content or "").strip():
            return
        items = self._pending.setdefault(group_id, deque())
        items.append({"content": content, "created_at": time.time()})
        while len(items) > PENDING_MAX_ITEMS:
            items.popleft()
        self.set_queue_mode(group_id)

    def set_queue_mode(self, group_id: str):
        if not group_id:
            return
        self._queue_mode_until[group_id] = time.time() + QUEUE_MODE_SECONDS

    def queue_mode_active(self, group_id: str) -> bool:
        until = self._queue_mode_until.get(group_id)
        if until is None:
            return False
        if time.time() < until:
            return True
        self._queue_mode_until.pop(group_id, None)
        return False

    def has_pending(self, group_id: str) -> bool:
        return bool(self._pending.get(group_id))

    def peek_pending(self, group_id: str) -> list:
        return [item["content"] for item in self._pending.get(group_id, ())]

    def replace_pending(self, group_id: str, contents: list):
        items = [{"content": c, "created_at": time.time()} for c in contents if c]
        if not items:
            self._pending.pop(group_id, None)
            return
        while len(items) > PENDING_MAX_ITEMS:
            items.pop(0)
        self._pending[group_id] = deque(items)

    def clear_pending(self, group_id: str):
        self._pending.pop(group_id, None)

    def purge(self, now=None):
        now = time.time() if now is None else now

        for group_id, win in list(self._windows.items()):
            if now - win["received_at"] > 600:
                self._windows.pop(group_id, None)
                msg_id = win["msg_id"]
                self._usage.pop(msg_id, None)
                self._used_at.pop(msg_id, None)
                self._exhausted.discard(msg_id)

        for group_id, items in list(self._pending.items()):
            kept = deque(
                item for item in items
                if now - item["created_at"] <= PENDING_TTL_SECONDS
            )
            if kept:
                self._pending[group_id] = kept
            else:
                self._pending.pop(group_id, None)

        for group_id, until in list(self._queue_mode_until.items()):
            if now >= until:
                self._queue_mode_until.pop(group_id, None)

        window_ids = {win["msg_id"] for win in self._windows.values()}
        for msg_id in list(self._usage.keys()):
            if msg_id in window_ids:
                continue
            if now - self._used_at.get(msg_id, 0) <= 600:
                continue
            self._usage.pop(msg_id, None)
            self._used_at.pop(msg_id, None)
            self._exhausted.discard(msg_id)

        for msg_id in list(self._exhausted):
            if msg_id in window_ids:
                continue
            if now - self._used_at.get(msg_id, 0) > 600:
                self._exhausted.discard(msg_id)

    def _window_by_msg_id(self, msg_id: str):
        if not msg_id:
            return None
        for win in self._windows.values():
            if win["msg_id"] == msg_id:
                return win
        return None

    def _window_valid(self, win: dict, now: float) -> bool:
        msg_id = win["msg_id"]
        if now - win["received_at"] > PASSIVE_WINDOW_SECONDS:
            return False
        if msg_id in self._exhausted:
            return False
        return self._usage.get(msg_id, 0) < PASSIVE_MAX_REPLIES

    def _drop_expired_pending(self, group_id: str, now: float):
        items = self._pending.get(group_id)
        if not items:
            return
        kept = deque(
            item for item in items
            if now - item["created_at"] <= PENDING_TTL_SECONDS
        )
        if kept:
            self._pending[group_id] = kept
        else:
            self._pending.pop(group_id, None)

# -*- coding: utf-8 -*-
"""会话选择与重命名"""
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets._option_list import Option


def format_rewind_files(
    files,
    skipped=None,
    limit: int = 8,
    restore_label: str = "还原",
    delete_label: str = "删除",
    none_label: str = "无文件改动",
    more_label: str = "还有 {} 个文件",
    skipped_label: str = "{} 个文件无法回滚",
    binary_label: str = "二进制",
) -> str:
    files = list(files or [])
    skipped = list(skipped or [])
    if not files:
        lines = [f"[dim]{none_label}[/]"]
    else:
        lines = []
        for f in files[:limit]:
            path = str(f.get("path") or "").replace("\\", "/")
            short = "/".join(path.split("/")[-2:])
            if len(short) > 34:
                short = short[:33] + "…"
            kind = f.get("kind")
            if kind == "restored":
                stat = f"[#b9a6ff]{restore_label}[/]"
            elif kind == "deleted":
                stat = f"[#f87171]{delete_label}[/]"
            elif f.get("binary"):
                stat = f"[dim]{binary_label}[/]"
            else:
                stat = (
                    f"[#34d399]+{int(f.get('added') or 0)}[/] "
                    f"[#f87171]-{int(f.get('removed') or 0)}[/]"
                )
            lines.append(f"{short}  {stat}")
        if len(files) > limit:
            lines.append(f"[dim]{more_label.format(len(files) - limit)}[/]")
    if skipped:
        lines.append(f"[#f87171]{skipped_label.format(len(skipped))}[/]")
    return "\n".join(lines)


class SessionRenameScreen(ModalScreen):
    """重命名会话"""

    CSS = """
    SessionRenameScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #session-rename-panel {
        width: 44;
        max-width: 80%;
        height: auto;
        background: $surface;
    }

    #session-rename-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }

    #session-rename-body {
        height: auto;
        padding: 1 2;
    }

    #session-rename-input {
        margin: 0 0 1 0;
    }

    #session-rename-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def __init__(self, session_id: str, initial: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session_id = session_id
        self._initial = initial or ""

    def compose(self):
        with Vertical(id="session-rename-panel"):
            yield Static(self.app.tr("session.rename_title"), id="session-rename-title")
            with Vertical(id="session-rename-body"):
                yield Input(value=self._initial, placeholder=self.app.tr("session.rename_placeholder"), id="session-rename-input")
            yield Static(self.app.tr("session.rename_footer"), id="session-rename-footer")

    def on_mount(self):
        inp = self.query_one("#session-rename-input", Input)
        inp.focus()

    def _save(self):
        inp = self.query_one("#session-rename-input", Input)
        title = "".join(" " if ord(ch) < 32 else ch for ch in (inp.value or ""))
        title = " ".join(title.split()).strip()[:60]
        if not title:
            self.app.pop_screen()
            return
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            if store.session_exists(self._session_id):
                store.set_session_title(self._session_id, title)
        except Exception:
            pass
        try:
            self.app.pop_screen()
            current = self.app.screen
            if isinstance(current, SessionPickerScreen):
                current._reload_sessions()
        except Exception:
            pass
        try:
            self.app._post(self.app._load_sessions())
        except Exception:
            pass

    def on_input_submitted(self, event):
        if event.input.id == "session-rename-input":
            self._save()

    def key_escape(self):
        self.app.pop_screen()


class DeleteConfirmScreen(ModalScreen):
    """删除会话确认"""

    BINDINGS = [
        ("y", "confirm", "确认删除"),
        ("n", "cancel", "取消"),
        ("escape", "cancel", "取消"),
    ]

    CSS = """
    DeleteConfirmScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #delete-confirm-panel {
        width: 52;
        max-width: 85%;
        height: auto;
        background: $surface;
    }

    #delete-confirm-body {
        height: auto;
        padding: 2;
    }

    #delete-confirm-actions {
        height: 3;
        padding: 0 2;
        align-horizontal: right;
    }
    """

    def __init__(self, message: str, confirm_label: str = "", cancel_label: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self):
        with Vertical(id="delete-confirm-panel"):
            yield Static(self._message, id="delete-confirm-body")
            with Horizontal(id="delete-confirm-actions"):
                yield Button(self._cancel_label, id="cancel")
                yield Button(self._confirm_label, id="confirm", variant="error")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)


class RewindConfirmScreen(ModalScreen):
    """撤回确认"""

    BINDINGS = [
        ("y", "confirm", "确认撤回"),
        ("n", "cancel", "取消"),
        ("escape", "cancel", "取消"),
    ]

    CSS = """
    RewindConfirmScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #rewind-confirm-panel {
        width: 60;
        max-width: 90%;
        height: auto;
        background: $surface;
    }

    #rewind-confirm-body {
        height: auto;
        padding: 1 2 0 2;
    }

    #rewind-confirm-files {
        height: auto;
        max-height: 12;
        padding: 1 2 0 2;
    }

    #rewind-confirm-hint {
        height: auto;
        padding: 1 2 0 2;
        color: $text-muted;
    }

    #rewind-confirm-actions {
        height: 3;
        padding: 0 2;
        align-horizontal: right;
    }

    #rewind-confirm-actions Button:focus {
        color: #ffffff;
        text-style: bold;
        background: $accent;
    }

    #rewind-confirm-actions Button#cancel:focus {
        background: $primary;
        color: #ffffff;
    }

    #rewind-confirm-actions Button#confirm:focus {
        background: $error;
        color: #ffffff;
    }

    #rewind-confirm-actions Button:hover {
        color: #ffffff;
    }
    """

    def __init__(
        self,
        message: str,
        files=None,
        skipped=None,
        hint: str = "",
        files_text: str = "",
        confirm_label: str = "",
        cancel_label: str = "",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._message = message
        self._files = files
        self._skipped = skipped
        self._hint = hint
        self._files_text = files_text
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self):
        with Vertical(id="rewind-confirm-panel"):
            yield Static(self._message, id="rewind-confirm-body")
            yield Static(self._files_text, id="rewind-confirm-files")
            yield Static(self._hint, id="rewind-confirm-hint")
            with Horizontal(id="rewind-confirm-actions"):
                yield Button(self._cancel_label, id="cancel")
                yield Button(self._confirm_label, id="confirm", variant="error")

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm")

    def action_confirm(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)


class SessionPickerScreen(ModalScreen):

    CSS = """
    SessionPickerScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #session-picker-panel {
        width: 60;
        max-width: 80%;
        height: auto;
        background: $surface;
    }

    #session-picker-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }

    #session-picker-list {
        height: auto;
        max-height: 18;
        padding: 1 0;
        border: none;
        background: $surface;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    #session-picker-list:focus {
        border: none;
        background-tint: transparent;
    }

    #session-picker-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def __init__(self, sessions, current, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # sessions: [(session_id, title), ...]
        self._sessions = sessions
        self._current = current

    def compose(self):
        with Vertical(id="session-picker-panel"):
            yield Static(self.app.tr("session.picker_title"), id="session-picker-title")
            yield OptionList(id="session-picker-list")
            yield Static(self.app.tr("session.picker_footer"), id="session-picker-footer")

    def on_mount(self):
        lst = self.query_one("#session-picker-list", OptionList)
        options = []
        for i, (sid, title) in enumerate(self._sessions):
            label = self._prompt(sid, title, 0)
            options.append(Option(label, id=sid))
            if sid == self._current:
                lst.highlighted = i
        lst.add_options(options)
        lst.focus()
        self._frame = 0
        self.set_interval(0.5, self._tick_running)

    def _prompt(self, sid: str, title: str, frame: int) -> str:
        from app.core.util.session_status import is_session_running
        from app.tui.spinner import running_marker
        from app.tui.widgets import clip_cells, plain_one_line
        title = clip_cells(plain_one_line(title), 48).replace("[", "\\[")
        if is_session_running(sid):
            return f"{running_marker(frame)} {title}"
        mark = "●" if sid == self._current else "○"
        return f"{mark} {title}"

    def _tick_running(self):
        self._frame += 1
        try:
            lst = self.query_one("#session-picker-list", OptionList)
        except Exception:
            return
        for idx, (sid, title) in enumerate(self._sessions):
            try:
                lst.replace_option_prompt_at_index(idx, self._prompt(sid, title, self._frame))
            except Exception:
                pass

    def _selected_session(self):
        lst = self.query_one("#session-picker-list", OptionList)
        opt = lst.highlighted_option
        if opt is None:
            return None
        return opt.id

    def _reload_sessions(self):
        """从 store 重新读取会话并重建列表"""
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            metas = store.list_sessions(limit=50)
            sessions = []
            for meta in metas:
                title = meta.title or meta.session_id
                if meta.message_count:
                    title += f" ({meta.message_count})"
                sessions.append((meta.session_id, title))
            self._sessions = sessions
            lst = self.query_one("#session-picker-list", OptionList)
            lst.clear_options()
            options = []
            for i, (sid, title) in enumerate(sessions):
                options.append(Option(self._prompt(sid, title, getattr(self, "_frame", 0)), id=sid))
                if sid == self._current:
                    lst.highlighted = i
            lst.add_options(options)
        except Exception:
            pass

    def on_option_list_option_selected(self, event):
        sid = event.option.id
        if sid == self._current:
            self.app.pop_screen()
            return
        self.dismiss(sid)

    def key_escape(self):
        self.app.pop_screen()

    def key_e(self):
        """重命名选中的会话"""
        sid = self._selected_session()
        if not sid:
            return
        title = ""
        for s, t in self._sessions:
            if s == sid:
                title = t
                break
        if " (" in title:
            title = title.split(" (")[0]
        self.app.push_screen(SessionRenameScreen(sid, initial=title))

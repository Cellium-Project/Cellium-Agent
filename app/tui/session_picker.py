# -*- coding: utf-8 -*-
"""会话选择与重命名"""
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets._option_list import Option


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
        title = (inp.value or "").strip()
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
            mark = "●" if sid == self._current else "○"
            label = f"{mark} {title}"
            options.append(Option(label, id=sid))
            if sid == self._current:
                lst.highlighted = i
        lst.add_options(options)
        lst.focus()

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
                mark = "●" if sid == self._current else "○"
                options.append(Option(f"{mark} {title}", id=sid))
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

    def key_delete(self):
        """删除选中的会话（保护 default/tui）"""
        sid = self._selected_session()
        if not sid:
            return
        if sid in ("default", "tui"):
            self.app.notify(self.app.tr("session.protected"), severity="warning")
            return
        self.dismiss(("__delete__", sid))

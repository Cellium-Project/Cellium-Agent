# -*- coding: utf-8 -*-
import os

os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

import asyncio
import json
import re
import threading
import time

from textual.app import App, ComposeResult
from textual.actions import SkipAction
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Static, Button, OptionList
from textual.widgets._option_list import Option
from rich.text import Text

from app.tui.theme import register_cellium_themes, LIGHT_THEME, DARK_THEME
from app.tui.commands import match_commands
from app.tui.widgets import (
    CommandInput,
    CommandPalette,
    UserMessage,
    AssistantMessage,
    ThinkingBlock,
    ToolCallCard,
    LogoImage,
    ChatScroll,
)

CSS = """
Screen {
    layout: vertical;
    background: $background;
    color: $foreground;
}

#body {
    height: 1fr;
}

#sidebar {
    width: 26;
    min-width: 20;
    max-width: 40;
    padding: 0 0 0 1;
    background: transparent;
}

.logo {
    width: 100%;
    height: auto;
    margin: 1 0 0 0;
    content-align: center middle;
}

#sidebar-title {
    height: 3;
    padding: 0 2;
    color: $text-muted;
    content-align: left bottom;
}

#session-list {
    height: 1fr;
    scrollbar-gutter: stable;
    padding: 0 1;
    border: none;
    background: transparent;
}

#session-list:focus {
    border: none;
    background-tint: transparent;
}

#chat.no-scrollbar {
    scrollbar-gutter: auto;
    scrollbar-color: transparent transparent;
    scrollbar-size-vertical: 0;
}

#sidebar-bottom {
    height: auto;
    padding: 0 1;
}

#btn-settings {
    width: 1fr;
    height: 3;
    content-align: left middle;
    padding: 0 2;
    color: $text-muted;
    border: none;
    background: transparent;
}

#btn-settings:hover {
    background: transparent;
    color: $primary;
}

#btn-settings:focus {
    text-style: bold;
    background: transparent;
    color: $primary;
}

#btn-new-session {
    height: 3;
    padding: 0 2;
    content-align: left middle;
    color: $primary;
    border: none;
    background: transparent;
    text-wrap: nowrap;
    overflow: hidden;
}

#btn-new-session:hover {
    background: transparent;
    text-style: bold;
}

#btn-new-session:focus {
    text-style: bold;
    background: transparent;
}

.session-item {
    padding: 0 2;
}

.session-item:hover {
    background: $bg-hover;
}

.session-active {
    background: $bg-active;
    color: $primary;
}

#chat {
    height: 1fr;
    padding: 1 0 1 1;
    scrollbar-gutter: stable;
}

#chat-column {
    width: 1fr;
    height: 1fr;
}

#footer-bar {
    height: auto;
    padding: 0 2 1 2;
    background: transparent;
}

#command-palette {
    display: none;
    height: auto;
    max-height: 15;
    background: transparent;
}

#palette-title {
    height: 3;
    padding: 0 2;
    color: $primary;
    text-style: bold;
    content-align: left middle;
    background: transparent;
}

#palette-list {
    height: auto;
    max-height: 10;
    padding: 1 0;
    border: none;
    background: transparent;
}

#palette-list:focus {
    border: none;
    background-tint: transparent;
}

#palette-footer {
    height: 2;
    padding: 0 2;
    color: $text-muted;
    content-align: left top;
    background: $bg-hover;
}

#input {
    height: auto;
    max-height: 6;
    min-height: 1;
    padding: 0 2;
    background: $input-bg;
    color: $foreground;
    border: none;
    border-top: solid $input-bg;
    border-bottom: solid $input-bg;
    scrollbar-size-vertical: 0;
    scrollbar-size-horizontal: 0;
}

#input > .input {
    border: none;
}

#input:focus {
    border: none;
    border-top: solid $input-bg;
    border-bottom: solid $input-bg;
    background-tint: transparent;
}

#hint {
    height: 2;
    padding: 0 2;
    color: $text-muted;
    content-align: left top;
    background: $input-bg;
}

#status-bar {
    height: 1;
    background: transparent;
    color: $text-muted;
    padding: 0 2;
}

#status-left {
    width: 1fr;
    height: 1;
    color: $text-muted;
}

#status-right {
    width: auto;
    height: 1;
    color: $text-muted;
    content-align: right middle;
}

.user-msg {
    margin: 0 0 1 0;
    padding: 1 2;
    background: $msg-user-bg;
    color: $msg-user-text;
}

.assistant-msg {
    margin: 0 0 1 0;
    padding: 0 1;
}

.thinking {
    color: $text-muted;
    margin: 0 0 1 2;
}

.system-msg {
    color: $text-muted;
    margin: 1 0;
    text-align: center;
}

.tool-card {
    margin: 1 0;
    padding: 0;
    background: transparent;
}

Screen > .screen--selection {
    color: $background;
    background: $foreground 100%;
}
"""

_THEME_PREF_FILE = None


def _theme_pref_path_impl() -> str:
    """惰性计算主题偏好文件路径（写 CWD，pip 场景安全）"""
    global _THEME_PREF_FILE
    if _THEME_PREF_FILE is None:
        from app.core.util.runtime_paths import resolve_dir_writable
        _THEME_PREF_FILE = os.path.join(resolve_dir_writable("data"), "tui_theme.json")
    return _THEME_PREF_FILE


class CelliumTUI(App):
    TITLE = "Cellium Agent"
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+q", "stop_or_exit", "退出", priority=True),
        Binding("ctrl+c", "copy_text", "复制", show=False),
        Binding("ctrl+y", "copy_selection", "复制选区", show=False),
        Binding("ctrl+t", "toggle_theme", "切换主题"),
        Binding("ctrl+n", "new_session", "新建会话"),
        Binding("ctrl+d", "delete_session", "删除会话"),
        Binding("ctrl+s", "open_settings", "设置"),
        Binding("ctrl+l", "clear_chat", "清屏"),
    ]

    def __init__(self, bootstrap=None, session_id=None):
        super().__init__()
        register_cellium_themes(self)
        self.theme = self.load_theme_pref()
        self.lang = self.load_lang_pref()
        self.bootstrap = bootstrap

        import threading as _threading
        self._agent_loop = asyncio.new_event_loop()
        self._agent_loop_thread = _threading.Thread(
            target=self._agent_loop.run_forever,
            daemon=True,
            name="tui-agent-loop",
        )
        self._agent_loop_thread.start()
        self.session_id = session_id or self._default_session_id()
        self.model_name = self._current_model_name()
        self._busy = False
        self._current_loop = None
        self._busy_frame = 0
        self._busy_status = ""
        self._busy_animation_running = False
        self._busy_started = 0.0
        self._session_epoch = 0
        self._last_session_ids = []
        self._history_limit = self._load_history_limit()
        self._history_loading = None
        self._current_thinking = None
        self._current_response = None
        self._current_md = ""
        self._thought_mode = False    # 是否处于 JSON 思考协议解析中
        self._thought_raw = ""        # 思考协议原始 JSON 累积
        self._thought_fenced = False  # 思考协议是否被 ```json 代码块包裹
        self._pending_text = ""       # 待定文本
        self._md_render_timer = None  # Markdown 流式渲染节流定时器
        self._tool_cards = {}
        self._active_tool_card = None
        self._tool_count = 0
        self._last_esc = 0.0
        self._show_thinking = True
        self._show_sidebar = True
        self._palette_active = False
        self._restoring_history = False 

        self.header = None
        self.body = None
        self.sidebar = None
        self.session_list = None
        self.chat = None
        self.input = None
        self.hint = None
        self.status_left = None
        self.status_right = None
        self._history_ready = asyncio.Event()
        self._bootstrap_ready = asyncio.Event()
        self._bootstrap_frame = 0
        self._bootstrap_shown = False
        self._history_offset = 0
        self._history_has_more = False
        self._history_loading_more = False

    def tr(self, key, *args):
        from app.tui.i18n import t
        return t(self.lang, key, *args)

    # ---- 初始化 ----

    def _default_session_id(self):
        try:
            from app.core.util.agent_config import get_config
            return get_config().get("agent.default_session_id", "default")
        except Exception:
            return "default"

    def _current_model_name(self):
        try:
            from app.core.util.agent_config import get_config
            return get_config().get("llm.current_model", "")
        except Exception:
            return ""

    def _load_history_limit(self):
        try:
            from app.core.util.agent_config import get_config
            value = get_config().get("tui.history_limit", 100)
            return max(10, min(500, int(value))) if value else 100
        except Exception:
            return 100

    def _theme_pref_path(self):
        path = os.path.dirname(_theme_pref_path_impl())
        os.makedirs(path, exist_ok=True)
        return _theme_pref_path_impl()

    def _read_prefs(self):
        try:
            with open(self._theme_pref_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _write_prefs(self, **kwargs):
        try:
            data = self._read_prefs()
            data.update(kwargs)
            with open(self._theme_pref_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def load_theme_pref(self):
        return self._read_prefs().get("theme", "cellium-dark")

    def save_theme_pref(self, theme):
        self._write_prefs(theme=theme)

    def load_lang_pref(self):
        return self._read_prefs().get("lang", "zh-CN")

    def save_lang_pref(self, lang):
        self._write_prefs(lang=lang)

    def _post(self, coro):
        asyncio.get_running_loop().create_task(coro)

    # ---- 界面构建 ----

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield LogoImage()
                yield Static(self.tr("sidebar.title"), id="sidebar-title")
                yield Button(self.tr("sidebar.new"), id="btn-new-session", variant="default")
                yield OptionList(id="session-list")
                with Vertical(id="sidebar-bottom"):
                    yield Button(self.tr("sidebar.settings"), id="btn-settings", variant="default")
            with Vertical(id="chat-column"):
                yield ChatScroll(id="chat")
                with Vertical(id="footer-bar"):
                    yield CommandPalette(id="command-palette")
                    yield CommandInput(id="input", placeholder="")
                    yield Static(self.tr("hint"), id="hint")
        with Horizontal(id="status-bar"):
            yield Static("", id="status-left")
            yield Static("", id="status-right")

    def on_mount(self):
        self.body = self.query_one("#body", Horizontal)
        self.sidebar = self.query_one("#sidebar", Vertical)
        self.session_list = self.query_one("#session-list", OptionList)
        self.chat = self.query_one("#chat", VerticalScroll)
        self.input = self.query_one("#input", CommandInput)
        self.hint = self.query_one("#hint", Static)
        self.status_left = self.query_one("#status-left", Static)
        self.status_right = self.query_one("#status-right", Static)
        self.input.focus()
        self._set_terminal_title()
        self._refresh_status()
        self._post(self._load_sessions())
        self._post(self._welcome())
        self._last_width = self.size.width
        self._apply_responsive_sidebar()
        self.set_interval(0.2, self._check_width_change)
        self._bootstrap_timer = self.set_interval(0.2, self._check_bootstrap)
        self._register_session_listener()
        self._apply_rich_md_theme()
        # 常驻订阅 broker：空闲时也能实时收到对端（WebUI/外部平台）发起的消息
        self._post(self._start_broker_listener())

    def _set_terminal_title(self):
        title = str(self.title or self.TITLE).replace("\x1b", "").replace("\x07", "")
        try:
            out = self.console.file
            if out is not None:
                out.write(f"\x1b]0;{title}\x07")
                out.flush()
        except Exception:
            pass
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.kernel32.SetConsoleTitleW(title)
            except Exception:
                pass

    def _apply_rich_md_theme(self):
        try:
            if getattr(self, "_rich_md_theme_applied", False):
                self.console.pop_theme()
            from rich.theme import Theme
            from rich.style import Style
            v = self.get_css_variables()
            primary = v.get("markdown-h2-color") or v.get("primary") or "#58a6ff"
            muted = v.get("text-muted") or "#8b949e"
            code_bg = v.get("code-bg") or "#0d1117"
            fg = v.get("foreground") or "#e6edf3"
            table_border = v.get("border-primary") or "#30363d"
            inline_bg = v.get("code-bg") or ("#0d1117" if self.theme.endswith("dark") else "#f6f8fa")
            inline_fg = v.get("accent-primary") or v.get("primary") or "#1a73e8"
            theme = Theme({
                "markdown.h1": Style(color=primary, bold=True),
                "markdown.h2": Style(color=primary, bold=True),
                "markdown.h3": Style(color=primary, bold=True),
                "markdown.h4": Style(color=primary, bold=True),
                "markdown.h5": Style(color=primary, bold=True),
                "markdown.h6": Style(color=primary, bold=True),
                "markdown.code_block": Style(bgcolor=code_bg, color=fg),
                "markdown.code": Style(bgcolor=inline_bg, color=inline_fg),
                "markdown.block_quote": Style(color=muted),
                "markdown.hr": Style(color=muted),
                "markdown.item.bullet": Style(color=primary),
                "markdown.item.number": Style(color=primary),
                "markdown.table.header": Style(color=primary, bold=True),
                "markdown.table.border": Style(color=table_border),
            })
            self.console.push_theme(theme)
            self._rich_md_theme_applied = True
        except Exception:
            self._rich_md_theme_applied = False

    def _check_bootstrap(self):
        if self._bootstrap_ready.is_set():
            if self._bootstrap_shown:
                self._bootstrap_shown = False
                self._refresh_status()
            if getattr(self, "_bootstrap_timer", None) is not None:
                try:
                    self._bootstrap_timer.stop()
                except Exception:
                    pass
                self._bootstrap_timer = None
            return
        self._bootstrap_shown = True
        self._bootstrap_frame += 1
        self._refresh_status()

    def _check_width_change(self) -> None:
        w = self.size.width
        if w != self._last_width:
            self._last_width = w
            self._refresh_status()
            self._apply_responsive_sidebar()

    def on_unmount(self) -> None:
        """退出时关闭常驻后台事件循环"""
        loop = getattr(self, "_agent_loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
                import threading as _threading
                th = getattr(self, "_agent_loop_thread", None)
                if th is not None and th.is_alive():
                    th.join(timeout=2)
                loop.close()
            except Exception:
                pass

    def on_app_focus(self, event):
        if self.input is not None:
            try:
                self.refresh()
            except Exception:
                pass
            try:
                if self.focused is None or self.focused is not self.input:
                    self.input.focus()
            except Exception:
                pass

    def on_app_blur(self, event):
        pass

    async def _welcome(self):
        await self._load_history()
        await self._check_model_setup()

    async def _check_model_setup(self):
        try:
            if self._model_configured_valid():
                if self.input:
                    self.input.set_placeholder_aware(self.tr("input.placeholder"))
                return
            if self.input:
                self.input.set_placeholder_aware(self.tr("input.no_model"))
            await self._append_system(self.tr("model.setup_hint"), markup=True)
            self._refresh_status()
        except Exception:
            pass

    def _model_configured_valid(self) -> bool:
        try:
            from app.core.util.agent_config import get_config
            llm = get_config().get_section("llm") or {}
            models = llm.get("models", [])
            current = llm.get("current_model", "")
            if not models or not current:
                return False
            target = None
            for m in models:
                if m.get("name") == current:
                    target = m
                    break
            if target is None:
                target = models[0]
            return bool((target.get("api_key") or "").strip())
        except Exception:
            return False

    def _start_busy_animation(self):
        if self._busy_animation_running:
            return
        self._busy_animation_running = True
        self._busy_frame = 0
        self._busy_started = time.monotonic()
        try:
            self._busy_timer = self.set_interval(1 / 12, self._tick_busy_animation)
        except Exception:
            self._busy_timer = None
        self._refresh_status()

    def _stop_busy_animation(self):
        if not self._busy_animation_running:
            return
        self._busy_animation_running = False
        try:
            if getattr(self, "_busy_timer", None) is not None:
                self._busy_timer.stop()
        except Exception:
            pass
        self._busy_timer = None
        self._refresh_status()

    def _tick_busy_animation(self):
        self._busy_frame += 1
        self._refresh_status()

    def _refresh_status(self):
        web = ""
        if self.bootstrap and self.bootstrap.fastapi_app is not None:
            web = f"http://{self.bootstrap.host}:{self.bootstrap.port}"

        cache = self._prompt_cache_status()

        if self._bootstrap_shown and not self._bootstrap_ready.is_set():
            from app.tui.spinner import cell_spinner_text
            color = DARK_THEME.primary if self.theme == "cellium-dark" else LIGHT_THEME.primary
            anim = cell_spinner_text(self._bootstrap_frame, self.tr("initializing"), color)
            if self.status_left:
                self.status_left.update(anim)
            self._refresh_status_right(web)
            return

        left = f"[dim]{self.tr('header.session')} {self.session_id} · "
        left += f"{self.tr('header.model')} {self.model_name}"
        if self._busy:
            from app.tui.spinner import cell_spinner_text
            state = self._busy_status or self.tr("busy")
            theme_color = DARK_THEME.primary if self.theme == "cellium-dark" else LIGHT_THEME.primary
            anim = cell_spinner_text(self._busy_frame, state, theme_color)
            if self.status_left:
                self.status_left.update(
                    Text.assemble(
                        anim,
                        Text.from_markup(
                            f"  [dim]{self.tr('header.session')} {self.session_id} · "
                            f"{self.tr('header.model')} {self.model_name}"
                        ),
                    )
                )
                self._refresh_status_right(web)
                return
        if cache:
            left += f" · {self.tr('header.cache')} {cache}"
        if self.status_left:
            self.status_left.update(left + "[/dim]")
        self._refresh_status_right(web)

    def _refresh_status_right(self, web):
        right = ""
        if web:
            right = f"[dim]| {self.tr('header.webui')} {web}[/dim]"
        if self.status_right:
            self.status_right.update(right)

    def _prompt_cache_status(self) -> str:
        try:
            from app.agent.loop import AgentLoopManager
            mgr = AgentLoopManager.get_instance()
            loop = mgr.get_loop_sync(self.session_id)
            if loop is None:
                return ""
            summary = loop.prompt_diff_summary()
            if not summary:
                return ""
            import re
            m = re.search(r"平均缓存覆盖率=([0-9.]+%)", summary)
            if not m:
                m = re.search(r"缓存覆盖率=([0-9.]+%)", summary)
            if m:
                return m.group(1)
            m = re.search(r"\(([0-9.]+%)\)", summary)
            if m:
                return m.group(1)
            return ""
        except Exception:
            return ""

    def _apply_responsive_sidebar(self):
        if not self.sidebar:
            return
        hide = self.size.width < 120
        if hide and self.sidebar.styles.display != "none":
            self.sidebar.styles.display = "none"
            self.sidebar.display = False
            try:
                self.chat.set_class(True, "no-scrollbar")
            except Exception:
                pass
        elif not hide and self.sidebar.styles.display == "none":
            self.sidebar.styles.display = "block"
            self.sidebar.display = True
            try:
                self.chat.set_class(False, "no-scrollbar")
            except Exception:
                pass

    def apply_language(self):
        if self.sidebar:
            self.query_one("#sidebar-title", Static).update(self.tr("sidebar.title"))
        if self.sidebar:
            self.query_one("#btn-new-session", Button).label = self.tr("sidebar.new")
            self.query_one("#btn-settings", Button).label = self.tr("sidebar.settings")
        if self.input:
            self.input.set_placeholder_aware(self.tr("input.placeholder") if self._model_configured_valid() else self.tr("input.no_model"))
        if self.hint:
            self.hint.update(self.tr("hint"))
        self._refresh_status()

    def on_button_pressed(self, event):
        if event.button.id == "btn-new-session":
            self.action_new_session()
        elif event.button.id == "btn-settings":
            self.action_open_settings()

    def on_option_list_option_selected(self, event):
        if getattr(event.option_list, "id", "") == "session-list":
            self._post(self._switch_session(event.option.id))
            return
        if getattr(event.option_list, "id", "") == "palette-list" and self._palette_active:
            option = getattr(event, "option", None)
            slash = getattr(option, "id", None) if option else None
            if slash:
                self.input.value = "/" + slash + " "
                self.input.cursor_position = len(self.input.value)
                self._palette_hide()
                self.input.focus()

    # ---- 会话管理 ----

    async def _start_broker_listener(self):
        """常驻订阅 broker：实时接收对端（WebUI/外部平台）发起的消息并渲染。

        单订阅模型：TUI 发消息也统一经本监听器渲染（_agent_worker 不再消费）。
        - message_received 用 _last_user_msg 去重：TUI 自己发消息已由 _agent_worker 显示
        - 切换会话时重新订阅当前 session
        """
        self._last_user_msg = None
        try:
            from app.messaging.message_broker import get_message_broker
            broker = get_message_broker()
            while True:
                current = self.session_id
                queue = broker.subscribe(current)
                try:
                    while True:
                        if self.session_id != current:
                            break
                        try:
                            evt = await asyncio.wait_for(queue.get(), timeout=0.5)
                        except asyncio.TimeoutError:
                            continue
                        if evt is None:
                            break
                        if self.session_id != current:
                            break
                        if self._history_stale(self._session_epoch):
                            continue
                        if evt.get("type") == "message_received":
                            msg = evt.get("message") or evt.get("content") or ""
                            if msg and str(msg) == getattr(self, "_last_user_msg", None):
                                continue
                            if msg:
                                self._last_user_msg = str(msg)
                        await self._handle_event(evt)
                        if evt.get("type") in ("done", "error", "stopped"):
                            self._last_local_task_end = time.monotonic()
                finally:
                    try:
                        broker.unsubscribe(current, queue)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _register_session_listener(self):
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()

            self._msg_refresh_timer = None

            def _on_change(event_type, data):
                try:
                    if threading.current_thread() is threading.main_thread():
                        _on_change_safe(event_type, data)
                    else:
                        self.call_from_thread(_on_change_safe, event_type, data)
                except Exception:
                    pass

            def _on_change_safe(event_type, data):
                try:
                    if event_type == "messages_changed":
                        sid = (data or {}).get("session_id")
                        if sid and sid == self.session_id and not self._busy:
                            last_end = getattr(self, "_last_local_task_end", 0.0)
                            if time.monotonic() - last_end > 1.0:
                                self._schedule_history_refresh()
                        return
                    self._post(self._load_sessions())
                except Exception:
                    pass

            self._session_listener_id = store.add_listener(_on_change)
            self.set_interval(2.0, self._check_sessions_file_change)
        except Exception:
            self._session_listener_id = None

    def _schedule_history_refresh(self):
        """防抖调度聊天区刷新：300ms 内多次变化只刷新一次"""
        try:
            timer = getattr(self, "_msg_refresh_timer", None)
            if timer is not None:
                timer.reset()
                return
            self._msg_refresh_timer = self.set_timer(0.3, self._do_history_refresh)
        except Exception:
            self._post(self._load_history())

    def _do_history_refresh(self):
        self._msg_refresh_timer = None
        try:
            if not self._busy and not self._restoring_history:
                last_end = getattr(self, "_last_local_task_end", 0.0)
                if time.monotonic() - last_end <= 1.0:
                    return
                self._post(self._load_history())
        except Exception:
            pass

    def _check_sessions_file_change(self):
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            store.list_sessions(limit=1)
        except Exception:
            pass

    def on_unmount(self):
        try:
            timer = getattr(self, "_msg_refresh_timer", None)
            if timer is not None:
                timer.stop()
            self._msg_refresh_timer = None
        except Exception:
            pass
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            if getattr(self, "_session_listener_id", None) is not None:
                store.remove_listener(self._session_listener_id)
        except Exception:
            pass

    async def _load_sessions(self):
        try:
            from app.agent.loop.session_store import get_session_store

            def _fetch():
                store = get_session_store()
                metas = store.list_sessions(limit=50)
                if not metas:
                    store.get_or_create_session(self.session_id)
                    metas = store.list_sessions(limit=50)
                return metas

            metas = await asyncio.get_running_loop().run_in_executor(None, _fetch)
        except Exception as e:
            await self._append_system(self.tr("session.list_error", e))
            return
        self.session_list.clear_options()
        for meta in metas:
            title = meta.title or meta.session_id
            if meta.message_count:
                title += f"  [dim]({meta.message_count})[/dim]"
            if meta.session_id == self.session_id:
                title = f"● {title}"
            else:
                title = f"○ {title}"
            self.session_list.add_option(Option(title, id=meta.session_id))

    async def _switch_session(self, session_id):
        if self._busy:
            await self._append_system(self.tr("session.busy"))
            return
        try:
            self.workers.cancel_group(self, "history-fill")
        except Exception:
            pass
        try:
            self.workers.cancel_group(self, "agent")
        except Exception:
            pass
        self._session_epoch += 1
        from_session = self.session_id
        self.session_id = session_id
        self._current_thinking = None
        self._current_response = None
        self._current_md = ""
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        self._pending_text = ""
        self._tool_cards = {}
        self._tool_count = 0
        self._refresh_status()
        await self._load_sessions()
        await self._load_history()

    async def _create_session(self):
        try:
            from app.agent.loop.session_store import get_session_store
            store = get_session_store()
            meta = store.get_or_create_session()
            from_session = self.session_id
            self._session_epoch += 1
            self.session_id = meta.session_id
            from app.agent.loop.session_manager import get_session_manager
            get_session_manager().get_or_create(self.session_id)
            self.chat.remove_children()
            self._refresh_status()
            await self._load_sessions()
            await self._append_system(self.tr("session.created", self.session_id))
            self._history_ready.set()
            self._bootstrap_ready.set()
        except Exception as e:
            await self._append_system(self.tr("session.create_failed", e))

    async def _delete_current_session(self):
        if self.session_id in ("default", "tui"):
            await self._append_system(self.tr("session.protected"))
            return
        try:
            from app.agent.loop.session_store import get_session_store
            from app.agent.loop.session_manager import get_session_manager
            store = get_session_store()
            store.delete_session(self.session_id)
            get_session_manager().close_session(self.session_id)
            await self._append_system(self.tr("session.deleted", self.session_id))
            self._session_epoch += 1
            self.session_id = "default"
            self.chat.remove_children()
            self._refresh_status()
            await self._load_sessions()
            await self._load_history()
        except Exception as e:
            await self._append_system(self.tr("session.delete_failed", e))

    async def _delete_session_by_arg(self, arg):
        target = arg[6:].strip()
        if not target:
            await self._delete_current_session()
            return
        if target in ("default", "tui"):
            await self._append_system(self.tr("session.protected"))
            return
        try:
            from app.agent.loop.session_store import get_session_store
            from app.agent.loop.session_manager import get_session_manager
            store = get_session_store()
            store.delete_session(target)
            get_session_manager().close_session(target)
            await self._append_system(self.tr("session.deleted", target))
            if self.session_id == target:
                self.session_id = "default"
                self.chat.remove_children()
                self._refresh_status()
                await self._load_sessions()
                await self._load_history()
            else:
                await self._load_sessions()
        except Exception as e:
            await self._append_system(self.tr("session.delete_failed", e))

    # ---- 事件处理 ----

    def _submit_input(self):
        if self.input is None:
            return
        text = self.input.value.strip()
        self.input.value = ""
        self._palette_hide()
        if not text:
            return
        pending = getattr(self.input, "_paste_pending", None)
        if pending:
            marker = f"[Pasted ~{pending.count(chr(10)) + 1} lines]"
            if marker in text:
                text = text.replace(marker, pending)
            self.input._paste_pending = None
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._send(text)

    # ---- / 命令补全面板 ----

    def on_input_changed(self, event):
        if getattr(event, "input", None) is not self.input:
            return
        value = self.input.value
        self._update_palette(value)

    def on_text_area_changed(self, event):
        if getattr(event, "text_area", None) is not self.input:
            return
        self._update_palette(self.input.value)

    def _update_palette(self, value: str):
        if value.startswith("/") and " " not in value:
            query = value[1:]
            cmds = match_commands(query)
            if cmds:
                palette = self.query_one("#command-palette", CommandPalette)
                palette.set_commands(cmds)
                palette.styles.display = "block"
                self._palette_active = True
                return
        self._palette_hide()

    def _palette_move(self, delta: int):
        if not self._palette_active:
            return
        try:
            self.query_one("#command-palette", CommandPalette).move(delta)
        except Exception:
            pass

    def _palette_accept(self):
        if not self._palette_active:
            return
        try:
            palette = self.query_one("#command-palette", CommandPalette)
            cmd = palette.current()
            if cmd:
                self.input.value = "/" + cmd["slash"] + " "
                self.input.cursor_position = len(self.input.value)
        except Exception:
            pass
        self._palette_hide()
        self.input.focus()

    def _palette_hide(self):
        self._palette_active = False
        try:
            self.query_one("#command-palette", CommandPalette).styles.display = "none"
        except Exception:
            pass

    def _handle_command(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self._post(self._append_system(self.tr("commands.help")))
        elif cmd == "/clear":
            self._clear_chat()
        elif cmd == "/theme":
            self.action_toggle_theme()
        elif cmd == "/settings":
            self.action_open_settings()
        elif cmd == "/session":
            if arg:
                if arg.startswith("delete"):
                    self._post(self._delete_session_by_arg(arg))
                else:
                    self._post(self._switch_session(arg))
            else:
                self._post(self._open_session_picker())
        elif cmd == "/delete":
            self.action_delete_session()
        elif cmd == "/new":
            self.action_new_session()
        elif cmd in ("/model", "/models"):
            if arg:
                self._post(self._handle_model_command(arg))
            else:
                self.action_open_model_picker()
        else:
            self._post(self._append_system(self.tr("commands.unknown", cmd)))

    def action_open_model_picker(self):
        from app.tui.model_picker import ModelPickerScreen
        try:
            from app.core.util.agent_config import get_config
            llm = get_config().get_section("llm") or {}
            models = llm.get("models", [])
            current = llm.get("current_model", "")
            if not models:
                self._post(self._append_system(self.tr("model.info_failed", "no models configured")))
                return
        except Exception as e:
            self._post(self._append_system(self.tr("model.info_failed", e)))
            return

        def on_result(name):
            if name:
                self._post(self._switch_model(name))
        self.push_screen(ModelPickerScreen(models, current), callback=on_result)

    async def _open_session_picker(self):
        """/session 无参数时弹出会话选择框"""
        try:
            from app.agent.loop.session_store import get_session_store

            def _fetch():
                store = get_session_store()
                metas = store.list_sessions(limit=50)
                if not metas:
                    store.get_or_create_session(self.session_id)
                    metas = store.list_sessions(limit=50)
                return metas

            metas = await asyncio.get_running_loop().run_in_executor(None, _fetch)
        except Exception as e:
            await self._append_system(self.tr("session.list_error", e))
            return

        sessions = []
        for meta in metas:
            title = meta.title or meta.session_id
            if meta.message_count:
                title += f" ({meta.message_count})"
            sessions.append((meta.session_id, title))

        def on_result(result):
            if isinstance(result, tuple) and result[0] == "__delete__":
                self._post(self._delete_session_by_arg("delete " + result[1]))
                return
            if isinstance(result, tuple) and result[0] == "__renamed__":
                self._post(self._load_sessions())
                return
            if result and result != self.session_id:
                self._post(self._switch_session(result))

        from app.tui.session_picker import SessionPickerScreen
        self.push_screen(SessionPickerScreen(sessions, self.session_id), callback=on_result)

    async def _handle_model_command(self, arg):
        await self._switch_model(arg)

    async def _switch_model(self, name):
        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            llm = cfg.get_section("llm")
            models = llm.get("models", [])
            if not any(m.get("name") == name for m in models):
                await self._append_system(self.tr("model.not_found", name))
                return
            cfg.set("llm.current_model", name, persist=True)
            from app.server.routes.config import reload_llm_engine
            await reload_llm_engine()
            self.model_name = name
            self._refresh_status()
            await self._append_system(self.tr("model.switched", name))
        except Exception as e:
            await self._append_system(self.tr("model.switch_failed", e))

    def delete_model_by_name(self, name: str):
        """删除已配置的模型"""
        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            llm = cfg.get_section("llm")
            models = llm.get("models", [])
            if not any(m.get("name") == name for m in models):
                self.notify(self.tr("model.not_found", name), severity="warning")
                return
            if name == llm.get("current_model", ""):
                self.notify(self.tr("model.delete_protected"), severity="warning")
                return
            new_models = [m for m in models if m.get("name") != name]
            cfg.set("llm.models", new_models, persist=True)
            self._post(self._append_system(self.tr("model.deleted", name)))
            from app.server.routes.config import reload_llm_engine
            asyncio.create_task(reload_llm_engine())
        except Exception as e:
            self.notify(self.tr("model.delete_failed", e), severity="error")

    # ---- 动作 ----

    def key_escape(self):
        now = time.monotonic()
        if self._busy:
            if now - self._last_esc <= 0.8:
                self._last_esc = 0.0
                if self._current_loop:
                    self._current_loop.stop()
                self._busy_status = self.tr("stop.requested")
                self._refresh_status()
            else:
                self._last_esc = now
                self._busy_status = self.tr("stop.press_again")
                self._refresh_status()
        elif now - self._last_esc <= 0.8:
            self._last_esc = 0.0
            self.exit()
        else:
            self._last_esc = now

    def action_stop_or_exit(self):
        if self._busy:
            if self._current_loop:
                self._current_loop.stop()
            self._busy_status = self.tr("stop.requested")
            self._refresh_status()
        else:
            self.exit()

    def action_copy_text(self):
        selection = None
        try:
            selection = self.screen.get_selected_text()
        except Exception:
            selection = None
        if selection:
            self.copy_to_clipboard(selection)
            self.screen.clear_selection()
            return
        try:
            if self.input is not None:
                self.input.action_copy()
        except SkipAction:
            pass

    def action_copy_selection(self):
        """Ctrl+Y 复制当前选区"""
        selection = None
        try:
            selection = self.screen.get_selected_text()
        except Exception:
            selection = None
        if selection:
            self.copy_to_clipboard(selection)
            self.screen.clear_selection()
            self.notify("已复制选区", severity="information")

    def action_toggle_theme(self):
        self.theme = "cellium-light" if self.theme == "cellium-dark" else "cellium-dark"
        self.save_theme_pref(self.theme)
        self._apply_rich_md_theme()
        self.refresh_theme_widgets()

    def refresh_theme_widgets(self):
        from app.tui.widgets import LogoImage, ToolCallCard, HistoryMarkdown, ThinkingBlock
        try:
            self.query_one(LogoImage).refresh_theme()
        except Exception:
            pass
        try:
            for card in self.query(ToolCallCard):
                card.refresh_theme()
        except Exception:
            pass
        try:
            for w in self.query(HistoryMarkdown):
                w.refresh_theme()
        except Exception:
            pass
        try:
            for w in self.query(ThinkingBlock):
                w.refresh()
        except Exception:
            pass
        try:
            self.chat.refresh()
        except Exception:
            pass

    def action_new_session(self):
        self._post(self._create_session())

    def action_delete_session(self):
        self._post(self._delete_current_session())

    def action_open_settings(self):
        from app.tui.settings_screen import SettingsScreen
        if isinstance(self.screen, SettingsScreen):
            return
        self.push_screen(SettingsScreen(current_theme=self.theme))

    def action_clear_chat(self):
        self._clear_chat()

    # ---- 发送 ----

    def _send(self, text):
        if self._busy:
            self._post(self._append_system(self.tr("busy")))
            return
        self._busy = True
        self._busy_status = self.tr("busy")
        self._start_busy_animation()
        self.run_worker(self._agent_worker(text), group="agent", exclusive=True)

    async def _agent_worker(self, text):
        epoch = self._session_epoch
        try:
            if not self._history_ready.is_set():
                try:
                    await asyncio.wait_for(self._history_ready.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
            await self._append_user(text, force_scroll=True)
            # 记录本端已显示的用户消息，供常驻监听器去重 message_received
            self._last_user_msg = text
            if not self._bootstrap_ready.is_set():
                await self._append_system(self.tr("initializing"))
                try:
                    await asyncio.wait_for(self._bootstrap_ready.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            mgr = await asyncio.to_thread(self._get_agent_mgr)
            lock = await mgr.get_lock(self.session_id)
            async with lock:
                loop = await self._get_loop_in_thread(mgr)
                self._current_loop = loop
                memory = await asyncio.to_thread(
                    self._get_session_memory, self.session_id
                )

                from app.server.task_manager import get_task_manager
                task_mgr = get_task_manager()

                started = await task_mgr.start_task(
                    session_id=self.session_id,
                    agent_loop=loop,
                    user_input=text,
                    memory=memory,
                )
                # 事件统一由常驻订阅监听器（_start_broker_listener）消费渲染，
                # _agent_worker 只负责启动任务并等待其结束（避免双订阅竞态）。
                if started:
                    self._last_local_task_end = 0.0
                    while True:
                        if self._history_stale(epoch):
                            break
                        if not task_mgr.has_running_task(self.session_id):
                            break
                        await asyncio.sleep(0.3)
                # 标记本端任务结束（抑制随后 update_message_count 触发的刷新）
                self._last_local_task_end = time.monotonic()
                self._busy = False
                self._busy_status = ""
                self._stop_busy_animation()
                self._current_loop = None
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._post(self._append_system(self.tr("error.exec", e)))
        finally:
            self._busy = False
            self._busy_status = ""
            self._stop_busy_animation()
            self._current_loop = None

    def _get_agent_mgr(self):
        """后台线程中获取 AgentLoopManager"""
        from app.agent.loop import AgentLoopManager
        return AgentLoopManager.get_instance()

    async def _get_loop_in_thread(self, mgr):
        """在后台线程获取/创建当前会话 AgentLoop（"""
        import asyncio as _asyncio
        loop = await _asyncio.to_thread(mgr.get_loop_sync_or_create, self.session_id)
        return loop

    def _get_session_memory(self, session_id):
        from app.agent.loop.session_manager import get_session_manager
        return get_session_manager().get_or_create(session_id).memory

    def _touch_busy(self):
        """有输出活动时重置 spinner 计时"""
        if self._busy_animation_running:
            self._busy_started = time.monotonic()

    async def _handle_event(self, evt):
        t = evt.get("type")
        if t == "status":
            self._refresh_status()
        elif t == "message_received":
            # 对端（WebUI/外部平台）发起的消息：立即显示用户消息
            msg = evt.get("message") or evt.get("content") or ""
            if msg:
                await self._append_user(str(msg))
        elif t == "thinking":
            self._touch_busy()
            await self._append_thinking(evt.get("content", ""))
        elif t in ("content_chunk", "content"):
            self._touch_busy()
            await self._append_chunk(evt.get("content", ""))
        elif t == "tool_start":
            self._touch_busy()
            await self._add_tool_card(evt)
        elif t == "tool_result":
            self._touch_busy()
            self._update_tool_card(evt)
        elif t == "done":
            await self._finalize_render()
            self._finish_response()
            self._refresh_status()
        elif t in ("stopped", "control_loop_stop", "heuristic_stop"):
            await self._finalize_render()
            self._finish_response()
        elif t == "error":
            await self._append_system(self.tr("error", evt.get("error", "")))
        elif t == "heuristic_redirect":
            await self._append_system(self.tr("redirect"))
        elif t == "hybrid_phase":
            pass
        elif t == "supplement_injected":
            content = evt.get("content", "")
            if self._current_response is not None:
                self._current_response = None
                self._current_md = ""
            if content:
                preview = content if len(content) <= 60 else content[:60] + "…"
                await self._append_system(self.tr("supplement.injected", preview))
                await self._append_system(self.tr("supplement.continue"))

    # ---- 渲染辅助 ----

    async def _append_user(self, content, force_scroll=False):
        await self.chat.mount(UserMessage(content))
        if force_scroll:
            self.chat.force_scroll_bottom()
        else:
            self.chat.scroll_to_follow()

    async def _append_thinking(self, content):
        if not content or not content.strip():
            return
        if self._current_thinking is None:
            self._current_thinking = ThinkingBlock("")
            await self.chat.mount(self._current_thinking)
        self._current_thinking.append_text(content)
        self.chat.scroll_to_follow()

    async def _append_chunk(self, content):
        if not content:
            return
        if self._thought_mode:
            self._thought_raw += content
            await self._check_thought_complete()
            return
        self._pending_text += content
        if self._find_thought_start(self._pending_text) is not None:
            await self._check_pending_thought()
        else:
            await self._flush_pending_text()

    async def _check_pending_thought(self):
        start = self._find_thought_start(self._pending_text)
        if start is None:
            await self._flush_pending_text()
            return
        pre = self._pending_text[:start]
        self._thought_fenced = bool(re.search(r'```json\s*$', pre, re.IGNORECASE))
        pre = re.sub(r'```json\s*$', '', pre, flags=re.IGNORECASE).strip()

        if pre:
            await self._append_text(pre)

        self._thought_mode = True
        self._thought_raw = self._pending_text[start:]
        self._pending_text = ""
        self._ensure_thinking_block_mount()
        await self._check_thought_complete()

    async def _check_thought_complete(self):
        if not self._thought_closed(self._thought_raw):
            if len(self._thought_raw) > 4000:
                await self._append_text(self._thought_raw)
                self._finish_thought()
            return

        json_end = self._find_json_end(self._thought_raw)
        json_str = self._thought_raw[:json_end]

        reasoning = ""
        data = None
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and "reasoning" in data and "action" in data:
                reasoning = str(data.get("reasoning", ""))
        except (json.JSONDecodeError, ValueError):
            data = None

        if data is None or not isinstance(data, dict) or "action" not in data:
            await self._append_text(self._thought_raw)
            if self._current_thinking is not None:
                try:
                    self._current_thinking.remove()
                except Exception:
                    pass
                self._current_thinking = None
            self._finish_thought()
            return

        if reasoning and self._current_thinking is not None:
            self._current_thinking.append_text(reasoning)

        rest = self._thought_raw[json_end:]

        rest = rest.strip()
        if self._thought_fenced and rest.startswith("```"):
            rest = rest[3:].strip()

        if rest:
            await self._append_text(rest)

        self._finish_thought()

    def _extract_reasoning(self, raw: str) -> str:
        m = re.search(r'\{\s*"reasoning"\s*:\s*("(?:\\.|[^"\\])*"|\[[^\]]*\]|\S*)', raw)
        if not m:
            return ""
        val = m.group(1).strip()
        if val.startswith('"') and val.endswith('"'):
            try:
                return json.loads(val)
            except Exception:
                return val[1:-1]
        return val

    async def _append_text(self, text):
        if not text or not text.strip():
            return
        import re as _re
        text = _re.sub(r'\n{3,}', '\n\n', text)
        if self._current_response is None:
            text = text.lstrip()
            if not text:
                return
            if self._active_tool_card is not None:
                self._active_tool_card.finish()
                self._active_tool_card = None
            if self._current_thinking is not None:
                self._current_thinking.finish()
                self._current_thinking = None
            self._current_response = AssistantMessage("")
            await self.chat.mount(self._current_response)
        self._current_md += text
        await self._schedule_md_render()

    async def _schedule_md_render(self):
        if getattr(self, "_md_render_timer", None) is not None:
            return
        timer = self.set_timer(0.05, self._do_md_render)
        self._md_render_timer = timer

    def _do_md_render(self):
        self._md_render_timer = None
        resp = self._current_response
        if resp is None or not self._current_md:
            return
        try:
            import asyncio
            asyncio.get_running_loop().create_task(self._render_now())
        except Exception:
            pass

    async def _render_now(self):
        try:
            await self._current_response.update(self._current_md)
        except Exception:
            pass
        self.chat.scroll_to_follow()

    async def _flush_pending_text(self):
        if not self._pending_text:
            return
        if self._is_thought_prefix(self._pending_text):
            return 
        await self._append_text(self._pending_text)
        self._pending_text = ""

    def _is_thought_prefix(self, text: str) -> bool:
        """判断文本尾部是否可能是 JSON 思考协议开头的部分片段

        支持两种格式:
        1. 直接 JSON: '{', '{ ', '{ "r', '{ "reasoning' 等
        2. 代码块包裹: '```json', '```json\n{' 等（支持流式切碎的任意前缀）
        """
        m = re.search(r'```([a-z]*)\s*$', text, re.IGNORECASE)
        if m and "json".startswith(m.group(1)):
            return True

        brace = text.rfind("{")
        if brace == -1:
            return False
        tail = text[brace + 1:]
        prefix_len = 0
        i = 0
        while i < len(tail) and tail[i].isspace():
            i += 1
        if i < len(tail) and tail[i] == '"':
            i += 1
        for j, ch in enumerate("reasoning"):
            if i + j >= len(tail):
                return True
            if tail[i + j] != ch:
                return False
            prefix_len += 1
        if len(tail) - i == prefix_len:
            return True
        rest = tail[i + prefix_len:]
        return rest in ('"', '')

    def _find_thought_start(self, text):
        """定位 JSON 思考协议开始位置：支持两种格式

        1. 直接 JSON: {"reasoning": ...}
        2. 代码块包裹: ```json\n{"reasoning": ...}\n```
        """
        m = re.search(r'```json\s*\n?\s*(\{\s*"reasoning)', text, re.IGNORECASE)
        if m:
            return m.start(1)

        m = re.search(r'\{\s*"reasoning', text)
        return m.start() if m else None

    def _ensure_thinking_block_mount(self):
        if self._current_thinking is None:
            self._current_thinking = ThinkingBlock("")
            if self._current_response is not None:
                self.chat.mount(self._current_thinking, before=self._current_response)
            else:
                self.chat.mount(self._current_thinking)
        self.chat.scroll_to_follow()

    def _find_json_end(self, raw: str) -> int:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1 
        return -1

    def _thought_closed(self, raw: str) -> bool:
        return self._find_json_end(raw) != -1

    def _finish_thought(self):
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        if self._current_thinking is not None:
            self._current_thinking.finish()
            self._current_thinking = None
        self.chat.scroll_to_follow()

    async def _add_tool_card(self, evt):
        # 先 flush 已累积的正文（否则工具卡会吞掉前面的流式文本，
        # 如"开始"在 tool_start 前已累积但 markdown 渲染 timer 未触发）
        if self._current_response is not None and self._current_md:
            await self._render_now()
        if self._current_response is not None:
            self._current_response = None
            self._current_md = ""
            self._pending_text = ""
        if self._thought_mode:
            self._thought_mode = False
            self._thought_raw = ""
            self._thought_fenced = False
        # 工具调用开始时结束当前 thinking 块：后续 thinking 事件作为新块
        if self._current_thinking is not None:
            self._current_thinking.finish()
            self._current_thinking = None
        if evt.get("gene"):
            return
        call_id = evt.get("call_id") or f"tc_{len(self._tool_cards)}"
        # 同一轮批量工具调用（tool_start 连续到达、尚无 tool_result）合并到同一卡；
        # 已有完成调用说明进入下一轮，开新卡按顺序排列
        same_round = self._active_tool_card is not None and not any(
            c["status"] in ("success", "error") for c in self._active_tool_card._calls.values()
        )
        if self._active_tool_card is not None and not same_round:
            self._active_tool_card.finish()
        if self._active_tool_card is None or not same_round:
            self._active_tool_card = ToolCallCard()
            await self.chat.mount(self._active_tool_card)
        self._active_tool_card.add_call(
            call_id,
            tool=evt.get("tool", ""),
            description=evt.get("description", ""),
            arguments=evt.get("arguments", {}),
        )
        self._tool_cards[call_id] = self._active_tool_card
        self._tool_count += 1
        self.chat.scroll_to_follow()

    def _update_tool_card(self, evt):
        card = self._tool_cards.get(evt.get("call_id", ""))
        if card:
            card.update_call(evt.get("call_id", ""), evt.get("result"), evt.get("duration_ms", 0))

    async def _append_system(self, content, markup=False):
        await self.chat.mount(Static(content, classes="system-msg", markup=markup))
        self.chat.scroll_to_follow()

    async def _finalize_render(self):
        """收尾前确保最终内容已渲染"""
        try:
            if getattr(self, "_md_render_timer", None) is not None:
                try:
                    self._md_render_timer.stop()
                except Exception:
                    pass
                self._md_render_timer = None
            if self._pending_text and not self._thought_mode:
                await self._append_text(self._pending_text)
                self._pending_text = ""
            if self._current_response is not None and self._current_md:
                await self._render_now()
        except Exception:
            pass

    def _finish_response(self):
        if self._current_thinking is not None:
            self._current_thinking.finish()
        self._current_thinking = None
        self._current_response = None
        self._current_md = ""
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        self._pending_text = ""
        if self._active_tool_card is not None:
            self._active_tool_card.finish()
            self._active_tool_card = None

    def _clear_chat(self):
        self.chat.remove_children()
        self._current_thinking = None
        self._current_response = None
        self._current_md = ""
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        self._pending_text = ""
        self._tool_cards = {}
        self._active_tool_card = None
        self._restoring_history = False

    def _history_stale(self, epoch: int) -> bool:
        return epoch != self._session_epoch

    def _widget_from_spec(self, spec, md=None):
        kind = spec["kind"]
        if kind == "user":
            return UserMessage(spec["content"])
        if kind == "thinking":
            return ThinkingBlock(spec["content"])
        if kind == "assistant":
            from app.tui.widgets import HistoryMarkdown
            content = spec.get("content", "") or ""
            w = HistoryMarkdown("")
            if md is not None:
                w._source = content
                w._md = md
            else:
                w._source = content
            return w
        return ToolCallCard()

    def _post_mount(self, widget, spec):
        if isinstance(widget, ToolCallCard):
            for idx, call in enumerate(spec["calls"]):
                cid = f"hist_{self._tool_count}_{idx}"
                widget.add_call(cid, tool=call["tool"], arguments=call.get("arguments", {}))
                widget.update_call(cid, call.get("result"), call.get("duration_ms", 0))
                self._tool_count += 1
            widget.finish()
        elif isinstance(widget, ThinkingBlock):
            widget.finish()

    async def _load_history(self):
        epoch = self._session_epoch
        self._history_ready.clear()
        self._restoring_history = True
        self._history_offset = 0
        self._history_has_more = False
        was_following = self.chat.following
        prev_scroll_y = self.chat.scroll_y
        try:
            from app.tui.history_render import build_history_plan
            hist_limit = self._history_limit

            def _build_plan_and_md():
                from app.tui.history_render import build_history_plan
                from app.tui.widgets import _build_rich_md
                p, dropped = build_history_plan(self.session_id, hist_limit)
                mds = []
                for spec in p:
                    if spec.get("kind") == "assistant" and spec.get("content"):
                        try:
                            mds.append(_build_rich_md(spec["content"]))
                        except Exception:
                            mds.append(None)
                    else:
                        mds.append(None)
                return p, dropped, mds

            plan, dropped, mds = await asyncio.get_running_loop().run_in_executor(
                None, _build_plan_and_md
            )
            if self._history_stale(epoch):
                return
            with self.batch_update():
                await self.chat.remove_children()
            self._history_has_more = dropped > 0
            if plan:
                widgets = [self._widget_from_spec(s, md) for s, md in zip(plan, mds)]
                if self._history_stale(epoch):
                    return
                with self.batch_update():
                    await self.chat.mount(*widgets)
                    for w, s in zip(widgets, plan):
                        self._post_mount(w, s)
                try:
                    self.chat.refresh(layout=True)
                except Exception:
                    pass
                for _ in range(4):
                    await asyncio.sleep(0)
                if was_following:
                    self.chat.force_scroll_bottom()
                else:
                    try:
                        self.chat.scroll_to(
                            y=min(prev_scroll_y, self.chat.max_scroll_y),
                            animate=False,
                            immediate=True,
                        )
                    except Exception:
                        pass
                self.set_timer(0.15, self._scroll_history_done)
                self._restoring_history = False
                self._history_ready.set()
                self._bootstrap_ready.set()
                self._set_terminal_title()
                if not self._busy:
                    self.input.focus()
                return
            self._restoring_history = False
            self._history_ready.set()
            self._bootstrap_ready.set()
            self._set_terminal_title()
            if not self._busy:
                self.input.focus()
        except Exception:
            if self._history_stale(epoch):
                return
            self._restoring_history = False
            try:
                await self._load_history()
            except Exception:
                pass

    def _remove_history_loading(self):
        """移除历史加载提示组件"""
        if self._history_loading is not None:
            try:
                self._history_loading.remove()
            except Exception:
                pass
            self._history_loading = None

    def _scroll_history_done(self):
        if self.chat and self.chat.following:
            self.chat.scroll_end(animate=False)

    def _on_chat_scrolled_top(self):
        if not self._history_has_more or self._history_loading_more:
            return
        if self._restoring_history or self._busy:
            return
        try:
            self._post(self._load_more_history())
        except Exception:
            pass

    async def _load_more_history(self):
        if self._history_loading_more or not self._history_has_more:
            return
        if self._restoring_history or self._busy:
            return
        self._history_loading_more = True
        epoch = self._session_epoch
        try:
            from app.tui.history_render import build_history_plan
            from app.tui.widgets import _build_rich_md
            limit = self._history_limit
            new_offset = self._history_offset + limit

            def _build_more():
                p, dropped = build_history_plan(self.session_id, limit, new_offset)
                mds = []
                for spec in p:
                    if spec.get("kind") == "assistant" and spec.get("content"):
                        try:
                            mds.append(_build_rich_md(spec["content"]))
                        except Exception:
                            mds.append(None)
                    else:
                        mds.append(None)
                return p, dropped, mds

            plan, dropped, mds = await asyncio.get_running_loop().run_in_executor(
                None, _build_more
            )
            if self._history_stale(epoch):
                return
            if not plan:
                self._history_has_more = False
                return

            scroll_y = self.chat.scroll_y
            old_max = self.chat.max_scroll_y
            first_widget = next(iter(self.chat.children), None)
            widgets = [self._widget_from_spec(s, md) for s, md in zip(plan, mds)]
            with self.batch_update():
                await self.chat.mount(*widgets, before=first_widget)
                for w, s in zip(widgets, plan):
                    self._post_mount(w, s)
            try:
                for _ in range(5):
                    await asyncio.sleep(0)
                new_max = self.chat.max_scroll_y
                added_height = new_max - old_max
                base_scroll = scroll_y + added_height
                self.chat.scroll_to(y=base_scroll, animate=False, immediate=True)
            except Exception:
                pass
            self._history_offset = await self._trim_history_widgets(new_offset)
            self._history_has_more = dropped > 0
        except Exception:
            pass
        finally:
            self._history_loading_more = False

    async def _trim_history_widgets(self, new_offset: int) -> int:
        try:
            max_widgets = max(200, self._history_limit * 5)
            children = list(self.chat.children)
            if len(children) <= max_widgets:
                return new_offset
            excess = len(children) - max_widgets
            old_widgets = children[:excess] 
            try:
                await self.chat.remove_children(old_widgets)
            except Exception:
                for w in old_widgets:
                    try:
                        await w.remove()
                    except Exception:
                        pass
            return max(0, new_offset - excess)
        except Exception:
            return new_offset


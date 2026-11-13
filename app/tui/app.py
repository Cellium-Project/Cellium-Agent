# -*- coding: utf-8 -*-
import logging
import os

logger = logging.getLogger(__name__)

os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

# Patch Textual 8.2.8 selection scroll delta sign bug (https://github.com/Textualize/textual/issues/6639)
def _textual_version_tuple():
    try:
        return tuple(int(p) for p in _txt.__version__.split(".")[:3])
    except Exception:
        return ()

try:
    import textual.selection as _sel
    import textual as _txt
    if _textual_version_tuple() == (8, 2, 8):
        _SelectStart = _sel.SelectStart
        _orig_pointer_start_offset = _SelectStart.pointer_start_offset.fget
        def _patched_pointer_start_offset(self):
            return (
                self.container.region.offset
                + self.container_pointer_delta
                - (self.container.scroll_offset - self.container_initial_scroll_offset)
            )
        _SelectStart.pointer_start_offset = property(_patched_pointer_start_offset)

        import textual.screen as _screen_mod
        def _patched_watch_selections(self_screen, old_selections, selections):
            for widget in old_selections.keys() | selections.keys():
                if old_selections.get(widget) == selections.get(widget):
                    continue
                widget.selection_updated(selections.get(widget, None))
        _screen_mod.Screen._watch_selections = _patched_watch_selections
    else:
        logger.debug("textual %s: selection patch not applied", getattr(_txt, "__version__", ""))
except Exception as e:
    logger.debug("textual selection patch skipped: %s", e)

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
from app.tui.md_buffer import MarkdownBuffer
from app.tui.widgets import (
    CommandInput,
    CommandPalette,
    FilePanel,
    UserMessage,
    DelegationNotice,
    AssistantMessage,
    ThinkingBlock,
    ReasoningBlock,
    ToolCallCard,
    LogoImage,
    ChatScroll,
    Hint,
    NoSelectStatic,
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
    width: 22%;
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
    text-wrap: nowrap;
    text-overflow: ellipsis;
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

#delegation-bar {
    height: auto;
    max-height: 8;
    width: 100%;
    min-width: 0;
    padding: 0 1;
    background: $surface;
    color: $text-muted;
    display: none;
    text-wrap: nowrap;
    text-overflow: ellipsis;
}

#delegation-bar.visible {
    display: block;
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
    background: transparent;
}

#file-panel {
    display: none;
    height: auto;
    max-height: 15;
    background: transparent;
}

#file-panel-title {
    height: 2;
    padding: 0 2;
    color: $primary;
    text-style: bold;
    content-align: left middle;
    background: transparent;
}

#file-panel-list {
    height: auto;
    max-height: 10;
    padding: 1 0;
    border: none;
    background: transparent;
}

#file-panel-list:focus {
    border: none;
    background-tint: transparent;
}

#file-panel-footer {
    height: 2;
    padding: 0 2;
    color: $text-muted;
    content-align: left top;
    background: transparent;
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

.user-msg.rewindable:hover {
    text-style: underline;
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

.delegation-notice {
    margin: 1 0;
    padding: 0 1;
    border-left: solid $delegate-accent;
    background: $panel 55%;
}

.delegation-notice:focus {
    background: $panel 85%;
}

Screen > .screen--selection {
    color: $background;
    background: $foreground 100%;
}

Toast {
    align-horizontal: right;
    align-vertical: top;
    margin: 1 2 0 0;
    layer: overlay;
}

ToastRack {
    align-horizontal: right;
    align-vertical: top;
    dock: top;
    height: auto;
    width: 1fr;
    layout: vertical;
}
"""

def mode_badge_markup(mode: str, dark: bool = True) -> str:
    """侧栏收起时的模式标识"""
    if mode == "plan":
        color = "#a78bfa" if dark else "#7c5cff"
        return f"[bold {color}]PLAN[/]"
    if mode == "delegate":
        color = "#f5a524" if dark else "#b45309"
        return f"[bold {color}]DELEGATE[/]"
    color = "#2ff0ab" if dark else "#0b8a63"
    return f"[bold {color}]RUN[/]"


_THEME_PREF_FILE = None


def _theme_pref_path_impl() -> str:
    """惰性计算主题偏好文件路径（写 CWD，pip 场景安全）"""
    global _THEME_PREF_FILE
    if _THEME_PREF_FILE is None:
        from app.core.util.runtime_paths import resolve_dir_writable
        _THEME_PREF_FILE = os.path.join(resolve_dir_writable("data"), "tui_theme.json")
    return _THEME_PREF_FILE


def copy_selection_if_any(app) -> bool:
    """复制当前选区；无选区返回 False"""
    try:
        selection = app.screen.get_selected_text()
    except Exception:
        return False
    if not selection:
        return False
    app.copy_to_clipboard(selection)
    app.screen.clear_selection()
    app.notify(app.tr("common.copied_selection"), severity="information")
    return True


class CelliumTUI(App):
    ENABLE_SELECT_AUTO_SCROLL = True  
    TITLE = "Cellium Agent"
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+q", "stop_or_exit", "退出", priority=True, show=False),
        Binding("ctrl+d", "stop_or_exit", "退出", priority=True),
        Binding("ctrl+c", "copy_text", "复制", show=False),
        Binding("ctrl+y", "copy_selection", "复制选区", show=False),
        Binding("ctrl+t", "toggle_theme", "切换主题"),
        Binding("ctrl+n", "new_session", "新建会话"),
        Binding("ctrl+s", "open_settings", "设置"),
        Binding("ctrl+l", "clear_chat", "清屏"),
        Binding("shift+tab", "toggle_run_mode", "模式切换", show=False, priority=True),
    ]

    def __init__(self, bootstrap=None, session_id=None):
        super().__init__()
        register_cellium_themes(self)
        self.theme = self.load_theme_pref()
        self.lang = self.load_lang_pref()
        self.bootstrap = bootstrap
        self.session_id = session_id or self._default_session_id()
        self.model_name = self._current_model_name()
        self._busy = False
        self._current_loop = None
        self._busy_frame = 0
        self._busy_status = ""
        self._busy_animation_running = False
        self._busy_started = 0.0
        self._global_blink_counter = 0
        self._global_blink_timer = None
        self._session_epoch = 0
        self._app_closing = False
        self._last_session_ids = []
        self._history_limit = self._load_history_limit()
        self._history_loading = None
        self._current_thinking = None
        self._current_reasoning = None
        self._current_response = None
        self._current_md = ""
        self._md_buffer = MarkdownBuffer()
        self._thought_mode = False    # 是否处于 JSON 思考协议解析中
        self._thought_raw = ""        # 思考协议原始 JSON 累积
        self._thought_fenced = False  # 思考协议是否被 ```json 代码块包裹
        self._pending_text = ""       # 待定文本
        self._md_render_timer = None  # Markdown 流式渲染节流定时器
        self._tool_cards = {}
        self._subagent_cards = {}
        self._active_tool_card = None
        self._tool_count = 0
        self._last_esc = 0.0
        self._show_thinking = True
        self._run_mode = "run"
        self._show_sidebar = True
        self._palette_active = False
        self._file_panel_active = False
        self._file_panel_dir = ""
        self._restoring_history = False 
        self._local_rewind = False
        self._tracked_ids = set()
        self._delegation_state = {}
        self._delegation_timers = {}
        self._delegation_text = ""
        self._delegation_frame = 0
        self._session_list_entries = []

        self.header = None
        self.body = None
        self.sidebar = None
        self.session_list = None
        self.chat = None
        self.input = None
        self.file_panel = None
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
        self._history_meta = None
        self._history_rev = None
        self._backfill_pending = False

    def tr(self, key, *args):
        from app.tui.i18n import t
        return t(self.lang, key, *args)

    # ---- 初始化 ----

    def _default_session_id(self):
        try:
            from app.agent.loop.session_store import get_session_store
            last = get_session_store().get_client_last_session("tui")
            if last:
                return last
        except Exception:
            pass
        try:
            from app.core.util.agent_config import get_config
            return get_config().get("agent.default_session_id", "default")
        except Exception:
            return "default"

    def _current_model_name(self):
        try:
            from app.server import session_llm
            override = session_llm.get_model(self.session_id)
            if override:
                return override
        except Exception:
            pass
        try:
            from app.core.util.agent_config import get_config
            llm = get_config().get_section("llm") or {}
            models = llm.get("models", [])
            current = llm.get("current_model", "")
            if current:
                return current
            return models[0].get("name", "") if models else ""
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
                yield NoSelectStatic(self.tr("sidebar.title"), id="sidebar-title")
                yield Button(self.tr("sidebar.new"), id="btn-new-session", variant="default")
                yield OptionList(id="session-list")
                with Vertical(id="sidebar-bottom"):
                    yield Button(self.tr("sidebar.settings"), id="btn-settings", variant="default")
            with Vertical(id="chat-column"):
                yield NoSelectStatic("", id="delegation-bar")
                yield ChatScroll(id="chat")
                with Vertical(id="footer-bar"):
                    yield CommandPalette(id="command-palette")
                    yield FilePanel(id="file-panel")
                    yield CommandInput(id="input", placeholder="")
                    yield Hint(self.tr("hint"), id="hint")
        with Horizontal(id="status-bar"):
            yield NoSelectStatic("", id="status-left")
            yield NoSelectStatic("", id="status-right")

    def on_mount(self):
        self.body = self.query_one("#body", Horizontal)
        self.sidebar = self.query_one("#sidebar", Vertical)
        self.session_list = self.query_one("#session-list", OptionList)
        self.chat = self.query_one("#chat", VerticalScroll)
        self.input = self.query_one("#input", CommandInput)
        self.file_panel = self.query_one("#file-panel", FilePanel)
        self.hint = self.query_one("#hint", Hint)
        self.status_left = self.query_one("#status-left", NoSelectStatic)
        self.status_right = self.query_one("#status-right", NoSelectStatic)
        self.delegation_bar = self.query_one("#delegation-bar", NoSelectStatic)
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
        self._apply_store_mode()
        self._setup_select_scope()
        self.set_interval(2.0, self._sync_subagent_cards)
        # 全局 blink timer，所有 spinner 共享
        self._global_blink_timer = self.set_interval(0.5, self._tick_global_blink)
        # 委托状态条单独高频刷新（0.12s ≈ 8fps）
        self._delegation_timer = self.set_interval(0.12, self._tick_delegation_bar)
        # 常驻订阅 broker：空闲时也能实时收到对端（WebUI/外部平台）发起的消息
        self._post(self._start_broker_listener())

    def _setup_select_scope(self):
        try:
            from textual.widget import Widget
            from textual.widgets import Toast
            Toast.ALLOW_SELECT = False
        except Exception:
            pass
        try:
            inside_chat = set(self.chat.walk_children(Widget))
            for w in self.screen.walk_children(Widget):
                if w in inside_chat:
                    continue
                try:
                    w.ALLOW_SELECT = False
                except Exception:
                    pass
        except Exception:
            pass

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
            self._schedule_session_prompt_reload()

    def _converge_runtime(self) -> None:
        try:
            import sys as _sys
            mod = _sys.modules.get("app.agent.loop.agent_loop_manager")
            if mod is None:
                return
            mgr = mod.AgentLoopManager.get_instance()
            for meta in list(getattr(mgr, "_loops", {}).values()):
                agent_loop = getattr(meta, "agent_loop", None)
                if agent_loop is None:
                    continue
                try:
                    agent_loop.stop()
                except Exception:
                    pass
                shell = getattr(agent_loop, "shell", None)
                if shell is not None:
                    try:
                        shell.terminate()
                    except Exception:
                        pass
        except Exception:
            pass

    def on_app_focus(self, event):
        if self.input is not None:
            try:
                if self.focused is None or self.focused is not self.input:
                    self.input.focus()
            except Exception:
                pass
        self._post(self._reload_history_if_stale())

    def _history_revision(self):
        try:
            from app.tui.history_render import history_revision
            return history_revision(self.session_id)
        except Exception:
            return None

    async def _reload_history_if_stale(self):
        if self._busy or self._restoring_history or not self._history_ready.is_set():
            return
        rev = await asyncio.to_thread(self._history_revision)
        if rev is None or rev == self._history_rev:
            return
        await self._load_history()

    def on_app_blur(self, event):
        pass

    async def _welcome(self):
        await self._load_history()
        await self._check_model_setup()

    def _update_model_placeholder(self):
        try:
            if self.input:
                self.input.set_placeholder_aware(
                    self.tr("input.placeholder") if self._model_configured_valid() else self.tr("input.no_model")
                )
        except Exception:
            pass

    async def _check_model_setup(self):
        try:
            self._update_model_placeholder()
            if self._model_configured_valid():
                return
            await self._append_system(self.tr("model.setup_hint"), markup=True)
            self._refresh_status()
        except Exception:
            pass

    def _model_configured_valid(self) -> bool:
        try:
            from app.core.util.agent_config import get_config
            llm = get_config().get_section("llm") or {}
            models = llm.get("models", [])
            if not models:
                return False
            current = llm.get("current_model", "")
            target = next((m for m in models if m.get("name") == current), None) or models[0]
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

    def _tick_global_blink(self):
        self._global_blink_counter += 1
        for w in self.chat.query("ToolCallCard"):
            if not w._done:
                w._tick()
        try:
            self._refresh_session_running()
        except Exception:
            pass

    def _tick_delegation_bar(self):
        if not self._delegation_state:
            return
        self._delegation_frame += 1
        if not any(
            (v.get("status") or "running") not in self._DELEGATION_TERMINAL
            for v in self._delegation_state.values()
        ):
            return
        try:
            self._render_delegation_bar()
        except Exception:
            pass

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
        # 侧栏收起时 logo 不可见，用状态栏补一个模式标识；委托模式始终显示
        if not self._sidebar_visible() or self._run_mode == "delegate":
            tag = mode_badge_markup(self._run_mode, self.theme == "cellium-dark")
            right = f"{right}  {tag}" if right else tag
        if self.status_right:
            self.status_right.update(right)

    def _sidebar_visible(self) -> bool:
        return self.size.width >= 120

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
        hide = not self._sidebar_visible()
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
        # 侧栏显示后按真实宽度重算标题
        self.set_timer(0.2, self._reload_session_prompts)

    def apply_language(self):
        if self.sidebar:
            self.query_one("#sidebar-title", Static).update(self.tr("sidebar.title"))
        if self.sidebar:
            self.query_one("#btn-new-session", Button).label = self.tr("sidebar.new")
            self.query_one("#btn-settings", Button).label = self.tr("sidebar.settings")
        self._update_model_placeholder()
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
        if getattr(event.option_list, "id", "") == "file-panel-list" and self._file_panel_active:
            option = getattr(event, "option", None)
            name = getattr(option, "id", None) if option else None
            self._file_panel_accept(name)

    # ---- 会话管理 ----

    async def _start_broker_listener(self):
        """常驻订阅 broker：实时接收对端（WebUI/外部平台）发起的消息并渲染。

        单订阅模型：TUI 发消息也统一经本监听器渲染（_agent_worker 不再消费）。
        - message_received 用 _last_user_msg 去重：TUI 自己发消息已由 _agent_worker 显示
        - 切换会话时重新订阅当前 session
        - 订阅/消费异常时自动重连
        """
        import logging as _logging
        _b_logger = _logging.getLogger(__name__)
        self._last_user_msg = None
        from app.messaging.message_broker import get_message_broker
        while True:
            current = None
            queue = None
            try:
                broker = get_message_broker()
                current = self.session_id
                queue = broker.subscribe(current)
                _b_logger.info("[BrokerListener] 订阅成功 | session=%s", current)
            except Exception as e:
                _b_logger.error("[BrokerListener] 订阅失败，重试中 | error=%s", e)
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    raise
                continue
            _idle_ticks = 0
            try:
                while True:
                    if self.session_id != current:
                        _b_logger.info("[BrokerListener] 会话切换 | %s→%s，重新订阅", current, self.session_id)
                        break
                    try:
                        evt = await asyncio.wait_for(queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        # 自检：订阅若被外部清理则重新订阅，防"无订阅者"失联
                        _idle_ticks += 1
                        if _idle_ticks >= 20:
                            _idle_ticks = 0
                            if not broker.is_subscribed(current, queue):
                                _b_logger.warning("[BrokerListener] 检测到订阅已丢失，重新订阅 | session=%s", current)
                                break
                        continue
                    if evt is None:
                        _b_logger.info("[BrokerListener] 收到终止哨兵，重新订阅 | session=%s", current)
                        break
                    if self.session_id != current:
                        _b_logger.info("[BrokerListener] 会话切换(b) | %s→%s", current, self.session_id)
                        break
                    if self._history_stale(self._session_epoch) and evt.get("type") not in (
                        "subagent_progress", "subagent_done",
                    ):
                        continue
                    if evt.get("type") == "message_received":
                        msg = evt.get("message") or evt.get("content") or ""
                        if msg and str(msg) == getattr(self, "_last_user_msg", None):
                            continue
                        if msg:
                            self._last_user_msg = str(msg)
                    try:
                        await self._handle_event(evt)
                    except Exception as e:
                        _b_logger.error("[BrokerListener] 处理事件失败但不中断 | type=%s | error=%s", evt.get("type"), e)
                    if evt.get("type") in ("done", "error", "stopped"):
                        self._last_local_task_end = time.monotonic()
            except asyncio.CancelledError:
                _b_logger.warning("[BrokerListener] 监听器被取消，退出")
                raise
            except Exception as e:
                import traceback
                _b_logger.error(
                    "[BrokerListener] 消费循环异常，自动重连 | error=%s\n%s",
                    e, traceback.format_exc()
                )
            finally:
                if queue is not None:
                    try:
                        get_message_broker().unsubscribe(current, queue)
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
        self._app_closing = True
        self._converge_runtime()
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
        from app.core.util.session_status import is_session_running
        entries = []
        for meta in metas:
            entry = {
                "sid": meta.session_id,
                "title": str(meta.title or meta.session_id),
                "count": meta.message_count or 0,
                "current": meta.session_id == self.session_id,
                "running": is_session_running(meta.session_id),
                "frame": self._global_blink_counter,
            }
            entries.append(entry)
            try:
                self.session_list.add_option(
                    Option(self._session_prompt(entry), id=meta.session_id)
                )
            except Exception as e:
                # 单条标题异常不应中断整个会话列表
                logger.warning("[TUI] 会话项渲染失败 | %s | %s", meta.session_id, e)
        self._session_list_entries = entries
        # 首次布局前算不到真实宽度，稍后重算一次
        self.set_timer(0.2, self._reload_session_prompts)

    def _session_list_width(self) -> int:
        try:
            w = self.session_list.scrollable_content_region.width
            if w:
                return max(10, w - 1)
        except Exception:
            pass
        for attr in ("content_size", "size"):
            try:
                w = getattr(self.session_list, attr).width
            except Exception:
                w = 0
            if w:
                return max(10, w - 2)
        return 16

    def _reload_session_prompts(self):
        """布局完成后按真实宽度重算，避免首帧过宽换行"""
        entries = getattr(self, "_session_list_entries", None) or []
        for idx, entry in enumerate(entries):
            try:
                self.session_list.replace_option_prompt_at_index(idx, self._session_prompt(entry))
            except Exception:
                pass

    def _schedule_session_prompt_reload(self, delay: float = 0.15):
        timer = getattr(self, "_prompt_reload_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._prompt_reload_timer = self.set_timer(delay, self._reload_session_prompts)

    def _session_prompt(self, entry, frame=None) -> str:
        from app.tui.spinner import running_marker
        from app.tui.widgets import clip_cells, plain_one_line
        from rich.cells import cell_len
        frame = self._global_blink_counter if frame is None else frame
        if entry.get("running"):
            prefix = f"{running_marker(frame)} "
        else:
            prefix = "● " if entry.get("current") else "○ "
        count = entry.get("count") or 0
        # 去 ANSI/控制字符并压成单行；截断后无条件转义方括号，避免 markup 崩溃
        raw = plain_one_line(entry.get("title") or "")
        suffix = f"  ({count})" if count else ""
        avail = max(4, self._session_list_width() - 3 - cell_len(suffix))
        title = clip_cells(raw, avail).replace("[", "\\[")
        if count:
            title += f"  [dim]({count})[/dim]"
        return prefix + title

    def _refresh_session_running(self):
        """闪烁点：只更新状态有变化的选项，避免整表重建丢选中"""
        entries = getattr(self, "_session_list_entries", None) or []
        if not entries:
            return
        try:
            from app.core.util.session_status import is_session_running
            lst = self.session_list
        except Exception:
            return
        frame = self._global_blink_counter
        for idx, entry in enumerate(entries):
            running = is_session_running(entry["sid"])
            if running != entry.get("running") or (running and entry.get("frame") != frame):
                entry["running"] = running
                entry["frame"] = frame
                try:
                    lst.replace_option_prompt_at_index(idx, self._session_prompt(entry, frame))
                except Exception:
                    pass

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
        try:
            from app.agent.loop.session_store import get_session_store
            get_session_store().set_client_last_session(session_id, "tui")
        except Exception:
            pass
        self._current_thinking = None
        self._current_reasoning = None
        self._current_response = None
        self._current_md = ""
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        self._pending_text = ""
        self._tool_cards = {}
        self._subagent_cards = {}
        self._tool_count = 0
        self.model_name = self._current_model_name()
        self._apply_store_mode()
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
            store.set_client_last_session(self.session_id, "tui")
            self.chat.remove_children()
            self.model_name = self._current_model_name()
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
            try:
                from app.core.util.undo_store import get_undo_store
                get_undo_store().drop_session(self.session_id)
            except Exception:
                pass
            await self._append_system(self.tr("session.deleted", self.session_id))
            self._session_epoch += 1
            self.session_id = "default"
            try:
                store.set_client_last_session("default", "tui")
            except Exception:
                pass
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
            try:
                from app.core.util.undo_store import get_undo_store
                get_undo_store().drop_session(target)
            except Exception:
                pass
            await self._append_system(self.tr("session.deleted", target))
            if self.session_id == target:
                self.session_id = "default"
                try:
                    store.set_client_last_session("default", "tui")
                except Exception:
                    pass
                self.chat.remove_children()
                self._refresh_status()
                await self._load_sessions()
                await self._load_history()
            else:
                await self._load_sessions()
        except Exception as e:
            await self._append_system(self.tr("session.delete_failed", e))

    def _request_delete(self, target: str = None):
        """删除前弹出确认"""
        target = (target or self.session_id).strip()
        if target in ("default", "tui"):
            self._post(self._append_system(self.tr("session.protected")))
            return
        from app.tui.session_picker import DeleteConfirmScreen
        self.push_screen(
            DeleteConfirmScreen(
                self.tr("session.delete_confirm", target),
                self.tr("common.delete"),
                self.tr("common.cancel"),
            ),
            callback=lambda ok: self._post(
                self._delete_session_by_arg(f"delete {target}")
            ) if ok else None,
        )

    # ---- 事件处理 ----

    def _submit_input(self):
        if self.input is None:
            return
        text = self.input.value.strip()
        self.input.value = ""
        self._palette_hide()
        self._file_panel_hide()
        if not text:
            return
        pending = getattr(self.input, "_paste_pending", None)
        if pending:
            marker = f"[Pasted ~{pending.count(chr(10)) + 1} lines]"
            if marker in text:
                text = text.replace(marker, pending)
            self.input._paste_pending = None
            self.input._paste_marker_end = None
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
        self._refresh_file_panel(value)

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

    # ---- @ 文件/文件夹选择 ----

    def _file_panel_base(self):
        try:
            from app.core.di.container import get_container
            from app.agent.shell.cellium_shell import CelliumShell
            container = get_container()
            if container.has(CelliumShell):
                shell = container.resolve(CelliumShell)
                cwd = getattr(shell, "_cwd", None)
                if cwd and os.path.isdir(cwd):
                    return os.path.abspath(cwd)
        except Exception:
            pass
        try:
            from app.core.util.agent_config import get_config
            from app.core.util.workdir import get_workdir
        except Exception:
            return os.path.abspath(os.getcwd())
        try:
            agent = get_config().get_section("agent") or {}
            if agent.get("shell_cwd"):
                return get_workdir()
        except Exception:
            pass
        return os.path.abspath(get_workdir())

    def _file_panel_token_pos(self, value):
        idx = -1
        for i in range(len(value)):
            if value[i] == "@" and (i == 0 or value[i - 1].isspace()):
                idx = i
        return idx

    def _file_panel_token(self, value):
        idx = self._file_panel_token_pos(value)
        if idx < 0:
            return None
        tok = ""
        for ch in value[idx + 1:]:
            if ch.isspace():
                break
            tok += ch
        return tok

    def _replace_file_token(self, replace, keep_at=True):
        value = self.input.value
        idx = self._file_panel_token_pos(value)
        if idx < 0:
            return
        j = idx + 1
        while j < len(value) and not value[j].isspace():
            j += 1
        if keep_at:
            # 保留 @：@rel
            new_value = value[:idx + 1] + replace + value[j:]
        else:
            # 去掉 @：直接拼路径
            new_value = value[:idx] + replace + value[j:]
        self.input.value = new_value
        self.input.cursor_position = len(self.input.value)

    def _refresh_file_panel(self, value):
        token = self._file_panel_token(value)
        if token is None:
            self._file_panel_hide()
            return
        base = self._file_panel_base()
        if not token:
            listing_dir, prefix = base, ""
        elif token.endswith("/") or token.endswith("\\"):
            d = os.path.join(base, token.rstrip("/\\"))
            if os.path.isdir(d):
                listing_dir, prefix = d, ""
            else:
                self._file_panel_hide()
                return
        else:
            parts = [p for p in token.replace("\\", "/").split("/") if p]
            candidate = os.path.join(base, *parts)
            if os.path.isdir(candidate):
                listing_dir, prefix = candidate, ""
            else:
                listing_dir = os.path.join(base, *parts[:-1])
                prefix = parts[-1] if parts else ""
        self._file_panel_dir = listing_dir
        self._show_file_panel(listing_dir, prefix)

    def _show_file_panel(self, directory, prefix):
        try:
            names = sorted(os.listdir(directory), key=str.lower)
        except Exception:
            self._file_panel_hide()
            return
        dirs, files = [], []
        for n in names:
            if prefix and not n.lower().startswith(prefix.lower()):
                continue
            if os.path.isdir(os.path.join(directory, n)):
                dirs.append(n)
            else:
                files.append(n)
        entries = [("[dim]..[/]", "..")] if os.path.normpath(directory) != os.path.normpath(self._file_panel_base()) else []
        for d in dirs:
            entries.append((f"[cyan]/[/][bold]{d}[/]", d))
        for f in files:
            entries.append((f"    {f}", f))
        if not entries:
            self._file_panel_hide()
            return
        rel = os.path.relpath(directory, self._file_panel_base()).replace("\\", "/")
        title = f"@  {rel if rel != '.' else '.'}/"
        footer = "Enter 进入/插入 · ← 上级 · Esc 关闭"
        self.file_panel.set_entries(entries, title, footer)
        self.file_panel.styles.display = "block"
        self._file_panel_active = True

    def _file_panel_move(self, delta):
        if not self._file_panel_active:
            return
        try:
            self.file_panel.move(delta)
        except Exception:
            pass

    def _file_panel_accept(self, name=None):
        if not self._file_panel_active:
            return
        if name is None:
            entry = self.file_panel.current()
            if not entry:
                return
            name = entry[1]
        if name == "..":
            self._file_panel_up()
            return
        full = os.path.join(self._file_panel_dir, name)
        if os.path.isdir(full):
            rel = os.path.relpath(full, self._file_panel_base()).replace("\\", "/")
            self._replace_file_token(rel + "/")
            self._refresh_file_panel(self.input.value)
        else:
            rel = os.path.relpath(full, self._file_panel_base()).replace("\\", "/")
            self._replace_file_token(rel + " ", keep_at=False)
            self._file_panel_hide()
            self.input.focus()

    def _file_panel_up(self):
        parent = os.path.dirname(self._file_panel_dir)
        if os.path.normpath(parent) == os.path.normpath(self._file_panel_dir):
            return
        rel = os.path.relpath(parent, self._file_panel_base()).replace("\\", "/")
        if rel == ".":
            rel = ""
        self._replace_file_token(rel + ("/" if rel else ""))
        self._refresh_file_panel(self.input.value)

    def _file_panel_hide(self):
        self._file_panel_active = False
        self._file_panel_dir = ""
        try:
            self.file_panel.styles.display = "none"
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
        elif cmd in ("/undo", "/rewind"):
            self._post(self._rewind_last_round())
        elif cmd == "/theme":
            self.action_toggle_theme()
        elif cmd == "/settings":
            self.action_open_settings()
        elif cmd == "/session":
            if arg:
                if arg.startswith("delete"):
                    self._request_delete(arg[6:].strip() or None)
                else:
                    self._post(self._switch_session(arg))
            else:
                self._post(self._open_session_picker())
        elif cmd == "/delete":
            self._request_delete()
        elif cmd == "/new":
            self.action_new_session()
        elif cmd in ("/model", "/models"):
            if arg:
                self._post(self._handle_model_command(arg))
            else:
                self.action_open_model_picker()
        elif cmd == "/plan":
            self._set_run_mode("plan")
        elif cmd == "/plan-habit":
            self._handle_plan_habit(arg)
        elif cmd == "/run":
            self._set_run_mode("run")
        elif cmd == "/delegate":
            self._set_run_mode("delegate")
        else:
            self._post(self._append_system(self.tr("commands.unknown", cmd)))

    def action_open_model_picker(self):
        from app.tui.model_picker import ModelPickerScreen
        try:
            from app.server import session_llm
            models = session_llm.list_models()
            current = self._current_model_name()
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
            from app.server import session_llm
            if name not in {m["name"] for m in session_llm.list_models()}:
                await self._append_system(self.tr("model.not_found", name))
                return
            # 只切当前会话，不动全局配置（其他会话继续用各自的模型）
            result = session_llm.set_model(self.session_id, name)
            if not result.get("success"):
                await self._append_system(self.tr("model.switch_failed", result.get("error")))
                return
            session_llm.apply_to_loop(self.session_id)
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
        # Esc 仅用于停止运行中的任务，不再用于退出应用（退出用 Ctrl+Q / Ctrl+D）
        if not self._busy:
            self._last_esc = 0.0
            return
        now = time.monotonic()
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
        if self.input is not None and self.input.has_focus:
            try:
                self.input.action_copy()
            except SkipAction:
                pass

    def action_copy_selection(self):
        """Ctrl+Y 复制当前选区"""
        if copy_selection_if_any(self):
            return

    def on_mouse_down(self, event):
        """右键复制选区"""
        if getattr(event, "button", 0) == 3 and copy_selection_if_any(self):
            try:
                event.stop()
            except Exception:
                pass

    def action_toggle_run_mode(self):
        order = ("run", "plan", "delegate")
        idx = order.index(self._run_mode) if self._run_mode in order else 0
        self._set_run_mode(order[(idx + 1) % len(order)])

    def _apply_store_mode(self):
        """按后端会话模式同步本地状态（启动/切会话时，不播放动画）"""
        try:
            from app.server.session_mode import get_mode
            mode = get_mode(self.session_id)
        except Exception:
            mode = self._run_mode
        self._run_mode = mode
        try:
            self.query_one(LogoImage).set_plan(mode == "plan", animate=False)
        except Exception:
            pass

    def _set_run_mode(self, mode, sync=True, animate=True):
        from app.core.security.run_mode import normalize_mode
        from app.server.session_mode import set_mode
        mode = normalize_mode(mode)
        if sync:
            mode = set_mode(self.session_id, mode)
        if mode == self._run_mode:
            return
        self._run_mode = mode
        try:
            self.query_one(LogoImage).set_plan(mode == "plan", animate=animate)
        except Exception:
            pass
        self._refresh_status()

    def _handle_plan_habit(self, arg):
        """查看/设置 Plan 模式自定义习惯（记忆库 + 配置镜像）"""
        from app.core.security.run_mode import get_plan_habit, set_plan_habit
        if not arg:
            habit = get_plan_habit()
            msg = self.tr("plan.habit_current", habit) if habit else self.tr("plan.habit_empty")
            self._post(self._append_system(msg))
            return

        value = "" if arg.strip().lower() in ("reset", "clear", "-") else arg.strip()
        if not set_plan_habit(value):
            self._post(self._append_system(self.tr("plan.habit_failed", "记忆系统未初始化")))
            return
        msg = self.tr("plan.habit_reset") if not value else self.tr("plan.habit_saved")
        self._post(self._append_system(msg))

    def _find_user_msg_id(self, content=None):
        memory = self._get_session_memory(self.session_id)
        text = None if content is None else str(content).strip()
        for m in reversed(memory.messages):
            if m.get("role") != "user" or m.get("_is_compacted_notes") or not m.get("id"):
                continue
            if text is None or str(m.get("content") or "").strip() == text:
                return m["id"]
        return None

    def action_rewind_to(self, message_id=None, content=None):
        if self._busy:
            self._post(self._append_system(self.tr("rewind.busy")))
            return
        if not message_id:
            try:
                message_id = self._find_user_msg_id(content)
            except Exception:
                message_id = None
        if not message_id:
            self._post(self._append_system(self.tr("rewind.none")))
            return
        self.run_worker(self._begin_rewind_confirm(message_id))

    async def _begin_rewind_confirm(self, msg_id):
        try:
            from app.agent.loop.rewind import has_tracking, preview_rewind
            tracked = await asyncio.to_thread(has_tracking, self.session_id, msg_id)
            if not tracked:
                await self._append_system(self.tr("rewind.untracked"))
                return
            preview = await asyncio.to_thread(preview_rewind, self.session_id, msg_id)
        except Exception as e:
            await self._append_system(self.tr("rewind.failed", e))
            return
        from app.tui.session_picker import RewindConfirmScreen, format_rewind_files
        files = list(preview.get("files") or [])
        skipped = list(preview.get("files_skipped") or [])
        files_text = format_rewind_files(
            files, skipped,
            restore_label=self.tr("rewind.files_restored"),
            delete_label=self.tr("rewind.files_deleted"),
            none_label=self.tr("rewind.files_none"),
            more_label=self.tr("rewind.files_more"),
            skipped_label=self.tr("rewind.files_skipped"),
            binary_label=self.tr("rewind.files_binary"),
        )
        self.push_screen(
            RewindConfirmScreen(
                self.tr("rewind.confirm"),
                files,
                skipped,
                self.tr("rewind.hint"),
                files_text,
                self.tr("rewind.confirm_yes"),
                self.tr("common.cancel"),
            ),
            callback=lambda ok: self._post(self._rewind_to(msg_id)) if ok else None,
        )

    async def _rewind_last_round(self):
        if self._busy:
            await self._append_system(self.tr("rewind.busy"))
            return
        try:
            msg_id = await asyncio.to_thread(self._find_user_msg_id)
        except Exception as e:
            await self._append_system(self.tr("rewind.failed", e))
            return
        if not msg_id:
            await self._append_system(self.tr("rewind.none"))
            return
        await self._begin_rewind_confirm(msg_id)

    async def _rewind_to(self, msg_id):
        try:
            from app.agent.loop.rewind import rewind_session
            self._local_rewind = True
            result = await asyncio.to_thread(rewind_session, self.session_id, msg_id)
            await self._refresh_after_rewind()
            removed = int((result or {}).get("removed_count") or 0)
            partial = bool((result or {}).get("warning") or (result or {}).get("files_skipped"))
            await self._append_system(
                self.tr("rewind.partial" if partial else "rewind.done", removed)
            )
            self.set_timer(2.0, self._clear_local_rewind)
        except Exception as e:
            self._local_rewind = False
            await self._append_system(self.tr("rewind.failed", e))

    async def _refresh_after_rewind(self):
        self._last_local_task_end = time.monotonic()
        self._clear_chat()
        await self._load_history()

    def _refresh_tracked_ids(self):
        try:
            from app.agent.loop.rewind import rewindable_user_ids
            self._tracked_ids = rewindable_user_ids(self.session_id)
        except Exception:
            self._tracked_ids = set()
        return self._tracked_ids

    def _apply_rewindable(self, mark_last=False, turn_id=None):
        from app.tui.widgets import UserMessage
        try:
            widgets = list(self.query(UserMessage))
        except Exception:
            return
        for w in widgets:
            w.set_rewindable(bool(w.message_id) and w.message_id in self._tracked_ids)
        if not mark_last or not widgets:
            return
        last = widgets[-1]
        msg_id = turn_id or self._find_user_msg_id(last._content)
        if msg_id and msg_id in self._tracked_ids:
            last.message_id = msg_id
            last.set_rewindable(True)

    async def _sync_rewindable(self, mark_last=False, turn_id=None):
        await asyncio.to_thread(self._refresh_tracked_ids)
        self._apply_rewindable(mark_last, turn_id)

    def _clear_local_rewind(self):
        self._local_rewind = False

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
            from app.server.task_manager import get_task_manager
            task_mgr = get_task_manager()
            queued = task_mgr.enqueue_supplement_message(self.session_id, {
                "content": text,
                "source": "tui",
                "msg_id": None,
                "received_at": time.time(),
                "platform": None,
                "message_type": None,
            })
            if not queued:
                self._busy = False
                self._busy_status = ""
                self._stop_busy_animation()
            else:
                self._post(self._append_user(text, force_scroll=True))
                return
        self._busy = True
        self._busy_status = self.tr("busy")
        self._start_busy_animation()
        self.run_worker(self._agent_worker(text), group="agent", exclusive=True)

    async def _agent_worker(self, text):
        epoch = self._session_epoch
        try:
            await self._append_user(text, force_scroll=True)
            self._last_user_msg = text
            if not self._history_ready.is_set():
                try:
                    await asyncio.wait_for(self._history_ready.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
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

                from app.server.session_mode import get_mode
                started = await task_mgr.start_task(
                    session_id=self.session_id,
                    agent_loop=loop,
                    user_input=text,
                    memory=memory,
                    mode=get_mode(self.session_id),
                )
                if not started:
                    if not task_mgr.enqueue_supplement_message(self.session_id, {
                        "content": text,
                        "source": "tui",
                        "msg_id": None,
                        "received_at": time.time(),
                        "platform": None,
                        "message_type": None,
                    }):
                        await self._append_system(self.tr("error.exec", "任务启动失败"))
                    return
                # 事件统一由常驻订阅监听器（_start_broker_listener）消费渲染，
                # _agent_worker 只负责启动任务并等待其结束。
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
        if t == "mode_changed":
            self._set_run_mode(evt.get("mode"), sync=False)
        elif t == "session_model_changed":
            if evt.get("session_id") in (None, self.session_id):
                self.model_name = evt.get("model") or self.model_name
                self._refresh_status()
        elif t == "status":
            self._refresh_status()
        elif t == "message_received":
            # 对端（WebUI/外部平台）发起的消息：立即显示用户消息
            msg = evt.get("message") or evt.get("content") or ""
            if msg:
                source_session = evt.get("source_session")
                if source_session:
                    await self._append_system(
                        self.tr(
                            "session.delegated_from",
                            evt.get("source_session_title") or source_session,
                        )
                    )
                await self._append_user(str(msg))
        elif t == "messages_rewound":
            if not self._local_rewind and not self._busy:
                await self._refresh_after_rewind()
        elif t == "thinking":
            self._touch_busy()
            await self._append_thinking(evt.get("content", ""))
        elif t == "thinking_start":
            self._touch_busy()
            if self._current_thinking is None:
                self._current_thinking = ThinkingBlock("")
                await self.chat.mount(self._current_thinking)
        elif t == "thinking_streaming":
            self._touch_busy()
            await self._stream_thinking(
                evt.get("content", ""),
                final=bool(evt.get("final", False)),
            )
        elif t == "reasoning":
            self._touch_busy()
            if self._current_response is not None:
                return
            if self._current_reasoning is None:
                self._current_reasoning = ReasoningBlock(start_time=evt.get("start_time", 0))
                await self.chat.mount(self._current_reasoning)
            self._current_reasoning.append_text(evt.get("content", ""))
            self.chat.scroll_to_follow()
        elif t in ("content_chunk", "content"):
            self._touch_busy()
            if self._current_reasoning is not None:
                self._current_reasoning.finish()
                self._current_reasoning = None
            await self._append_chunk(evt.get("content", ""))
        elif t == "tool_call_pending":
            self._touch_busy()
            # 触发工具调用即 reasoning 已结束，别把参数生成时间算进思考耗时
            if self._current_reasoning is not None:
                self._current_reasoning.finish()
                self._current_reasoning = None
            await self._add_tool_card({
                "tool": evt.get("tool", ""),
                "arguments": {},
                "description": evt.get("description", ""),
                "call_id": evt.get("call_id"),
            })
        elif t == "tool_start":
            self._touch_busy()
            if self._current_reasoning is not None:
                self._current_reasoning.finish()
                self._current_reasoning = None
            await self._add_tool_card(evt)
        elif t == "tool_result":
            self._touch_busy()
            self._update_tool_card(evt)
        elif t == "subagent_progress":
            if evt.get("kind") == "session":
                self._update_delegation_bar(evt)
            else:
                await self._update_subagent_card(evt)
        elif t == "subagent_done":
            if evt.get("kind") == "session":
                self._update_delegation_bar(evt)
            else:
                await self._finish_subagent_card(evt)
        elif t == "done":
            await self._finalize_render()
            self._finish_response()
            self._refresh_status()
            await self._sync_rewindable(mark_last=True, turn_id=evt.get("tracked_turn_id"))
        elif t in ("stopped", "control_loop_stop", "heuristic_stop"):
            await self._finalize_render()
            self._finish_response()
            await self._sync_rewindable(mark_last=True, turn_id=evt.get("tracked_turn_id"))
        elif t == "error":
            await self._append_system(self.tr("error", evt.get("error", "")))
        elif t == "heuristic_redirect":
            await self._append_system(self.tr("redirect"))
        elif t == "hybrid_phase":
            pass
        elif t == "supplement_injected":
            content = evt.get("content", "")
            notice = evt.get("notice")
            if self._current_response is not None:
                self._current_response = None
                self._current_md = ""
                self._md_buffer.reset()
            if isinstance(notice, dict) and notice.get("type") in ("delegation", "subagent"):
                await self.chat.mount(DelegationNotice(notice))
                self.chat.scroll_to_follow()
            elif content:
                preview = content if len(content) <= 60 else content[:60] + "…"
                await self._append_system(self.tr("supplement.injected", preview))
                await self._append_system(self.tr("supplement.continue"))

    # ---- 渲染辅助 ----

    async def _append_user(self, content, force_scroll=False):
        from app.tui.history_render import is_delegation_notice
        if is_delegation_notice(str(content)):
            await self.chat.mount(DelegationNotice(str(content)))
        else:
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

    async def _stream_thinking(self, content, final=False):
        """thinking_streaming：协议 reasoning 流式事件。

        final=False: 流式早期占位，覆写 buf；
        final=True:  闭合最终值，重置 buf 后写入避免重复累加。
        """
        if not content:
            return
        if self._current_thinking is None:
            self._current_thinking = ThinkingBlock("")
            await self.chat.mount(self._current_thinking)
        if final:
            self._current_thinking._buf = ""
        self._current_thinking._buf = content
        self._current_thinking._refresh()
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
        if not text:
            return
        if self._current_response is None:
            if self._active_tool_card is not None:
                self._active_tool_card.finish()
                self._active_tool_card = None
            if self._current_thinking is not None:
                self._current_thinking.finish()
                self._current_thinking = None
            if self._current_reasoning is not None:
                self._current_reasoning.finish()
                self._current_reasoning = None
            text = text.lstrip()
            if not text:
                return
            self._current_response = AssistantMessage("")
            await self.chat.mount(self._current_response)
        self._md_buffer.append(text)
        self._current_md = self._md_buffer.get_content()
        await self._schedule_md_render()

    async def _schedule_md_render(self):
        if self._is_user_selecting():
            self._pending_render_after_select = True
            return
        if getattr(self, "_md_render_timer", None) is None:
            n = len(self._current_md or "")
            delay = 0.05 if n < 1500 else (0.08 if n < 5000 else 0.12)
            self._md_render_timer = self.set_timer(delay, self._do_md_render)

    def _is_user_selecting(self) -> bool:
        try:
            selections = getattr(self.screen, "selections", None)
            if not selections:
                return False
            for sel in selections.values():
                if sel is None:
                    continue
                try:
                    start, end = sel
                except (TypeError, ValueError):
                    continue
                if start is not None and end is not None and start != end:
                    return True
            return False
        except Exception:
            return False

    def on_mouse_up(self, event) -> None:
        try:
            if getattr(self, "_pending_render_after_select", False):
                self._pending_render_after_select = False
                old = getattr(self, "_md_render_timer", None)
                if old is not None:
                    try:
                        old.stop()
                    except Exception:
                        pass
                self._md_render_timer = self.set_timer(0.05, self._do_md_render)
        except Exception:
            pass

    def _do_md_render(self):
        self._md_render_timer = None
        resp = self._current_response
        if resp is None or not self._md_buffer.has_changed():
            return
        try:
            import asyncio
            asyncio.ensure_future(self._render_now(force_layout=True))
            self._md_buffer.mark_rendered()
        except Exception as e:
            logging.warning(f"Failed to schedule markdown render: {e}")

    async def _render_now(self, force_layout=False):
        try:
            resp = self._current_response
            md = self._current_md
            if resp is not None and md:
                await resp.update(md, layout=force_layout)
                self.chat.scroll_to_follow()
        except Exception as e:
            logging.warning(f"Failed to render markdown: {e}")

    async def _flush_pending_text(self):
        if not self._pending_text:
            return
        if self._is_thought_prefix(self._pending_text):
            return 
        await self._append_text(self._pending_text)
        self._pending_text = ""

    def _is_thought_prefix(self, text: str) -> bool:
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
        stripped = text.lstrip()
        offset = len(text) - len(stripped)

        m = re.match(r'```json\s*\n?\s*(\{\s*"reasoning)', stripped, re.IGNORECASE)
        if m:
            return offset + m.start(1)

        if m := re.match(r'\{\s*"reasoning"\s*:', stripped):
            return offset + m.start()

        return None

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
        if self._current_response is not None and self._current_md:
            await self._render_now()
        if self._current_response is not None:
            self._current_response = None
            self._current_md = ""
            self._md_buffer.reset()
            self._pending_text = ""
        if self._thought_mode:
            self._thought_mode = False
            self._thought_raw = ""
            self._thought_fenced = False
        if self._current_thinking is not None:
            self._current_thinking.finish()
            self._current_thinking = None
        if evt.get("gene"):
            return
        call_id = evt.get("call_id") or f"tc_{len(self._tool_cards)}"
        same_round = self._active_tool_card is not None and not any(
            c["status"] in ("success", "error") for c in self._active_tool_card._calls.values()
        )
        if self._active_tool_card is not None and not same_round:
            self._active_tool_card.finish()
        if self._active_tool_card is None or not same_round:
            self._active_tool_card = ToolCallCard()
            await self.chat.mount(self._active_tool_card)
        tool_name = evt.get("tool", "")
        arguments = evt.get("arguments", {})
        description = evt.get("description", "")
        if tool_name == "sub_agent" and isinstance(arguments, dict):
            tasks = arguments.get("tasks") if isinstance(arguments.get("tasks"), list) else []
            names = [
                str(item.get("name") or item.get("task") or "?")[:24]
                for item in tasks if isinstance(item, dict)
            ]
            description = description or (
                f"{arguments.get('command', 'parallel')} · {len(tasks)} 个子 Agent"
                + (f": {', '.join(names)}" if names else "")
            )
            arguments = {"command": arguments.get("command", ""), "tasks": names}
        self._active_tool_card.add_call(
            call_id,
            tool=tool_name,
            description=description,
            arguments=arguments,
        )
        is_new = call_id not in self._tool_cards
        self._tool_cards[call_id] = self._active_tool_card
        if is_new:
            self._tool_count += 1
        self.chat.scroll_to_follow()

    def _update_tool_card(self, evt):
        card = self._tool_cards.get(evt.get("call_id", ""))
        if card:
            card.update_call(evt.get("call_id", ""), evt.get("result"), evt.get("duration_ms", 0))

    @staticmethod
    def _subagent_label(evt):
        if evt.get("kind") == "session":
            status_cn = {
                "queued": "排队中", "running": "执行中", "done": "完成",
                "error": "失败", "timeout": "超时", "cancelled": "已取消",
            }
            target = str(evt.get("target_title") or evt.get("target") or evt.get("name") or "?")
            raw_status = str(evt.get("status") or "running")
            parts = [f"会话 {target}", status_cn.get(raw_status, raw_status)]
            if evt.get("purpose"):
                parts.append(str(evt["purpose"])[:40])
        else:
            parts = [str(evt.get("name") or "subagent"), str(evt.get("status") or "running")]
        tool = evt.get("current_tool") or evt.get("last_tool")
        used = [t for t in (evt.get("tools_used") or []) if t]
        if tool:
            parts.append(f"工具 {tool}")
        elif used:
            parts.append(f"工具 {', '.join(used)}")
        if evt.get("tool_calls"):
            parts.append(f"第 {evt['tool_calls']} 次")
        elapsed = evt.get("elapsed_s")
        if not elapsed:
            elapsed = evt.get("duration_s")
        if elapsed:
            parts.append(f"{float(elapsed):.0f}s")
        preview = evt.get("last_result_preview")
        if preview:
            parts.append(str(preview)[:80])
        if evt.get("error"):
            parts.append(str(evt["error"])[:60])
        return " · ".join(parts)

    _DELEGATION_FIELDS = (
        "target", "target_title", "purpose", "status", "current_tool",
        "last_tool", "last_reasoning", "tool_calls", "elapsed_s", "error", "success",
    )
    _DELEGATION_TERMINAL = ("done", "error", "timeout", "cancelled", "unknown")

    def _update_delegation_bar(self, evt):
        key = str(evt.get("task_id") or f"session:{evt.get('target') or evt.get('name') or ''}")
        info = self._delegation_state.setdefault(key, {"task_id": key})
        for field in self._DELEGATION_FIELDS:
            if evt.get(field) is not None:
                info[field] = evt.get(field)
        info["updated_at"] = time.time()
        status = info.get("status") or "running"
        if status in self._DELEGATION_TERMINAL:
            old = self._delegation_timers.pop(key, None)
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    pass
            delay = 2.5 if status == "done" else 4.0
            self._delegation_timers[key] = self.set_timer(
                delay, lambda k=key: self._remove_delegation(k)
            )
        self._render_delegation_bar()

    def _remove_delegation(self, key):
        self._delegation_timers.pop(key, None)
        self._delegation_state.pop(key, None)
        self._render_delegation_bar()

    def _reset_delegations(self):
        for timer in getattr(self, "_delegation_timers", {}).values():
            try:
                timer.stop()
            except Exception:
                pass
        self._delegation_timers = {}
        self._delegation_state = {}
        self._delegation_text = ""
        try:
            self.delegation_bar.update("")
            self.delegation_bar.remove_class("visible")
        except Exception:
            pass

    def _render_delegation_bar(self):
        bar = getattr(self, "delegation_bar", None)
        if bar is None:
            return
        state = list(self._delegation_state.values())
        if not state:
            self._delegation_text = ""
            bar.update("")
            bar.remove_class("visible")
            return
        bar.add_class("visible")
        from rich.markup import escape
        from app.tui.spinner import DOTS_FRAMES
        frame = DOTS_FRAMES[self._delegation_frame % len(DOTS_FRAMES)]
        lines = []
        visible = state[-3:]
        hidden = len(state) - len(visible)
        for info in visible:
            status = info.get("status") or "running"
            name = info.get("target_title") or info.get("target") or "?"
            if status in self._DELEGATION_TERMINAL:
                if status == "done":
                    prefix = "[green]✓[/]"
                    label = self.tr("delegation.done", name)
                else:
                    prefix = "[red]✗[/]"
                    label = self.tr("delegation.failed", name)
            else:
                prefix = f"[bold cyan]{frame}[/]"
                label = self.tr(
                    "delegation.running" if status == "running" else "delegation.queued", name
                )
            parts = [f"{prefix} {escape(str(label))}"]
            tool = info.get("current_tool") or info.get("last_tool")
            if tool:
                parts.append(f"[dim]{escape(self.tr('delegation.tool', tool))}[/]")
            if info.get("tool_calls"):
                parts.append(f"[dim]{escape(self.tr('delegation.attempt', info['tool_calls']))}[/]")
            if info.get("elapsed_s"):
                parts.append(f"[dim]{int(float(info['elapsed_s']))}s[/]")
            reasoning = info.get("last_reasoning")
            if reasoning:
                label = self.tr("delegation.reasoning")
                parts.append(f"[dim]{escape(label)}: {escape(str(reasoning)[:40])}[/]")
            if status in self._DELEGATION_TERMINAL and info.get("error"):
                parts.append(f"[red]{escape(str(info['error'])[:60])}[/]")
            lines.append(" · ".join(parts))
        if hidden > 0:
            lines.append(f"[dim]{escape(self.tr('delegation.more', hidden))}[/]")
        self._delegation_text = "\n".join(lines)
        bar.update(self._delegation_text)

    async def _update_subagent_card(self, evt):
        is_session = evt.get("kind") == "session"
        name = str(evt.get("name") or "subagent")
        target = str(evt.get("target") or name)
        key = f"session:{target}" if is_session else f"subagent:{name}"
        desc = self._subagent_label(evt)
        card = self._subagent_cards.get(key)
        if card is None:
            card = ToolCallCard()
            await self.chat.mount(card)
            if is_session:
                card.add_call(key, tool="session_agent", description=desc,
                              arguments={"target": target, "purpose": evt.get("purpose")})
            else:
                card.add_call(key, tool="sub_agent", description=desc,
                              arguments={"name": name, "task": evt.get("task", "")})
            self._subagent_cards[key] = card
        else:
            card.set_call_description(key, desc)
        self.chat.scroll_to_follow()

    async def _finish_subagent_card(self, evt):
        is_session = evt.get("kind") == "session"
        name = str(evt.get("name") or "subagent")
        target = str(evt.get("target") or name)
        key = f"session:{target}" if is_session else f"subagent:{name}"
        desc = self._subagent_label(evt)
        card = self._subagent_cards.pop(key, None)
        if card is None:
            card = ToolCallCard()
            await self.chat.mount(card)
            if is_session:
                card.add_call(key, tool="session_agent", description=desc,
                              arguments={"target": target, "purpose": evt.get("purpose")})
            else:
                card.add_call(key, tool="sub_agent", description=desc, arguments={"name": name})
        else:
            card.set_call_description(key, desc)
        card.update_call(
            key,
            {"success": evt.get("success", True), "error": evt.get("error"),
             "content": evt.get("content")},
            int(float(evt.get("duration_s") or 0) * 1000),
        )
        card.finish()
        self.chat.scroll_to_follow()

    def _sync_subagent_cards(self):
        """兜底：注册表已结束但卡片仍显示 running 时自动收尾"""
        if not self._subagent_cards:
            return
        try:
            from components import sub_agent as sa
        except Exception:
            return
        for key in list(self._subagent_cards.keys()):
            # session 委托不在 SubAgent 注册表中，跳过注册表兜底
            if not key.startswith("subagent:"):
                continue
            name = key.split("subagent:", 1)[-1]
            info = sa._SUBAGENTS.get(sa._sub_key(self.session_id, name)) or {}
            status = info.get("status")
            if info and status not in ("done", "error", "cancelled"):
                continue
            card = self._subagent_cards.pop(key, None)
            if card is None:
                continue
            final_status = status if status in ("done", "error", "cancelled") else "done"
            content = info.get("result_content", "") or ""
            try:
                card.set_call_description(key, self._subagent_label({
                    "name": name, "status": final_status,
                    "last_tool": info.get("last_tool"),
                    "tool_calls": info.get("tool_calls", 0),
                    "tools_used": info.get("tools_used", []),
                    "elapsed_s": sa._elapsed_seconds(info),
                    "error": info.get("error"),
                }))
                card.update_call(
                    key,
                    {"success": final_status == "done", "content": content,
                     "error": None if final_status == "done" else final_status},
                    0,
                )
                card.finish()
            except Exception:
                pass

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
                try:
                    await self._current_response.update(self._current_md, layout=True)
                except Exception:
                    pass
            try:
                self.chat.scroll_to_follow()
            except Exception:
                pass
            await self._enforce_chat_cap()
        except Exception:
            pass

    async def _enforce_chat_cap(self, cap: int = 200):
        """长会话限流：移除最旧的聊天控件，避免布局随对话轮次线性变慢"""
        try:
            if not getattr(self.chat, "_following", True):
                return
            if len(self.chat.children) <= cap:
                return
            children = list(self.chat.children)
            excess = len(children) - cap
            if excess <= 0:
                return
            protected = {
                self._current_response, self._current_thinking,
                self._current_reasoning, self._active_tool_card,
            }
            old = [w for w in children[:excess] if w not in protected]
            if old:
                await self.chat.remove_children(old)
                self._history_specs = None
            live = set(self.chat.children)
            for cid, card in list(self._tool_cards.items()):
                if card not in live:
                    self._tool_cards.pop(cid, None)
        except Exception:
            pass

    def _finish_response(self):
        if self._current_thinking is not None:
            self._current_thinking.finish()
        if self._current_reasoning is not None:
            self._current_reasoning.finish()
        self._current_thinking = None
        self._current_reasoning = None
        self._current_response = None
        self._current_md = ""
        self._md_buffer.reset()
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
        self._current_reasoning = None
        self._current_response = None
        self._current_md = ""
        self._md_buffer.reset()
        self._thought_mode = False
        self._thought_raw = ""
        self._thought_fenced = False
        self._pending_text = ""
        self._tool_cards = {}
        self._subagent_cards = {}
        self._active_tool_card = None
        self._restoring_history = False
        self._history_specs = None
        self._reset_delegations()

    def _history_stale(self, epoch: int) -> bool:
        return epoch != self._session_epoch

    def _build_mds(self, specs):
        from app.tui.widgets import _build_rich_md
        mds = []
        for spec in specs:
            if spec.get("kind") == "assistant" and spec.get("content"):
                try:
                    mds.append(_build_rich_md(spec["content"]))
                except Exception:
                    mds.append(None)
            else:
                mds.append(None)
        return mds

    def _widget_from_spec(self, spec, md=None):
        widget = self._build_spec_widget(spec, md)
        try:
            widget.hist_span = spec.get("span")
        except Exception:
            pass
        return widget

    def _build_spec_widget(self, spec, md=None):
        kind = spec["kind"]
        if kind == "user":
            return UserMessage(
                spec["content"],
                spec.get("id"),
                bool(spec.get("id")) and spec.get("id") in self._tracked_ids,
            )
        if kind == "notice":
            return DelegationNotice(spec.get("content", ""))
        if kind == "thinking":
            return ThinkingBlock(spec["content"])
        if kind == "reasoning":
            return ReasoningBlock(
                duration_ms=spec.get("duration_ms", 0),
                reasoning_text=spec.get("content", ""),
            )
        if kind == "assistant":
            from app.tui.widgets import HistoryMarkdown
            w = HistoryMarkdown("")
            w.set_rendered(spec.get("content", "") or "", md)
            return w
        if kind == "tool":
            card = ToolCallCard()
            for idx, call in enumerate(spec.get("calls", [])):
                cid = f"hist_{self._tool_count}_{idx}"
                card.add_call(cid, tool=call["tool"], arguments=call.get("arguments", {}))
                card.update_call(cid, call.get("result"), call.get("duration_ms", 0))
                self._tool_count += 1
            return card
        return ToolCallCard()

    def _post_mount(self, widget, spec):
        if isinstance(widget, ToolCallCard):
            widget.finish()
        elif isinstance(widget, ThinkingBlock):
            widget.finish()
        elif isinstance(widget, ReasoningBlock):
            widget.finish()

    async def _append_history_tail(self, plan, meta, was_following, prev_scroll_y, epoch=None) -> bool:
        """已挂载历史与新计划的公共前缀一致时，只替换尾部，避免整表重建"""
        prev = getattr(self, "_history_specs", None)
        if getattr(self, "_history_specs_sid", None) != self.session_id:
            return False
        if not prev or len(prev) > len(plan):
            return False
        children = list(self.chat.children)
        if len(children) < len(prev):
            return False
        shift = None
        if plan[:len(prev)] == prev:
            shift = 0
        else:
            starts = [s.get("span", (None,))[0] for s in prev]
            first = plan[0].get("span", (None,))[0]
            if first in starts:
                idx = starts.index(first)
                if len(prev) - idx <= len(plan) and plan[:len(prev) - idx] == prev[idx:]:
                    shift = idx
        if shift is None:
            return False
        keep_from = len(prev) - shift
        drop = children[len(prev):] if shift == 0 else (children[:shift] + children[len(prev):])
        new_specs = plan[keep_from:]
        mds = await asyncio.get_running_loop().run_in_executor(None, self._build_mds, new_specs)
        widgets = [self._widget_from_spec(s, md) for s, md in zip(new_specs, mds)]
        if epoch is not None and self._history_stale(epoch):
            return False
        try:
            with self.batch_update():
                if drop:
                    await self.chat.remove_children(drop)
                if widgets:
                    await self.chat.mount(*widgets)
                    for w, s in zip(widgets, new_specs):
                        self._post_mount(w, s)
            live = set(self.chat.children)
            for cid, card in list(self._tool_cards.items()):
                if card not in live:
                    self._tool_cards.pop(cid, None)
        except Exception:
            return False
        self._history_specs = list(plan)
        self._history_specs_sid = self.session_id
        self._history_meta = meta
        self._history_has_more = meta.get("has_more", False)
        self._history_live_start = int(meta.get("end") or 0)
        if widgets:
            try:
                self.chat.refresh(layout=True)
            except Exception:
                pass
            for _ in range(2):
                await asyncio.sleep(0)
        if was_following:
            self.chat.force_scroll_bottom()
        return True

    async def _load_history(self):
        epoch = self._session_epoch
        await asyncio.to_thread(self._refresh_tracked_ids)
        self._history_rev = await asyncio.to_thread(self._history_revision)
        self._history_ready.clear()
        self._restoring_history = True
        self._history_offset = 0
        self._history_has_more = False
        self._history_meta = None
        self._backfill_pending = False
        self._history_live_start = 0
        was_following = self.chat.following
        prev_scroll_y = self.chat.scroll_y
        try:
            hist_limit = self._history_limit

            def _build_plan():
                from app.tui.history_range import build_history_plan_range
                return build_history_plan_range(self.session_id, None, hist_limit)

            plan, meta = await asyncio.get_running_loop().run_in_executor(
                None, _build_plan
            )
            if self._history_stale(epoch):
                return
            if plan and await self._append_history_tail(plan, meta, was_following, prev_scroll_y, epoch):
                self.set_timer(0.15, self._scroll_history_done)
                self._restoring_history = False
                self._history_ready.set()
                self._bootstrap_ready.set()
                self._set_terminal_title()
                if not self._busy:
                    self.input.focus()
                return
            with self.batch_update():
                await self.chat.remove_children()
            self._history_meta = meta
            self._history_has_more = meta.get("has_more", False)
            self._history_live_start = int(meta.get("end") or 0)
            if plan:
                mds = await asyncio.get_running_loop().run_in_executor(None, self._build_mds, plan)
                if self._history_stale(epoch):
                    return
                widgets = [self._widget_from_spec(s, md) for s, md in zip(plan, mds)]
                if self._history_stale(epoch):
                    return
                _batch = 20
                for _i in range(0, len(widgets), _batch):
                    _wc = widgets[_i:_i + _batch]
                    _sc = plan[_i:_i + _batch]
                    with self.batch_update():
                        await self.chat.mount(*_wc)
                        for w, s in zip(_wc, _sc):
                            self._post_mount(w, s)
                    await asyncio.sleep(0)
                    if self._history_stale(epoch):
                        return
                self._history_specs = list(plan)
                self._history_specs_sid = self.session_id
                try:
                    self.chat.refresh(layout=True)
                except Exception:
                    pass
                for _ in range(4):
                    await asyncio.sleep(0)
                if was_following:
                    self.chat.force_scroll_bottom()
                self.set_timer(0.15, self._scroll_history_done)
                self._restoring_history = False
                self._history_ready.set()
                self._bootstrap_ready.set()
                self._set_terminal_title()
                if not self._busy:
                    self.input.focus()
                return
            self._history_specs = []
            self._history_specs_sid = self.session_id
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

    def _on_chat_following_restored(self):
        if not self._backfill_pending or self._history_loading_more:
            return
        if self._restoring_history or self._busy:
            return
        try:
            self._post(self._backfill_tail_history())
        except Exception:
            pass

    async def _backfill_tail_history(self):
        if self._backfill_pending is False:
            return
        epoch = self._session_epoch
        try:
            from app.tui.history_range import plan_slice, resolve_anchor_index
            from app.tui.history_render import load_session_messages
            messages = await asyncio.get_running_loop().run_in_executor(
                None, lambda: load_session_messages(self.session_id, limit=10000)
            )
            if self._history_stale(epoch):
                return
            if not messages:
                self._backfill_pending = False
                return
            total = len(messages)
            meta = self._history_meta or {}
            live_start = int(getattr(self, "_history_live_start", 0) or 0)
            end_target = live_start if live_start > 0 else total
            max_end = 0
            for child in self.chat.children:
                span = getattr(child, "hist_span", None)
                if span:
                    max_end = max(max_end, int(span[1]))
            start_index = max(max_end, int(meta.get("end") or 0))
            if start_index >= end_target:
                self._backfill_pending = False
                return
            plan = plan_slice(messages, start_index, end_target)
            if not plan:
                self._backfill_pending = False
                return
            scroll_y = self.chat.scroll_y
            old_max = self.chat.max_scroll_y
            mds = await asyncio.get_running_loop().run_in_executor(None, self._build_mds, plan)
            if self._history_stale(epoch):
                return
            widgets = [self._widget_from_spec(s, md) for s, md in zip(plan, mds)]
            with self.batch_update():
                await self.chat.mount(*widgets)
                for w, s in zip(widgets, plan):
                    self._post_mount(w, s)
            try:
                for _ in range(5):
                    await asyncio.sleep(0)
                added_height = self.chat.max_scroll_y - old_max
                self.chat.scroll_to(y=scroll_y + added_height, animate=False, immediate=True)
            except Exception:
                pass
            from app.tui.history_range import message_identity
            self._history_meta = {
                "start": int(meta.get("start") or start_index),
                "end": end_target,
                "total": total,
                "anchor": message_identity(messages[start_index]) if start_index < total else None,
                "has_more": (int(meta.get("start") or start_index)) > 0,
            }
            self._backfill_pending = False
            await self._trim_history_widgets(total)
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
            from app.tui.history_range import build_history_plan_range, resolve_anchor_index
            from app.tui.history_render import load_session_messages
            limit = self._history_limit
            meta = self._history_meta or {}
            anchor = meta.get("anchor")
            prev_end = int(meta.get("end") or 0)

            def _build_more():
                messages = load_session_messages(self.session_id, limit=10000)
                if not messages:
                    return [], {}, messages, []
                end_index = resolve_anchor_index(messages, anchor, prev_end)
                if end_index <= 0:
                    return [], {}, messages, []
                p, new_meta = build_history_plan_range(
                    self.session_id, end_index, limit, messages=messages
                )
                return p, new_meta, messages, self._build_mds(p)

            plan, new_meta, messages, mds = await asyncio.get_running_loop().run_in_executor(
                None, _build_more
            )
            if self._history_stale(epoch):
                return
            if not plan:
                self._history_has_more = False
                return

            live_total = len(messages)
            scroll_y = self.chat.scroll_y
            old_max = self.chat.max_scroll_y
            first_widget = next(iter(self.chat.children), None)
            widgets = [self._widget_from_spec(s, md) for s, md in zip(plan, mds)]
            with self.batch_update():
                await self.chat.mount(*widgets, before=first_widget)
                for w, s in zip(widgets, plan):
                    self._post_mount(w, s)
            prev_specs = getattr(self, "_history_specs", None)
            self._history_specs = (list(plan) + prev_specs) if prev_specs is not None else None
            self._history_specs_sid = self.session_id
            try:
                for _ in range(5):
                    await asyncio.sleep(0)
                new_max = self.chat.max_scroll_y
                added_height = new_max - old_max
                base_scroll = scroll_y + added_height
                self.chat.scroll_to(y=base_scroll, animate=False, immediate=True)
            except Exception:
                pass
            self._history_meta = new_meta
            self._history_offset = await self._trim_history_widgets(new_meta.get("end", 0))
            self._history_has_more = new_meta.get("has_more", False)
            self._backfill_pending = new_meta.get("end", 0) < live_total
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
            self._history_specs = None
            return new_offset
        except Exception:
            return new_offset


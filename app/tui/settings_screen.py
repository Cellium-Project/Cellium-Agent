# -*- coding: utf-8 -*-
import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, Static
from textual.widgets._option_list import Option

class SettingsScreen(Screen):
    BINDINGS = [
        ("escape", "back_to_chat", "返回聊天"),
    ]

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
        color: $foreground;
    }

    #settings-body {
        height: 1fr;
        width: 1fr;
    }

    #settings-navigation {
        width: 24;
        min-width: 20;
        max-width: 30;
        padding: 0 0 0 1;
        background: transparent;
    }

    #settings-nav-list {
        height: 1fr;
        padding: 0 1;
        border: none;
        background: transparent;
    }

    #settings-nav-list:focus {
        border: none;
        background-tint: transparent;
    }

    #settings-content {
        width: 1fr;
        padding: 1 2 1 2;
    }

    #settings-content-scroll {
        height: 1fr;
    }

    .settings-heading {
        height: 2;
        padding: 0 1;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        border-bottom: solid $border;
        margin-bottom: 1;
    }

    .settings-section-title {
        color: $primary;
        text-style: bold;
        margin: 1 0 0 0;
    }

    OptionList {
        height: auto;
        min-height: 2;
        max-height: 6;
        margin: 1 0;
        border: none;
        background: transparent;
    }

    .settings-desc {
        color: $text-muted;
        margin-bottom: 1;
    }

    #settings-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: transparent;
    }
    """

    def __init__(self, current_theme, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_theme = current_theme

    def compose(self) -> ComposeResult:
        with Horizontal(id="settings-body"):
            with Vertical(id="settings-navigation"):
                yield Static("", id="settings-sidebar-title")
                yield OptionList(id="settings-nav-list")
            with Vertical(id="settings-content"):
                yield VerticalScroll(id="settings-content-scroll")
        yield Static("", id="settings-footer")

    def on_mount(self):
        self.nav = self.query_one("#settings-nav-list", OptionList)
        self.content = self.query_one("#settings-content-scroll", VerticalScroll)
        self.content.can_focus = False
        self.query_one("#settings-sidebar-title", Static).update(self.app.tr("settings.title"))
        self.query_one("#settings-footer", Static).update(self.app.tr("settings.footer"))
        self.nav.add_options([Option(self.app.tr(label), id=tab_id) for tab_id, label in [("appearance", "settings.nav.appearance")]])
        self.nav.highlighted = 0
        self.nav.focus()
        asyncio.create_task(self._render_tab())

    def action_back_to_chat(self):
        self.app.pop_screen()

    async def _render_tab(self):
        await self.content.remove_children()
        await asyncio.sleep(0)
        await self.content.mount(Static(self.app.tr("settings.nav.appearance"), classes="settings-heading"))
        self._render_appearance()

    def _render_appearance(self):
        self.content.mount(Static(self.app.tr("settings.theme.title"), classes="settings-section-title"))
        themes = OptionList(id="settings-theme-list")
        self.content.mount(themes)
        themes.add_options([
            Option(f"{'●' if self.app.theme == theme_id else '○'} {self.app.tr('settings.theme.dark' if theme_id == 'cellium-dark' else 'settings.theme.light')}", id=theme_id)
            for theme_id in ("cellium-dark", "cellium-light")
        ])
        for i, option in enumerate(themes._options):
            if option.id == self.app.theme:
                themes.highlighted = i
                break
        else:
            themes.highlighted = 0
        self.content.mount(Static(self.app.tr("settings.theme.desc"), classes="settings-desc"))

        self.content.mount(Static(self.app.tr("settings.language.title"), classes="settings-section-title"))
        from app.tui.i18n import LANGUAGES, LANG_LABELS
        langs = OptionList(id="settings-lang-list")
        self.content.mount(langs)
        langs.add_options([
            Option(f"{'●' if self.app.lang == lang_id else '○'} {LANG_LABELS[lang_id]}", id=lang_id)
            for lang_id in LANGUAGES
        ])
        for i, option in enumerate(langs._options):
            if option.id == self.app.lang:
                langs.highlighted = i
                break
        else:
            langs.highlighted = 0
        self.content.mount(Static(self.app.tr("settings.language.desc"), classes="settings-desc"))

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "settings-theme-list":
            self._switch_theme(event.option.id)
        elif event.option_list.id == "settings-lang-list":
            self._switch_lang(event.option.id)

    def _switch_theme(self, theme):
        self.app.theme = theme
        self.app.save_theme_pref(theme)
        self.app._apply_rich_md_theme()
        self.app.refresh_theme_widgets()
        self._refresh_theme_marks()

    def _switch_lang(self, lang):
        self.app.lang = lang
        self.app.save_lang_pref(lang)
        self.app.apply_language()
        focused = self.focused
        focused_id = getattr(focused, "id", None)
        asyncio.create_task(self._render_tab_keep_focus(focused_id))
    def _refresh_theme_marks(self):
        try:
            themes = self.query_one("#settings-theme-list", OptionList)
        except Exception:
            return
        for i, option in enumerate(themes._options):
            theme_id = option.id
            prefix = "●" if theme_id == self.app.theme else "○"
            label = self.app.tr("settings.theme.dark" if theme_id == "cellium-dark" else "settings.theme.light")
            themes.replace_option_prompt_at_index(i, f"{prefix} {label}")

    async def _render_tab_keep_focus(self, focused_id: str):
        old_highlight = None
        try:
            if focused_id and focused_id.endswith("-list"):
                lst = self.query_one(f"#{focused_id}", OptionList)
                old_highlight = lst.highlighted
        except Exception:
            pass
        # 同步刷新语言相关文案（标题/footer/nav）
        self.query_one("#settings-sidebar-title", Static).update(self.app.tr("settings.title"))
        self.query_one("#settings-footer", Static).update(self.app.tr("settings.footer"))
        self.nav.clear_options()
        self.nav.add_options([Option(self.app.tr(label), id=tab_id) for tab_id, label in [("appearance", "settings.nav.appearance")]])
        self.nav.highlighted = 0
        await self._render_tab()
        if focused_id:
            try:
                lst = self.query_one(f"#{focused_id}", OptionList)
                lst.focus()
                if old_highlight is not None:
                    lst.highlighted = old_highlight
            except Exception:
                pass

# -*- coding: utf-8 -*-
import asyncio
import os
import yaml
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import OptionList, Static, Input, Button
from textual.widgets._option_list import Option
from app.tui.i18n import LANGUAGES, LANG_LABELS
from app.core.util.runtime_paths import resolve_config_dir

class SettingsScreen(Screen):
    BINDINGS = [
        ("escape", "back_to_chat", "返回聊天"),
        ("ctrl+s", "save_channel", "保存配置"),
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

    .channel-card {
        margin: 1 0;
        padding: 1;
        border: solid $border;
        background: $secondary-background;
    }

    .channel-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }

    .channel-field {
        margin: 0 0 1 0;
    }

    .channel-field-label {
        color: $text-muted;
        margin-bottom: 0;
    }

    Input:focus {
        border: tall $primary;
    }

    Button {
        margin: 1 0;
        min-width: 16;
    }

    Button:focus {
        text-style: bold;
        background: $primary;
        color: $background;
        border: tall $primary-lighten-2;
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
        self.query_one("#settings-sidebar-title", Static).update(self.app.tr("settings.title"))
        self.query_one("#settings-footer", Static).update(self.app.tr("settings.footer"))
        self.nav.add_options([
            Option(self.app.tr("settings.nav.channel"), id="channel"),
            Option(self.app.tr("settings.nav.appearance"), id="appearance"),
        ])
        self.nav.highlighted = 0
        self.nav.focus()
        self._rendering = False
        self._render_pending = None
        asyncio.create_task(self._render_tab())

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "settings-nav-list":
            self._schedule_render()

    def _schedule_render(self):
        if self._render_pending is not None:
            self._render_pending.cancel()
        self._render_pending = asyncio.create_task(self._render_tab())

    def action_back_to_chat(self):
        self.app.pop_screen()

    def action_save_channel(self):
        idx = self.nav.highlighted if self.nav.highlighted is not None else 0
        tab_id = self.nav._options[idx].id if idx < len(self.nav._options) else "appearance"
        if tab_id != "channel":
            return
        self._save_channel_settings()

    def _save_channel_settings(self):
        try:
            qq_app_id = self.query_one("#qq-app-id", Input).value.strip()
            qq_app_secret = self.query_one("#qq-app-secret", Input).value.strip()
            tg_token = self.query_one("#tg-bot-token", Input).value.strip()

            config_path = Path(resolve_config_dir()) / "channels.yaml"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                except Exception:
                    data = {}
            else:
                data = {}

            data["channels"] = data.get("channels") or {}

            # QQ Bot 配置
            qq_cfg = data["channels"].get("qq") or {}
            qq_cfg["app_id"] = qq_app_id
            qq_cfg["app_secret"] = qq_app_secret
            if qq_app_id:
                os.environ["QQ_BOT_APP_ID"] = qq_app_id
            else:
                os.environ.pop("QQ_BOT_APP_ID", None)
            if qq_app_secret:
                os.environ["QQ_BOT_APP_SECRET"] = qq_app_secret
            else:
                os.environ.pop("QQ_BOT_APP_SECRET", None)
            data["channels"]["qq"] = qq_cfg

            # Telegram Bot 配置
            tg_cfg = data["channels"].get("telegram") or {}
            tg_cfg["bot_token"] = tg_token
            if tg_token:
                os.environ["TELEGRAM_BOT_TOKEN"] = tg_token
            else:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            data["channels"]["telegram"] = tg_cfg

            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            self.app.notify(self.app.tr("settings.channel.saved"))
        except Exception as e:
            self.app.notify(self.app.tr("settings.channel.save_failed", str(e)), severity="error")

    async def _render_tab(self):
        if getattr(self, "_rendering", False):
            return
        self._rendering = True
        try:
            children = list(self.content.children)
            for child in children:
                try:
                    await child.remove()
                except Exception:
                    pass
            await asyncio.sleep(0.02)

            idx = self.nav.highlighted if self.nav.highlighted is not None else 0
            options = self.nav._options
            if not options:
                return
            tab_id = options[idx].id if idx < len(options) else "appearance"
            if tab_id == "channel":
                await self.content.mount(Static(self.app.tr("settings.nav.channel"), classes="settings-heading"))
                await self._render_channels()
            else:
                await self.content.mount(Static(self.app.tr("settings.nav.appearance"), classes="settings-heading"))
                await self._render_appearance()
        finally:
            self._rendering = False

    async def _render_appearance(self):
        try:
            await self.content.mount(Static(self.app.tr("settings.theme.title"), classes="settings-section-title", id="theme-title"))
            themes = OptionList(id="settings-theme-list")
            await self.content.mount(themes)
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
            await self.content.mount(Static(self.app.tr("settings.theme.desc"), classes="settings-desc", id="theme-desc"))

            await self.content.mount(Static(self.app.tr("settings.language.title"), classes="settings-section-title", id="lang-title"))
            langs = OptionList(id="settings-lang-list")
            await self.content.mount(langs)
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
            await self.content.mount(Static(self.app.tr("settings.language.desc"), classes="settings-desc", id="lang-desc"))
        except Exception as e:
            self.app.notify(f"Render appearance failed: {e}", severity="error")

    def _update_appearance_texts(self):
        try:
            headings = self.content.query(".settings-heading")
            if headings:
                headings.first().update(self.app.tr("settings.nav.appearance"))
            try:
                self.content.query_one("#theme-desc").update(self.app.tr("settings.theme.desc"))
            except Exception:
                pass
            try:
                self.content.query_one("#lang-desc").update(self.app.tr("settings.language.desc"))
            except Exception:
                pass
            themes = self.content.query_one("#settings-theme-list", OptionList)
            for i, option in enumerate(themes._options):
                prefix = "●" if option.id == self.app.theme else "○"
                label = self.app.tr("settings.theme.dark" if option.id == "cellium-dark" else "settings.theme.light")
                themes.replace_option_prompt_at_index(i, f"{prefix} {label}")
            langs = self.content.query_one("#settings-lang-list", OptionList)
            for i, option in enumerate(langs._options):
                prefix = "●" if option.id == self.app.lang else "○"
                label = LANG_LABELS[option.id]
                langs.replace_option_prompt_at_index(i, f"{prefix} {label}")
        except Exception:
            pass

    async def _render_channels(self):
        try:
            config_path = Path(resolve_config_dir()) / "channels.yaml"
            cfg = {}
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                        cfg = data.get("channels", {})
                except Exception:
                    pass

            qq_cfg = cfg.get("qq", {})
            tg_cfg = cfg.get("telegram", {})

            qq_app_id_val = qq_cfg.get("app_id") or os.environ.get("QQ_BOT_APP_ID", "")
            qq_app_secret_val = qq_cfg.get("app_secret") or os.environ.get("QQ_BOT_APP_SECRET", "")
            tg_token_val = tg_cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")

            await self.content.mount(Static(self.app.tr("settings.channel.desc"), classes="settings-desc"))

            await self.content.mount(Static(self.app.tr("settings.channel.qq.title"), classes="channel-title"))
            await self.content.mount(Static(self.app.tr("settings.channel.qq.app_id"), classes="channel-field-label"))
            await self.content.mount(Input(placeholder=self.app.tr("settings.channel.qq.app_id.placeholder"), id="qq-app-id", value=qq_app_id_val))
            await self.content.mount(Static(self.app.tr("settings.channel.qq.app_secret"), classes="channel-field-label"))
            await self.content.mount(Input(placeholder=self.app.tr("settings.channel.qq.app_secret.placeholder"), id="qq-app-secret", password=True, value=qq_app_secret_val))
            await self.content.mount(Static("", classes="settings-desc"))

            await self.content.mount(Static(self.app.tr("settings.channel.telegram.title"), classes="channel-title"))
            await self.content.mount(Static(self.app.tr("settings.channel.telegram.bot_token"), classes="channel-field-label"))
            await self.content.mount(Input(placeholder=self.app.tr("settings.channel.telegram.bot_token.placeholder"), id="tg-bot-token", value=tg_token_val))
            await self.content.mount(Static("", classes="settings-desc"))

            await self.content.mount(Button(self.app.tr("settings.channel.save"), id="save-channel-btn", variant="primary"))
        except Exception as e:
            self.app.notify(f"Render channels failed: {e}", severity="error")

    def on_option_list_option_selected(self, event):
        if event.option_list.id == "settings-theme-list":
            self._switch_theme(event.option.id)
        elif event.option_list.id == "settings-lang-list":
            self._switch_lang(event.option.id)

    def on_button_pressed(self, event):
        if event.button.id == "save-channel-btn":
            self._save_channel_settings()

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
        asyncio.create_task(self._render_tab_keep_focus())
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

    async def _render_tab_keep_focus(self):
        self.query_one("#settings-sidebar-title", Static).update(self.app.tr("settings.title"))
        self.query_one("#settings-footer", Static).update(self.app.tr("settings.footer"))
        idx = self.nav.highlighted if self.nav.highlighted is not None else 0
        self.nav.clear_options()
        self.nav.add_options([
            Option(self.app.tr("settings.nav.channel"), id="channel"),
            Option(self.app.tr("settings.nav.appearance"), id="appearance"),
        ])
        safe_idx = min(idx, len(self.nav._options) - 1) if self.nav._options else 0
        self.nav.highlighted = safe_idx
        if safe_idx >= 0 and safe_idx < len(self.nav._options):
            tab_id = self.nav._options[safe_idx].id
            if tab_id == "appearance":
                self._update_appearance_texts()
            elif tab_id == "channel":
                await self._render_channels()

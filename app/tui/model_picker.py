# -*- coding: utf-8 -*-
import asyncio

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets._option_list import Option

class ModelPickerScreen(ModalScreen):

    CSS = """
    ModelPickerScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #model-picker-panel {
        width: 60;
        max-width: 80%;
        height: auto;
        background: $surface;
    }

    #model-picker-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }

    #model-picker-list {
        height: auto;
        max-height: 18;
        padding: 1 0;
        border: none;
        background: $surface;
    }

    #model-picker-list:focus {
        border: none;
        background-tint: transparent;
    }

    #model-picker-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def __init__(self, models, current, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._models = models
        self._current = current

    def compose(self):
        with Vertical(id="model-picker-panel"):
            yield Static(self.app.tr("model.picker_title"), id="model-picker-title")
            yield OptionList(id="model-picker-list")
            yield Static(self.app.tr("model.picker_footer"), id="model-picker-footer")

    def on_mount(self):
        lst = self.query_one("#model-picker-list", OptionList)
        options = []
        for m in self._models:
            name = m.get("name", "")
            mark = "●" if name == self._current else "○"
            desc = f"{m.get('model', '') or '-'} @ {m.get('base_url', '') or '-'}"
            options.append(Option(f"{mark} {name}  [{desc}]", id=name))
        options.append(Option(self.app.tr("settings.add_model"), id="__add__"))
        lst.add_options(options)
        for i, m in enumerate(self._models):
            if m.get("name") == self._current:
                lst.highlighted = i
                break
        lst.focus()

    def _selected_model(self):
        lst = self.query_one("#model-picker-list", OptionList)
        opt = lst.highlighted_option
        if opt is None:
            return None
        return opt.id

    def on_option_list_option_selected(self, event):
        if event.option.id == "__add__":
            self.app.push_screen(ProviderPickerScreen())
            return
        name = event.option.id
        if name == self._current:
            self.app.pop_screen()
            return
        self.dismiss(name)

    def key_e(self):
        name = self._selected_model()
        if not name or name == "__add__":
            self.app.notify(self.app.tr("model.not_found", name or ""), severity="warning")
            return
        model = next((m for m in self._models if m.get("name") == name), None)
        if model is None:
            return
        self.app.push_screen(ModelAddScreen(initial=model))

    def key_d(self):
        name = self._selected_model()
        if not name or name == "__add__":
            return
        if name == self._current:
            self.app.notify(self.app.tr("model.delete_protected"), severity="warning")
            return
        self.app.delete_model_by_name(name)
        self._reload_models()

    def _reload_models(self):
        try:
            from app.core.util.agent_config import get_config
            llm = get_config().get_section("llm") or {}
            models = llm.get("models", [])
            current = llm.get("current_model", "")
            self._models = models
            self._current = current
            lst = self.query_one("#model-picker-list", OptionList)
            lst.clear_options()
            options = []
            for m in self._models:
                name = m.get("name", "")
                mark = "●" if name == current else "○"
                desc = f"{m.get('model', '') or '-'} @ {m.get('base_url', '') or '-'}"
                options.append(Option(f"{mark} {name}  [{desc}]", id=name))
            options.append(Option(self.app.tr("settings.add_model"), id="__add__"))
            lst.add_options(options)
            for i, m in enumerate(self._models):
                if m.get("name") == current:
                    lst.highlighted = i
                    break
        except Exception:
            pass

    def key_escape(self):
        self.app.pop_screen()

    async def _on_switch_ok(self):
        pass


class ProviderPickerScreen(ModalScreen):

    CSS = """
    ProviderPickerScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }
    #provider-picker-panel {
        width: 44;
        max-width: 80%;
        height: auto;
        background: $surface;
    }
    #provider-picker-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }
    #provider-picker-list {
        height: auto;
        max-height: 12;
        padding: 1 0;
        border: none;
        background: $surface;
    }
    #provider-picker-list:focus { border: none; background-tint: transparent; }
    #provider-picker-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def compose(self):
        with Vertical(id="provider-picker-panel"):
            yield Static(self.app.tr("model.provider_title"), id="provider-picker-title")
            yield OptionList(id="provider-picker-list")
            yield Static(self.app.tr("model.provider_footer"), id="provider-picker-footer")

    def on_mount(self):
        lst = self.query_one("#provider-picker-list", OptionList)
        opts = []
        try:
            from app.agent.llm.providers import list_providers
            for p in list_providers():
                opts.append(Option(p.get_provider_name(), id=p.get_provider_id()))
        except Exception:
            pass
        opts.append(Option(self.app.tr("model.provider_custom"), id="__custom__"))
        lst.add_options(opts)
        lst.highlighted = 0
        lst.focus()
        self.query_one("#provider-picker-title", Static).update(self.app.tr("model.provider_title"))
        self.query_one("#provider-picker-footer", Static).update(self.app.tr("model.provider_footer"))

    def on_option_list_option_selected(self, event):
        pid = event.option.id
        if pid == "__custom__":
            self.app.push_screen(ModelAddScreen())
        else:
            self.app.push_screen(CommandCodeAddScreen(provider_id=pid))

    def key_escape(self):
        self.app.pop_screen()


class CommandCodeAddScreen(ModalScreen):

    CSS = """
    CommandCodeAddScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }
    #cc-panel {
        width: 54;
        max-width: 85%;
        height: auto;
        background: $surface;
    }
    #cc-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }
    #cc-body {
        height: auto;
        padding: 1 2;
    }
    #cc-hint {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }
    #cc-input { margin: 0 0 1 0; }
    #cc-status {
        height: auto;
        color: $warning;
        margin-bottom: 1;
    }
    #cc-list {
        height: auto;
        max-height: 14;
        padding: 1 0;
        border: none;
        background: $surface;
    }
    #cc-list:focus { border: none; background-tint: transparent; }
    #cc-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def __init__(self, provider_id: str = "commandcode", initial: dict = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._provider_id = provider_id
        self._initial = initial or {}
        self._phase = "api_key"
        self._models: list = []
        self._saving = False
        self._fetching = False
        self._api_key = self._initial.get("api_key", "")

    def compose(self):
        with Vertical(id="cc-panel"):
            yield Static("", id="cc-title")
            with Vertical(id="cc-body"):
                yield Static("", id="cc-hint")
                yield Input(value=self._api_key, password=True, placeholder="sk-...", id="cc-input")
                yield Static("", id="cc-status")
                yield OptionList(id="cc-list")
            yield Static("", id="cc-footer")

    def on_mount(self):
        provider = self._provider()
        pname = provider.get_provider_name() if provider else "Provider"
        title = self.app.tr("model.edit_title") if self._initial else self.app.tr("model.cc_title_fmt", pname)
        self.query_one("#cc-title", Static).update(title)
        self.query_one("#cc-hint", Static).update(self.app.tr("model.cc_hint_fmt", pname))
        self.query_one("#cc-footer", Static).update(self.app.tr("model.cc_footer_input"))
        self.query_one("#cc-list", OptionList).display = False
        self.query_one("#cc-input", Input).focus()

    def _provider(self):
        try:
            from app.agent.llm.providers import get_provider
            return get_provider(self._provider_id)
        except Exception:
            return None

    def on_input_submitted(self, event):
        if self._phase == "api_key":
            self._on_api_key_enter()

    def _on_api_key_enter(self):
        if self._fetching:
            return
        api_key = (self.query_one("#cc-input", Input).value or "").strip()
        if not api_key:
            self.app.notify(self.app.tr("model.cc_no_api_key"), severity="warning")
            return
        self._api_key = api_key
        asyncio.create_task(self._fetch_models())

    async def _fetch_models(self):
        if self._fetching:
            return
        self._fetching = True
        inp = self.query_one("#cc-input", Input)
        inp.disabled = True
        status = self.query_one("#cc-status", Static)
        status.update(self.app.tr("model.cc_fetching"))
        try:
            provider = self._provider()
            if not provider:
                status.update(self.app.tr("model.cc_fetch_failed", "provider not found"))
                return
            models = await provider.fetch_models(self._api_key)
            if not models:
                status.update(self.app.tr("model.cc_fetch_failed", "empty list"))
                return
            self._models = models
            self._phase = "pick_model"
            inp.display = False
            status.update(self.app.tr("model.cc_select_model"))
            self.query_one("#cc-hint", Static).update(self.app.tr("model.cc_select_hint"))
            self.query_one("#cc-footer", Static).update(self.app.tr("model.cc_footer_pick"))
            lst = self.query_one("#cc-list", OptionList)
            lst.display = True
            lst.clear_options()
            opts = []
            for m in models:
                label = m.get("name") or m.get("id", "")
                mid = m.get("id", "")
                opts.append(Option(label, id=mid))
            lst.add_options(opts)
            lst.highlighted = 0
            lst.focus()
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Unauthorized" in msg:
                msg = "API Key invalid"
            status.update(self.app.tr("model.cc_fetch_failed", msg[:80]))
            inp.disabled = False
            inp.focus()
        finally:
            self._fetching = False

    def on_option_list_option_selected(self, event):
        if self._phase != "pick_model":
            return
        model_id = event.option.id
        model_name = ""
        for m in self._models:
            if m.get("id") == model_id:
                model_name = m.get("name", "")
                break
        asyncio.create_task(self._save(model_id, model_name))

    async def _save(self, model_id: str, model_name: str = ""):
        if self._saving:
            return
        self._saving = True
        try:
            provider = self._provider()
            cfg = provider.create_model_config(self._api_key, model_id, model_name) if provider else {}
            name = cfg.get("name", f"{self._provider_id}-{model_id.split('/')[-1]}")
            editing = bool(self._initial.get("name"))
            if editing:
                name = self._initial.get("name", name)
                cfg["name"] = name
            await asyncio.to_thread(self._persist, name, cfg)
            app = self.app
            app.model_name = app._current_model_name()
            app._refresh_status()
            try:
                app.pop_screen()
                app.pop_screen()
            except Exception:
                pass
            try:
                cur = app.screen
                if isinstance(cur, ModelPickerScreen):
                    cur._reload_models()
            except Exception:
                pass
            try:
                from app.server.routes.config import reload_llm_engine
                await reload_llm_engine()
            except Exception as e:
                app.notify(app.tr("model.reload_failed", e), severity="warning")
            app._update_model_placeholder()
            key = "model.edited" if editing else "model.added"
            await app._append_system(app.tr(key, name))
        except Exception as e:
            self.app.notify(self.app.tr("model.edit_failed" if self._initial else "settings.model.save_failed", e), severity="error")
        finally:
            self._saving = False

    def _persist(self, name, cfg):
        from app.server.routes.config import _sync_model_to_llm_config
        _sync_model_to_llm_config(name, cfg, add=True)

    def key_escape(self):
        if self._phase == "pick_model":
            self._phase = "api_key"
            self.query_one("#cc-input", Input).display = True
            self.query_one("#cc-input", Input).disabled = False
            self.query_one("#cc-input", Input).focus()
            self.query_one("#cc-list", OptionList).display = False
            self.query_one("#cc-hint", Static).update(self.app.tr("model.cc_hint"))
            self.query_one("#cc-footer", Static).update(self.app.tr("model.cc_footer_input"))
            self.query_one("#cc-status", Static).update("")
        else:
            self.app.pop_screen()

class ModelAddScreen(ModalScreen):

    STEPS = [
        ("name", "settings.model.name", "my-model", False),
        ("base_url", "settings.model.base_url", "https://api.openai.com/v1", False),
        ("model_id", "settings.model.model", "glm-5.2", False),
        ("api_key", "settings.model.api_key", "sk-...", True),
    ]

    CSS = """
    ModelAddScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.6);
    }

    #model-add-panel {
        width: 44;
        max-width: 80%;
        height: auto;
        background: $surface;
    }

    #model-add-title {
        height: 3;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
        background: $panel;
    }

    #model-add-body {
        height: auto;
        padding: 1 2;
    }

    #model-add-step {
        height: 1;
        color: $text-muted;
        margin-bottom: 1;
    }

    .model-add-label {
        color: $text-muted;
    }

    #add-input {
        margin: 0 0 1 0;
    }

    #model-add-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $bg-hover;
    }
    """

    def __init__(self, initial: dict = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._step = 0
        self._saving = False
        self._initial = initial or {}
        self._values = {}
        if self._initial:
            self._values = {
                "name": self._initial.get("name", ""),
                "base_url": self._initial.get("base_url", ""),
                "model_id": self._initial.get("model", ""),
                "api_key": self._initial.get("api_key", ""),
            }

    def _editing_name(self):
        return self._initial.get("name")

    def _current_step_value(self):
        key = self.STEPS[self._step][0]
        return self._values.get(key, "")

    def compose(self):
        with Vertical(id="model-add-panel"):
            yield Static(
                self.app.tr("model.edit_title") if self._editing_name() else self.app.tr("settings.add_model"),
                id="model-add-title",
            )
            with Vertical(id="model-add-body"):
                yield Static("", id="model-add-step")
                yield Static("", id="model-add-label", classes="model-add-label")
                yield Input(value=self._current_step_value(), id="add-input")
            yield Static("", id="model-add-footer")

    def on_mount(self):
        self._render_step()

    def _render_step(self):
        key, label_key, placeholder, password = self.STEPS[self._step]
        step = self.STEPS[self._step]
        total = len(self.STEPS)
        self.query_one("#model-add-step", Static).update(
            self.app.tr("model.wizard_step", self._step + 1, total)
        )
        self.query_one("#model-add-label", Static).update(
            self.app.tr(step[1])
        )
        self.query_one("#model-add-footer", Static).update(
            self.app.tr("model.wizard_hint")
        )
        inp = self.query_one("#add-input", Input)
        inp.placeholder = step[2]
        inp.password = bool(step[3])
        inp.value = self._values.get(key, "")
        inp.focus()

    def _current_key(self):
        return self.STEPS[self._step][0]

    def key_escape(self):
        if self._step > 0:
            self._step -= 1
            self._render_step()
        else:
            self.app.pop_screen()

    def on_input_submitted(self, event):
        self._advance()

    def _advance(self):
        value = (self.query_one("#add-input", Input).value or "").strip()
        key = self._current_key()
        if key != "api_key" and not value:
            self.app.notify(self.app.tr("model.add_required"), severity="warning")
            return
        self._values[key] = value
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._render_step()
        else:
            asyncio.create_task(self._save())

    async def _save(self):
        if self._saving:
            return
        self._saving = True
        editing = bool(self._editing_name())
        name = self._values.get("name", "")
        base_url = self._values.get("base_url", "")
        model_id = self._values.get("model_id", "")
        api_key = self._values.get("api_key", "")

        try:
            await asyncio.to_thread(self._persist, name, base_url, model_id, api_key)
            app = self.app
            app.model_name = app._current_model_name()
            app._refresh_status()
            try:
                app.pop_screen()
            except Exception:
                pass
            try:
                current_screen = app.screen
                from app.tui.model_picker import ModelPickerScreen
                if isinstance(current_screen, ModelPickerScreen):
                    current_screen._reload_models()
            except Exception:
                pass
            try:
                from app.server.routes.config import reload_llm_engine
                await reload_llm_engine()
            except Exception as _reload_err:
                app.notify(app.tr("model.reload_failed", _reload_err), severity="warning")
            app._update_model_placeholder()
            msg = app.tr("model.edited", name) if editing else app.tr("model.added", name)
            await app._append_system(msg)
        except Exception as e:
            key = "model.edit_failed" if editing else "settings.model.save_failed"
            self.app.notify(self.app.tr(key, e), severity="error")
        finally:
            self._saving = False

    def _persist(self, name, base_url, model_id, api_key):
        from app.server.routes.config import _sync_model_to_llm_config

        model_dict = {
            "name": name,
            "provider": "openai",
            "base_url": base_url,
            "model": model_id,
            "api_key": api_key,
            "temperature": 0.7,
            "timeout": 120,
        }
        _sync_model_to_llm_config(name, model_dict, add=True)

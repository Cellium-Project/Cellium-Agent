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
            self.app.push_screen(ModelAddScreen())
            return
        name = event.option.id
        if name == self._current:
            self.app.pop_screen()
            return
        self.dismiss(name)

    def key_e(self):
        """编辑选中的模型"""
        name = self._selected_model()
        if not name or name == "__add__":
            self.app.notify(self.app.tr("model.not_found", name or ""), severity="warning")
            return
        model = next((m for m in self._models if m.get("name") == name), None)
        if model is None:
            return
        self.app.push_screen(ModelAddScreen(initial=model))

    def key_d(self):
        """删除选中的模型"""
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
            app.model_name = name
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

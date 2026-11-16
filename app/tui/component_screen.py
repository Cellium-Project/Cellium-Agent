# -*- coding: utf-8 -*-
"""组件管理：本地组件列表 + 组件市场，设置页与 /components 共用"""

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static
from textual.widgets._option_list import Option


def _component_builder():
    from app.core.util.components_loader import get_cell
    cell = get_cell("component")
    if cell is not None:
        return cell
    from components.component_builder import ComponentBuilder
    return ComponentBuilder()


class ComponentPanel(Vertical):
    """组件列表 + 市场搜索，加载/卸载/安装都用回车触发"""

    BINDINGS = [("r", "reload_all", "热重载")]

    DEFAULT_CSS = """
    ComponentPanel { height: auto; }
    ComponentPanel .component-section {
        margin: 1 0 0 0;
        color: $primary;
        text-style: bold;
    }
    ComponentPanel #component-loaded, ComponentPanel #component-available {
        height: auto;
        max-height: 12;
        border: none;
    }
    ComponentPanel #component-status, ComponentPanel #market-status {
        height: auto;
        color: $text-muted;
    }
    ComponentPanel #market-query { margin: 1 0 0 0; }
    ComponentPanel #market-results {
        height: auto;
        max-height: 12;
        border: none;
    }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded = []
        self._available = []
        self._market_items = []

    def compose(self) -> ComposeResult:
        yield Static("", id="component-status")
        yield Static(self.app.tr("components.loaded"), classes="component-section")
        yield OptionList(id="component-loaded")
        yield Static(self.app.tr("components.available"), classes="component-section")
        yield OptionList(id="component-available")
        yield Static(self.app.tr("components.market_title"), classes="component-section")
        yield Input(placeholder=self.app.tr("components.market_placeholder"), id="market-query")
        yield Static("", id="market-status")
        yield OptionList(id="market-results")

    def on_mount(self):
        self.refresh_listing()
        self.run_worker(self._search_market(""), exclusive=True)

    def _status(self, text: str, oid: str = "#component-status"):
        try:
            self.query_one(oid, Static).update(text)
        except Exception:
            pass

    async def _ask(self, message: str, confirm_label: str, cancel_label: str) -> bool:
        from app.tui.session_picker import DeleteConfirmScreen
        try:
            return bool(await self.app.push_screen_wait(
                DeleteConfirmScreen(message, confirm_label, cancel_label)
            ))
        except Exception:
            return False

    def refresh_listing(self):
        from app.core.util.components_loader import (
            discover_components,
            get_all_cells,
            get_all_commands,
        )
        from app.core.util.component_tool_registry import DEFAULT_COMPONENT_MODULES

        cells = get_all_cells()
        commands = get_all_commands()
        self._loaded = [
            (
                name,
                len(commands.get(name, {})),
                getattr(cell, "_source_file", "") or "",
                name.lower() in DEFAULT_COMPONENT_MODULES,
            )
            for name, cell in sorted(cells.items())
        ]
        loaded_files = {str(f).lower() for _, _, f, _ in self._loaded if f}
        self._available = [
            (item.get("class_name", ""), item.get("module_path", ""), str(item.get("file", "")))
            for item in discover_components()
            if str(item.get("file", "")).lower() not in loaded_files
        ]

        try:
            loaded_list = self.query_one("#component-loaded", OptionList)
            loaded_list.clear_options()
            loaded_list.add_options(
                [
                    Option(
                        f"{name}  ({count} commands){'  [内置]' if builtin else ''}",
                        id=f"loaded:{name}",
                    )
                    for name, count, _f, builtin in self._loaded
                ]
            )
            available_list = self.query_one("#component-available", OptionList)
            available_list.clear_options()
            available_list.add_options(
                [
                    Option(f"{cls}  ({module})", id=f"new:{module}")
                    for cls, module, _ in self._available
                ]
            )
            default_list = available_list if self._available else loaded_list
            if default_list.option_count:
                default_list.highlighted = 0
            default_list.focus()
        except Exception:
            pass

    def action_reload_all(self):
        try:
            from app.core.di.container import get_container
            from app.core.util.components_loader import hot_reload

            report = hot_reload(container=get_container())
            self._status(self.app.tr(
                "components.reloaded",
                len(report.get("added", [])),
                len(report.get("removed", [])),
            ))
        except Exception as e:
            self._status(self.app.tr("components.load_failed", e))
        self.refresh_listing()

    async def _auto_reload(self):
        """依赖装好后自动热重载，让组件立刻可用"""
        try:
            from app.core.di.container import get_container
            from app.core.util.components_loader import hot_reload

            await asyncio.to_thread(lambda: hot_reload(container=get_container()))
        except Exception:
            pass
        self.refresh_listing()

    async def _load(self, module_path: str):
        item = None
        try:
            from app.core.di.container import get_container
            from app.core.util.components_loader import (
                _instantiate_component,
                _loaded_files,
                discover_components,
                register_cell,
                register_to_config,
                sync_tool_registry,
            )

            item = await asyncio.to_thread(
                lambda: next(
                    (i for i in discover_components() if i.get("module_path") == module_path),
                    None,
                )
            )
            if not item:
                raise RuntimeError(f"未找到组件模块: {module_path}")
            cls = item.get("cls")
            if cls is not None:
                cell = cls()
            else:
                cell, _info = _instantiate_component(module_path)
            if cell is None:
                raise RuntimeError("组件实例化失败")
            register_cell(cell)
            _loaded_files.add(item["file"])
            cell._source_file = item["file"]
            if hasattr(cell, "on_load"):
                cell.on_load()
            try:
                get_container().register(type(cell), cell)
            except Exception:
                pass
            register_to_config(module_path)
            sync_tool_registry()
            self._status(self.app.tr("components.loaded_ok", cell.cell_name))
        except Exception as e:
            from app.core.util.components_loader import (
                dependency_hint,
                missing_dependency_specs,
            )

            source = str(item.get("file", "")) if item else ""
            specs = missing_dependency_specs(e, source)
            reason = f"{type(e).__name__}: {e}"
            hint = dependency_hint(e, source)
            if hint:
                reason = f"{hint}（{reason}）"
            self._status(self.app.tr("components.load_failed", reason))

            if specs:
                joined = " ".join(specs)
                if await self._ask(
                    self.app.tr("components.confirm_deps", joined),
                    self.app.tr("components.install_deps"),
                    self.app.tr("common.cancel"),
                ):
                    self._status(self.app.tr("components.deps_installing", joined))
                    pip = await asyncio.to_thread(_component_builder()._pip_install, specs)
                    if pip.get("success"):
                        self._status(self.app.tr("components.deps_ok"))
                        await self._auto_reload()
                    else:
                        self._status(self.app.tr("components.deps_failed"))
        self.refresh_listing()

    async def _unload(self, name: str, path: str):
        try:
            from app.core.util.components_loader import remove_component

            result = await asyncio.to_thread(remove_component, name, delete_files=True)
            if not result.get("success"):
                self._status(self.app.tr("components.unload_failed", result.get("error") or name))
            elif result.get("deleted"):
                self._status(self.app.tr(
                    "components.uninstalled_ok", name, result.get("path") or path
                ))
            else:
                self._status(self.app.tr("components.unloaded_ok", name))
        except Exception as e:
            self._status(self.app.tr("components.unload_failed", e))
        self.refresh_listing()
        keyword = ""
        try:
            keyword = self.query_one("#market-query", Input).value
        except Exception:
            pass
        await self._search_market(keyword, keep_status=True)

    async def _install(self, slug: str):
        self._status(self.app.tr("components.installing", slug), "#market-status")
        try:
            result = await asyncio.to_thread(_component_builder()._cmd_market_install, slug=slug)
        except Exception as e:
            self._status(self.app.tr("components.install_failed", e), "#market-status")
            return
        if result.get("success") is False:
            self._status(self.app.tr("components.install_failed", result.get("error", "")), "#market-status")
            return
        self._status(self.app.tr("components.install_ok", result.get("name") or slug), "#market-status")
        self.refresh_listing()

        targets = [t for t in ((result.get("dependencies") or {}).get("targets") or []) if t]
        if targets:
            joined = " ".join(targets)
            if await self._ask(
                self.app.tr("components.confirm_deps", joined),
                self.app.tr("components.install_deps"),
                self.app.tr("common.cancel"),
            ):
                self._status(self.app.tr("components.deps_installing", joined), "#market-status")
                pip = await asyncio.to_thread(_component_builder()._pip_install, targets)
                if pip.get("success"):
                    self._status(self.app.tr("components.deps_ok"), "#market-status")
                    await self._auto_reload()
                else:
                    self._status(self.app.tr("components.deps_failed"), "#market-status")

        keyword = ""
        try:
            keyword = self.query_one("#market-query", Input).value
        except Exception:
            pass
        await self._search_market(keyword, keep_status=True)

    async def _search_market(self, keyword: str, keep_status: bool = False):
        if not keep_status:
            self._status(self.app.tr("common.loading"), "#market-status")
        result = await asyncio.to_thread(
            _component_builder()._cmd_market_search, keyword=keyword, page_size=30
        )
        if result.get("success") is False:
            self._status(self.app.tr("components.market_error", result.get("error", "")), "#market-status")
            return
        self._market_items = result.get("items") or []
        try:
            results = self.query_one("#market-results", OptionList)
            results.clear_options()
            results.add_options(
                [
                    Option(
                        f"{it.get('name') or it.get('slug')}"
                        f"  v{it.get('latest_version', '')}"
                        f"{'  [已安装]' if it.get('installed') else ''}"
                        f"  {str(it.get('summary') or '')[:70]}",
                        id=f"market:{it.get('slug', '')}",
                    )
                    for it in self._market_items
                ]
            )
            if self._market_items:
                results.highlighted = 0
        except Exception:
            pass
        if not keep_status:
            self._status(self.app.tr("components.market_count", len(self._market_items)), "#market-status")

    def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "market-query":
            return
        self.run_worker(self._search_market(event.value), exclusive=True)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        oid = event.option_list.id
        value = str(getattr(event.option, "id", "") or "")
        if oid == "component-available" and value.startswith("new:"):
            self.run_worker(self._load(value[len("new:"):]), exclusive=True)
        elif oid == "component-loaded" and value.startswith("loaded:"):
            self.run_worker(self._confirm_unload(value[len("loaded:"):]), exclusive=True)
        elif oid == "market-results" and value.startswith("market:"):
            self.run_worker(self._confirm_install(value[len("market:"):]), exclusive=True)

    async def _confirm_unload(self, name: str):
        if any(name == n and builtin for n, _c, _f, builtin in self._loaded):
            self._status(self.app.tr("components.builtin_skip", name))
            return
        path = next((p for n, _c, p, _b in self._loaded if n == name), "")
        ok = await self._ask(
            self.app.tr("components.confirm_unload", name, path),
            self.app.tr("common.delete"),
            self.app.tr("common.cancel"),
        )
        if ok:
            await self._unload(name, path)

    async def _confirm_install(self, slug: str):
        ok = await self._ask(
            self.app.tr("components.confirm_install", slug),
            self.app.tr("components.install"),
            self.app.tr("common.cancel"),
        )
        if ok:
            await self._install(slug)


class ComponentScreen(Screen):
    """全屏组件管理（/components 命令入口）"""

    BINDINGS = [("escape", "back", "返回")]

    CSS = """
    Screen { layout: vertical; background: $background; color: $foreground; }
    #component-root { height: 1fr; padding: 1 2; }
    .component-heading {
        height: 2;
        color: $primary;
        text-style: bold;
        border-bottom: solid $border;
    }
    #component-scroll { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="component-root"):
            yield Static(self.app.tr("components.title"), classes="component-heading")
            with VerticalScroll(id="component-scroll"):
                yield ComponentPanel()

    def action_back(self):
        self.app.pop_screen()

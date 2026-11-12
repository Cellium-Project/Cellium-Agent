# -*- coding: utf-8 -*-
"""POST /api/components/{name}/load 必须支持单文件组件（cls 为 None）"""
import asyncio
import sys
import textwrap
from unittest.mock import patch

from app.core.util import components_loader as cl
from app.core.util.component_tool_registry import get_component_tool_registry
from app.server.routes import components as routes

COMPONENT_SRC = textwrap.dedent('''
    from app.core.interface.base_cell import BaseCell

    class SingleDemo(BaseCell):
        """单文件组件"""

        @property
        def cell_name(self) -> str:
            return "single_demo"

        def _cmd_ping(self) -> dict:
            """ping"""
            return {"success": True}
''')


class _FakeContainer:
    def register(self, *args, **kwargs):
        return None


def test_load_single_file_component(tmp_path):
    user_dir = tmp_path / "components"
    builtin_dir = tmp_path / "builtin"
    user_dir.mkdir()
    builtin_dir.mkdir()
    (user_dir / "single_demo.py").write_text(COMPONENT_SRC, encoding="utf-8")

    registry = get_component_tool_registry()
    registry._registry.clear()

    try:
        with patch.object(cl, "get_components_dir", return_value=user_dir), \
             patch.object(cl, "get_builtin_components_dir", return_value=builtin_dir), \
             patch.object(cl, "register_to_config", lambda *a, **k: None), \
             patch("app.core.di.container.get_container", return_value=_FakeContainer()):
            result = asyncio.run(routes.load_single_component("single_demo"))

        assert result["status"] == "ok"
        assert cl.get_cell("single_demo") is not None
    finally:
        cl.unregister_cell("single_demo")
        cl._loaded_files.discard(str(user_dir / "single_demo.py"))
        registry._registry.clear()
        for path in (str(user_dir), str(builtin_dir)):
            while path in sys.path:
                sys.path.remove(path)
        sys.modules.pop("single_demo", None)

# -*- coding: utf-8 -*-
import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, ".")

import app.core.util.cell_tool_adapter as cta
from app.core.interface.base_cell import BaseCell

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _ProbeCell(BaseCell):
    def __init__(self, name):
        super().__init__()
        self._probe_name = name

    @property
    def cell_name(self):
        return self._probe_name

    def _cmd_ping(self):
        """ping"""
        return {"ok": 1}


class _FakeCell:
    def __init__(self, name):
        self.cell_name = name


class _TmpWhitelist:
    """把免沙箱白名单指向临时目录，隔离真实 config/sandbox.yaml"""

    def __init__(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "sandbox.yaml")
        self._saved_cache = cta._sandbox_whitelist_cache
        self._saved_enabled = cta.SANDBOX_ENABLED
        cta._sandbox_whitelist_cache = {"path": self.path}

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def close(self):
        cta._sandbox_whitelist_cache = self._saved_cache
        cta.SANDBOX_ENABLED = self._saved_enabled
        self._dir.cleanup()


class TestWhitelistFile(unittest.TestCase):

    def setUp(self):
        self.wl = _TmpWhitelist()

    def tearDown(self):
        self.wl.close()

    def test_path_uses_tmp(self):
        self.assertEqual(cta.get_sandbox_whitelist_path(), self.wl.path)

    def test_missing_file_returns_empty(self):
        self.assertEqual(cta.load_sandbox_whitelist(), set())

    def test_roundtrip(self):
        cta.save_sandbox_whitelist({"a_comp", "b_comp"})
        self.assertEqual(cta.load_sandbox_whitelist(), {"a_comp", "b_comp"})

    def test_save_creates_missing_dir(self):
        """首次添加时父目录不存在也要能自动建出来"""
        nested = os.path.join(self.wl._dir.name, "x", "y", "sandbox.yaml")
        cta._sandbox_whitelist_cache = {"path": nested}
        cta.save_sandbox_whitelist({"first_comp"})
        self.assertTrue(os.path.exists(nested))
        self.assertEqual(cta.load_sandbox_whitelist(), {"first_comp"})

    def test_dedup_and_blank_filtered(self):
        cta.save_sandbox_whitelist({"dup", "dup", "  ", "", None})
        self.assertEqual(cta.load_sandbox_whitelist(), {"dup"})

    def test_yaml_format(self):
        cta.save_sandbox_whitelist({"x_comp"})
        content = self.wl.read()
        self.assertTrue(content.startswith("#"))
        self.assertIn("exempted:\n  - x_comp", content)

    def test_sorted_output(self):
        cta.save_sandbox_whitelist({"c_comp", "a_comp", "b_comp"})
        content = self.wl.read()
        self.assertLess(content.index("- a_comp"), content.index("- b_comp"))
        self.assertLess(content.index("- b_comp"), content.index("- c_comp"))

    def test_save_then_load_reflects_latest(self):
        cta.save_sandbox_whitelist({"v1"})
        self.assertEqual(cta.load_sandbox_whitelist(), {"v1"})
        cta.save_sandbox_whitelist({"v2"})
        self.assertEqual(cta.load_sandbox_whitelist(), {"v2"})

    def test_manual_edit_picked_up(self):
        cta.save_sandbox_whitelist({"old"})
        self.assertEqual(cta.load_sandbox_whitelist(), {"old"})
        self.wl.write("exempted:\n  - manual_new\n")
        self.assertEqual(cta.load_sandbox_whitelist(), {"manual_new"})

    def test_no_stale_cache(self):
        """同 mtime 下改文件内容也必须读到新值（不做缓存）"""
        cta.save_sandbox_whitelist({"v1"})
        self.assertEqual(cta.load_sandbox_whitelist(), {"v1"})
        st = os.stat(self.wl.path)
        self.wl.write("exempted:\n  - v2\n")
        os.utime(self.wl.path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(cta.load_sandbox_whitelist(), {"v2"})

    def test_corrupt_yaml_returns_empty(self):
        self.wl.write("exempted: [unclosed\n\t- bad\n")
        self.assertEqual(cta.load_sandbox_whitelist(), set())

    def test_bare_list_format(self):
        self.wl.write("- bare_a\n- bare_b\n")
        self.assertEqual(cta.load_sandbox_whitelist(), {"bare_a", "bare_b"})

    def test_dirty_value_type_ignored(self):
        self.wl.write("exempted: not_a_list\n")
        self.assertEqual(cta.load_sandbox_whitelist(), set())

    def test_exempted_names_keep_builtin(self):
        cta.save_sandbox_whitelist({"custom_x"})
        names = cta.get_exempted_names()
        self.assertIn("component", names)
        self.assertIn("scheduler", names)
        self.assertIn("skill_manager", names)
        self.assertIn("custom_x", names)

    def test_builtin_never_diluted_by_empty_file(self):
        cta.save_sandbox_whitelist(set())
        names = cta.get_exempted_names()
        self.assertEqual(names, set(cta.EXEMPTED_NAMES))


class TestAdapterSandboxMode(unittest.TestCase):

    def setUp(self):
        self.wl = _TmpWhitelist()

    def tearDown(self):
        self.wl.close()

    def test_builtin_not_sandboxed(self):
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("scheduler"))._use_sandbox)
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("component"))._use_sandbox)

    def test_custom_component_sandboxed(self):
        self.assertTrue(cta.CellToolAdapter(_ProbeCell("random_comp"))._use_sandbox)

    def test_whitelisted_component_not_sandboxed(self):
        cta.save_sandbox_whitelist({"random_comp"})
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("random_comp"))._use_sandbox)
        self.assertTrue(cta.CellToolAdapter(_ProbeCell("other_comp"))._use_sandbox)

    def test_removed_returns_to_sandbox(self):
        cta.save_sandbox_whitelist({"random_comp"})
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("random_comp"))._use_sandbox)
        cta.save_sandbox_whitelist(set())
        self.assertTrue(cta.CellToolAdapter(_ProbeCell("random_comp"))._use_sandbox)

    def test_explicit_flag_wins(self):
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("random_comp"), use_sandbox=False)._use_sandbox)
        self.assertTrue(cta.CellToolAdapter(_ProbeCell("scheduler"), use_sandbox=True)._use_sandbox)

    def test_global_switch_off(self):
        cta.SANDBOX_ENABLED = False
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("random_comp"))._use_sandbox)

    def test_refresh_sandbox_mode(self):
        """热重载复用同一 adapter 时，必须按新白名单重算"""
        a = cta.CellToolAdapter(_ProbeCell("random_comp"))
        self.assertTrue(a._use_sandbox)
        cta.save_sandbox_whitelist({"random_comp"})
        self.assertFalse(a.refresh_sandbox_mode())
        cta.save_sandbox_whitelist(set())
        self.assertTrue(a.refresh_sandbox_mode())

    def test_refresh_keeps_explicit_flag(self):
        a = cta.CellToolAdapter(_ProbeCell("random_comp"), use_sandbox=False)
        cta.save_sandbox_whitelist(set())
        self.assertFalse(a.refresh_sandbox_mode())

    def test_registry_reuses_adapter_but_refreshes(self):
        from app.core.util.component_tool_registry import ComponentToolRegistry

        reg = ComponentToolRegistry()
        cell = _ProbeCell("zz_refresh")
        a1 = reg.register(cell)
        self.assertTrue(a1._use_sandbox)
        cta.save_sandbox_whitelist({"zz_refresh"})
        a2 = reg.register(cell)
        self.assertIs(a1, a2)
        self.assertFalse(a1._use_sandbox)


class TestSandboxCommands(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "probe_component_builder", os.path.join(ROOT, "components", "component_builder.py")
        )
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def setUp(self):
        self.wl = _TmpWhitelist()
        # 与真实契约一致：get_all_cells() 返回 {cell_name: cell} 的 dict
        self.mod.get_all_cells = lambda: {
            n: _FakeCell(n) for n in ("demo_a", "demo_b", "component", "scheduler")
        }
        self.builder = self.mod.ComponentBuilder()

    def tearDown(self):
        self.wl.close()

    def test_commands_registered(self):
        cmds = self.builder.get_commands()
        self.assertIn("sandbox_add", cmds)
        self.assertIn("sandbox_list", cmds)
        self.assertIn("sandbox_remove", cmds)

    def test_list_structure(self):
        cta.save_sandbox_whitelist({"demo_a"})
        info = self.builder._cmd_sandbox_list()
        self.assertTrue(info["success"])
        self.assertEqual(info["whitelist_file"], self.wl.path)
        self.assertIn("component", info["builtin"])
        self.assertEqual(info["custom"], ["demo_a"])
        self.assertIn("demo_b", info["in_sandbox"])
        self.assertNotIn("demo_a", info["in_sandbox"])
        self.assertEqual(info["stale"], [])
        self.assertIsInstance(info["components_dirs"], list)

    def test_list_stale_detected(self):
        cta.save_sandbox_whitelist({"ghost_comp"})
        info = self.builder._cmd_sandbox_list()
        self.assertEqual(info["stale"], ["ghost_comp"])

    def test_add_missing_name(self):
        r = self.builder._cmd_sandbox_add(name="", auto_reload=False)
        self.assertFalse(r["success"])
        self.assertIn("name", r["error"])

    def test_add_unloaded_component(self):
        r = self.builder._cmd_sandbox_add(name="nope_comp", auto_reload=False)
        self.assertFalse(r["success"])
        self.assertIn("未加载", r["error"])
        self.assertIn("demo_a", r["loaded_components"])

    def test_add_builtin_skipped(self):
        r = self.builder._cmd_sandbox_add(name="component", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertTrue(r["already_exempt"])
        self.assertEqual(cta.load_sandbox_whitelist(), set())

    def test_add_ok(self):
        r = self.builder._cmd_sandbox_add(name="demo_a", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertEqual(r["custom"], ["demo_a"])
        self.assertEqual(cta.load_sandbox_whitelist(), {"demo_a"})
        self.assertFalse(r["reloaded"])
        self.assertIn("reload", r["hint"].lower())

    def test_add_duplicate(self):
        self.builder._cmd_sandbox_add(name="demo_a", auto_reload=False)
        r = self.builder._cmd_sandbox_add(name="demo_a", auto_reload=False)
        self.assertTrue(r["already_exempt"])
        self.assertEqual(cta.load_sandbox_whitelist(), {"demo_a"})

    def test_remove_missing_name(self):
        r = self.builder._cmd_sandbox_remove(name="", auto_reload=False)
        self.assertFalse(r["success"])

    def test_remove_builtin_rejected(self):
        r = self.builder._cmd_sandbox_remove(name="scheduler", auto_reload=False)
        self.assertFalse(r["success"])
        self.assertIn("内置", r["error"])

    def test_remove_not_in_whitelist(self):
        r = self.builder._cmd_sandbox_remove(name="demo_a", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertIn("不在", r["message"])
        self.assertEqual(cta.load_sandbox_whitelist(), set())

    def test_remove_ok(self):
        cta.save_sandbox_whitelist({"demo_a", "demo_b"})
        r = self.builder._cmd_sandbox_remove(name="demo_a", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertEqual(r["custom"], ["demo_b"])
        self.assertEqual(cta.load_sandbox_whitelist(), {"demo_b"})

    def test_add_name_case_insensitive(self):
        """LLM 传小写，白名单必须落原始 cell_name"""
        self.mod.get_all_cells = lambda: {"demo_a": _FakeCell("Demo_A")}
        r = self.builder._cmd_sandbox_add(name="demo_a", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertEqual(r["resolved_name"], "Demo_A")
        self.assertEqual(cta.load_sandbox_whitelist(), {"Demo_A"})
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("Demo_A"))._use_sandbox)

    def test_add_sanitized_tool_name_resolved(self):
        """LLM 看到的是 sanitize 后的工具名，也要能还原成原始名"""
        self.mod.get_all_cells = lambda: {"demo.probe": _FakeCell("Demo.Probe")}
        r = self.builder._cmd_sandbox_add(name="Demo_Probe", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertEqual(r["resolved_name"], "Demo.Probe")
        self.assertEqual(cta.load_sandbox_whitelist(), {"Demo.Probe"})
        self.assertFalse(cta.CellToolAdapter(_ProbeCell("Demo.Probe"))._use_sandbox)

    def test_remove_name_case_insensitive(self):
        cta.save_sandbox_whitelist({"Demo_B"})
        self.mod.get_all_cells = lambda: {"demo_b": _FakeCell("Demo_B")}
        r = self.builder._cmd_sandbox_remove(name="demo_b", auto_reload=False)
        self.assertTrue(r["success"])
        self.assertEqual(cta.load_sandbox_whitelist(), set())


if __name__ == "__main__":
    unittest.main()

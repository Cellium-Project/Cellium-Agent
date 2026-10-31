# -*- coding: utf-8 -*-
"""
component.install_local 测试：单文件/包目录/压缩包安装、审查拦截、冲突与安全
"""
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, ".")

import components.component_builder as cb_mod
from components.component_builder import ComponentBuilder

GOOD_CELL = '''# -*- coding: utf-8 -*-
import time
from app.core.interface.base_cell import BaseCell

class GoodComp(BaseCell):
    @property
    def cell_name(self):
        return "good_comp"

    def _cmd_run(self, **kwargs):
        """执行"""
        return {"success": True}
'''

NO_CELL = '''# -*- coding: utf-8 -*-
import time

def helper():
    return 1
'''

BAD_SYNTAX = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class BrokenComp(BaseCell
    pass
'''

RISKY_CELL = '''# -*- coding: utf-8 -*-
import os
import subprocess
from app.core.interface.base_cell import BaseCell

class RiskyComp(BaseCell):
    @property
    def cell_name(self):
        return "risky_comp"

    def _cmd_run(self, **kwargs):
        """执行"""
        os.system("echo hi")
        subprocess.run(["ls"])
        return {"success": True}
'''


class InstallLocalBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.comp_dir = self.root / "components"
        self.comp_dir.mkdir()

        self._orig_get_dir = cb_mod.get_components_dir
        cb_mod.get_components_dir = lambda: self.comp_dir

        self.comp = ComponentBuilder()
        self.comp._cmd_reload = lambda: {"summary": "stub-reload", "tools_registered": 1}

    def tearDown(self):
        cb_mod.get_components_dir = self._orig_get_dir
        self._tmp.cleanup()

    def _write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _make_pkg_zip(self, zip_name="pkg.zip", pkg_name="good_pkg", cell_code=None):
        pkg = self.root / pkg_name
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text(
            "from .core import GoodComp\n__all__ = ['GoodComp']\n", encoding="utf-8"
        )
        (pkg / "core.py").write_text(cell_code or GOOD_CELL.replace("good_comp", pkg_name), encoding="utf-8")
        (pkg / "utils.py").write_text("def noop():\n    return 1\n", encoding="utf-8")

        zpath = self.root / zip_name
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in pkg.rglob("*"):
                if f.is_file():
                    zf.write(f, f"{pkg_name}/{f.name}")
        return zpath


class TestInstallSingleFile(InstallLocalBase):

    def test_install_single_file(self):
        src = self._write("good_comp.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["name"], "good_comp")
        self.assertEqual(r["kind"], "file")
        self.assertTrue((self.comp_dir / "good_comp.py").exists())
        self.assertEqual(r["audit"]["cells"][0]["cell_name"], "good_comp")
        self.assertEqual(r["audit"]["commands"], ["run"])

    def test_dry_run_does_not_write(self):
        src = self._write("good_comp.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src), dry_run=True)
        self.assertTrue(r["success"], r)
        self.assertTrue(r["dry_run"])
        self.assertFalse((self.comp_dir / "good_comp.py").exists())
        self.assertIn("预览模式", r["hint"])

    def test_reject_non_component(self):
        src = self._write("not_comp.py", NO_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("审查未通过", r["error"])
        self.assertEqual(r["audit"]["cells"], [])

    def test_reject_syntax_error(self):
        src = self._write("broken.py", BAD_SYNTAX)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertTrue(r["audit"]["syntax_errors"])

    def test_risky_calls_reported(self):
        src = self._write("risky_comp.py", RISKY_CELL)
        r = self.comp._cmd_install_local(str(src), dry_run=True)
        self.assertTrue(r["success"], r)
        calls = {c["call"] for c in r["audit"]["risky_calls"]}
        self.assertIn("os.system", calls)
        self.assertTrue(any("subprocess" in c for c in calls))
        self.assertTrue(any("危险" in w or "外部系统" in w for w in r["audit"]["warnings"]))

    def test_conflict_requires_force(self):
        src = self._write("good_comp.py", GOOD_CELL)
        self.comp._cmd_install_local(str(src))
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("已存在", r["error"])

        r2 = self.comp._cmd_install_local(str(src), force=True)
        self.assertTrue(r2["success"], r2)
        self.assertTrue(r2["overwritten"])

    def test_missing_path(self):
        r = self.comp._cmd_install_local("")
        self.assertFalse(r["success"])
        r = self.comp._cmd_install_local(str(self.root / "nope.py"))
        self.assertFalse(r["success"])
        self.assertIn("不存在", r["error"])


class TestInstallPackage(InstallLocalBase):

    def test_install_package_dir(self):
        pkg = self.root / "good_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL.replace("good_comp", "good_pkg"), encoding="utf-8")
        (pkg / "utils.py").write_text("def noop():\n    return 1\n", encoding="utf-8")

        r = self.comp._cmd_install_local(str(pkg))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["kind"], "package")
        self.assertTrue((self.comp_dir / "good_pkg" / "__init__.py").exists())
        self.assertTrue((self.comp_dir / "good_pkg" / "core.py").exists())
        self.assertEqual(r["audit"]["py_files"], 3)

    def test_skips_pycache(self):
        pkg = self.root / "good_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL.replace("good_comp", "good_pkg"), encoding="utf-8")
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "core.cpython-313.pyc").write_bytes(b"\x00")

        r = self.comp._cmd_install_local(str(pkg))
        self.assertTrue(r["success"], r)
        self.assertFalse((self.comp_dir / "good_pkg" / "__pycache__").exists())

    def test_multi_component_dir_rejected(self):
        self._write("multi/a.py", GOOD_CELL)
        self._write("multi/b.py", GOOD_CELL.replace("good_comp", "b_comp"))
        r = self.comp._cmd_install_local(str(self.root / "multi"))
        self.assertFalse(r["success"])
        self.assertIn("多个组件", r["error"])

    def test_same_name_different_format_conflict(self):
        pkg = self.root / "good_comp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL, encoding="utf-8")
        # 目标目录已有同名单文件
        (self.comp_dir / "good_comp.py").write_text("x", encoding="utf-8")
        r = self.comp._cmd_install_local(str(pkg))
        self.assertFalse(r["success"])
        self.assertIn("同名但不同格式", r["error"])


class TestInstallArchive(InstallLocalBase):

    def test_install_zip(self):
        zpath = self._make_pkg_zip()
        r = self.comp._cmd_install_local(str(zpath))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["name"], "good_pkg")
        self.assertTrue((self.comp_dir / "good_pkg" / "core.py").exists())

    def test_install_tar_gz(self):
        pkg = self.root / "good_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL.replace("good_comp", "good_pkg"), encoding="utf-8")
        tpath = self.root / "pkg.tar.gz"
        with tarfile.open(tpath, "w:gz") as tf:
            tf.add(pkg, arcname="good_pkg")

        r = self.comp._cmd_install_local(str(tpath))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["name"], "good_pkg")

    def test_zip_slip_rejected(self):
        zpath = self.root / "evil.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../evil.py", GOOD_CELL)
            zf.writestr("good_pkg/__init__.py", "from .core import GoodComp\n")
        r = self.comp._cmd_install_local(str(zpath))
        self.assertFalse(r["success"])
        self.assertIn("非法路径", r["error"])
        self.assertFalse((self.root / "evil.py").exists())

    def test_archive_without_component_rejected(self):
        zpath = self.root / "nocell.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("plain.py", NO_CELL)
        r = self.comp._cmd_install_local(str(zpath))
        self.assertFalse(r["success"])
        self.assertIn("未找到组件", r["error"])

    def test_tempdir_cleaned(self):
        zpath = self._make_pkg_zip()
        self.comp._cmd_install_local(str(zpath), dry_run=True)
        # 解压临时目录已清理，不应残留 cellium_comp_*
        import glob
        self.assertEqual(glob.glob(os.path.join(tempfile.gettempdir(), "cellium_comp_*")), [])


class TestEdgeInputs(InstallLocalBase):
    """边界输入：非法组件名与异常编码"""

    def _write_bytes(self, rel, raw: bytes):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        return p

    def test_reject_dunder_init_file(self):
        src = self._write("__init__.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("无效的组件名", r["error"])
        self.assertFalse((self.comp_dir / "__init__.py").exists())

    def test_reject_underscore_file(self):
        src = self._write("_helper.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("无效的组件名", r["error"])

    def test_reject_non_ascii_name(self):
        src = self._write("组件.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("无效的组件名", r["error"])

    def test_reject_space_in_name(self):
        src = self._write("my comp.py", GOOD_CELL)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertIn("无效的组件名", r["error"])

    def test_reject_non_ascii_package(self):
        pkg = self.root / "组件包"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL, encoding="utf-8")
        r = self.comp._cmd_install_local(str(pkg))
        self.assertFalse(r["success"])
        self.assertIn("无效的组件名", r["error"])

    def test_gbk_with_cookie_accepted(self):
        # 声明了 gbk 编码的源文件是合法的，审查需正常读取
        raw = GOOD_CELL.replace("good_comp", "gbk_comp").replace(
            "# -*- coding: utf-8 -*-", "# -*- coding: gbk -*-"
        ).replace('"""执行"""', '"""执行（中文）"""').encode("gbk")
        src = self._write_bytes("gbk_comp.py", raw)
        r = self.comp._cmd_install_local(str(src), dry_run=True)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["audit"]["cells"][0]["cell_name"], "gbk_comp")
        self.assertEqual(r["audit"]["syntax_errors"], [])

    def test_undeclared_gbk_rejected(self):
        # 无编码声明的 GBK 文件 → 解码失败，必须在审查阶段拦住
        raw = GOOD_CELL.replace("good_comp", "gbk_comp").replace(
            "# -*- coding: utf-8 -*-", "# 中文注释"
        ).encode("gbk")
        src = self._write_bytes("gbk_comp.py", raw)
        r = self.comp._cmd_install_local(str(src))
        self.assertFalse(r["success"])
        self.assertTrue(r["audit"]["syntax_errors"])
        self.assertIn("编码", r["audit"]["syntax_errors"][0]["error"])
        self.assertFalse((self.comp_dir / "gbk_comp.py").exists())


    def test_invalid_module_filename_in_package_rejected(self):
        pkg = self.root / "good_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL, encoding="utf-8")
        (pkg / "辅助模块.py").write_text("X = 1\n", encoding="utf-8")
        r = self.comp._cmd_install_local(str(pkg))
        self.assertFalse(r["success"])
        self.assertTrue(r["audit"]["syntax_errors"])

    def test_cell_only_in_private_module_rejected(self):
        # 组件类只在 _core.py（加载器会跳过该模块）→ 审查须判不通过
        pkg = self.root / "priv_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "_core.py").write_text(GOOD_CELL, encoding="utf-8")
        r = self.comp._cmd_install_local(str(pkg))
        self.assertFalse(r["success"])
        self.assertIn("审查未通过", r["error"])
        self.assertEqual(r["audit"]["cells"], [])
        self.assertTrue(any("跳过" in w for w in r["audit"]["warnings"]))

    def test_private_helper_module_ok(self):
        pkg = self.root / "good_pkg2"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("from .core import GoodComp\n", encoding="utf-8")
        (pkg / "core.py").write_text(GOOD_CELL.replace("good_comp", "good_pkg2"), encoding="utf-8")
        (pkg / "_utils.py").write_text("def noop():\n    return 1\n", encoding="utf-8")
        r = self.comp._cmd_install_local(str(pkg), dry_run=True)
        self.assertTrue(r["success"], r)
        self.assertEqual(len(r["audit"]["cells"]), 1)
        self.assertTrue(any("跳过" in w for w in r["audit"]["warnings"]))


class TestMarketStructures(InstallLocalBase):
    """市场包常见结构：src/ 嵌套、混入辅助文件"""

    def _zip(self, name, files):
        z = self.root / name
        with zipfile.ZipFile(z, "w") as zf:
            for rel, content in files.items():
                zf.writestr(rel, content)
        return z

    def test_src_layout_zip(self):
        z = self._zip("src_layout.zip", {
            "README.md": "# readme",
            "requirements.txt": "httpx\n",
            "src/my_cell.py": GOOD_CELL.replace("good_comp", "my_cell"),
        })
        r = self.comp._cmd_install_local(str(z))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["name"], "my_cell")
        self.assertEqual(r["kind"], "file")
        self.assertTrue((self.comp_dir / "my_cell.py").exists())

    def test_ignores_non_component_py(self):
        z = self._zip("mixed.zip", {
            "my_cell.py": GOOD_CELL.replace("good_comp", "my_cell"),
            "test_my_cell.py": "def test_x():\n    assert True\n",
            "setup.py": "from setuptools import setup\nsetup()\n",
        })
        r = self.comp._cmd_install_local(str(z))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["name"], "my_cell")

    def test_multiple_real_components_reported(self):
        z = self._zip("multi.zip", {
            "a.py": GOOD_CELL.replace("good_comp", "a_comp"),
            "b.py": GOOD_CELL.replace("good_comp", "b_comp"),
        })
        r = self.comp._cmd_install_local(str(z))
        self.assertFalse(r["success"])
        self.assertIn("多个组件", r["error"])
        self.assertEqual(sorted(r["candidates"]), ["a.py", "b.py"])

    def test_nested_package_found(self):
        z = self._zip("nested_pkg.zip", {
            "README.md": "# x",
            "src/good_pkg/__init__.py": "from .core import GoodComp\n",
            "src/good_pkg/core.py": GOOD_CELL.replace("good_comp", "good_pkg"),
        })
        r = self.comp._cmd_install_local(str(z))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["kind"], "package")
        self.assertEqual(r["name"], "good_pkg")
        self.assertTrue((self.comp_dir / "good_pkg" / "core.py").exists())

    def test_no_component_found(self):
        z = self._zip("nocomp.zip", {"helpers/util.py": "def f():\n    return 1\n"})
        r = self.comp._cmd_install_local(str(z))
        self.assertFalse(r["success"])
        self.assertIn("未找到组件", r["error"])


class TestLoaderNameMismatch(unittest.TestCase):
    """回归：类名与文件名不一致的单文件组件也能被热重载加载"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.comp_dir = Path(self._tmp.name)
        self._orig_get_dir = cb_mod.get_components_dir
        import app.core.util.components_loader as loader_mod
        self._loader = loader_mod
        self._orig_scan_dirs = loader_mod.get_scan_dirs
        cb_mod.get_components_dir = lambda: self.comp_dir
        loader_mod.get_scan_dirs = lambda: [self.comp_dir]

    def tearDown(self):
        cb_mod.get_components_dir = self._orig_get_dir
        self._loader.get_scan_dirs = self._orig_scan_dirs
        for key in list(sys.modules.keys()):
            if key.startswith("_component_") or key.startswith("components."):
                sys.modules.pop(key, None)
        self._tmp.cleanup()

    def test_instantiate_by_real_filename(self):
        # 文件名 install_probe.py，类名 InstallProbeCell（按类名推导会变成 install_probe_cell.py）
        src = self.comp_dir / "install_probe.py"
        src.write_text(GOOD_CELL.replace("GoodComp", "InstallProbeCell"), encoding="utf-8")

        from app.core.util.components_loader import _instantiate_component
        module_path = "components.install_probe.InstallProbeCell"
        instance, info = _instantiate_component(module_path, purge_cache=True)
        self.assertEqual(instance.cell_name, "good_comp")
        self.assertEqual(info["class_name"], "InstallProbeCell")
        self.assertTrue(info["is_new"])

    def test_missing_file_still_errors(self):
        from app.core.util.components_loader import _instantiate_component
        with self.assertRaises(ImportError):
            _instantiate_component("components.ghost_mod.GhostCell")


if __name__ == "__main__":
    unittest.main()

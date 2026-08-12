# -*- coding: utf-8 -*-
"""
入口文件顶层 import 测试。
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_TOP_LEVEL = {"os", "sys", "multiprocessing"}

ENTRY_FILES = [
    "main.py",
    "builder/embed_main.py",
]


def _top_level_imports(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def _heavy_imports(path: str) -> list[str]:
    imports = _top_level_imports(path)
    return [name for name in imports if name.split(".")[0] not in ALLOWED_TOP_LEVEL]


class TestEntryTopLevelImports(unittest.TestCase):
    def test_entry_files_keep_top_level_imports_light(self):
        for rel in ENTRY_FILES:
            path = os.path.join(ROOT, rel.replace("/", os.sep))
            self.assertTrue(os.path.exists(path), f"入口文件不存在: {rel}")
            heavy = _heavy_imports(path)
            self.assertEqual(
                [],
                heavy,
                f"{rel} 顶层不得 import 重模块（spawn 子进程会重新执行顶层）: {heavy}",
            )


if __name__ == "__main__":
    unittest.main()

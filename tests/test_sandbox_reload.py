# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, ".")

from app.core.util.component_sandbox import ComponentSandbox

COMPONENT_TEMPLATE = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class ReloadProbe(BaseCell):
    @property
    def cell_name(self):
        return "reload_probe"

    def _cmd_version(self):
        return "{version}"
'''


class TestSandboxReload(unittest.TestCase):

    NAME = "reload_probe"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.comp_path = os.path.join(self._tmp.name, "reload_probe.py")
        self._write_component("v1")

    def tearDown(self):
        ComponentSandbox.remove_sandbox(self.NAME)
        self._tmp.cleanup()

    def _write_component(self, version):
        with open(self.comp_path, "w", encoding="utf-8") as f:
            f.write(COMPONENT_TEMPLATE.format(version=version))

    def test_reload_loads_new_component_code(self):
        sandbox = ComponentSandbox.get_sandbox(self.NAME)
        sandbox.initialize(self.comp_path, "ReloadProbe")
        self.assertEqual(sandbox.execute("version"), "v1")

        time.sleep(0.3)
        self._write_component("v2")

        new_sandbox = ComponentSandbox.reload_sandbox(self.NAME)
        new_sandbox.initialize(self.comp_path, "ReloadProbe")
        self.assertEqual(new_sandbox.execute("version"), "v2")
        self.assertEqual(ComponentSandbox.get_sandbox(self.NAME).execute("version"), "v2")


if __name__ == "__main__":
    unittest.main()

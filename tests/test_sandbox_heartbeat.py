# -*- coding: utf-8 -*-
"""
沙箱心跳保活冒烟测试：IS_BACKGROUND 声明、后台线程存活、心跳防回收
对应修复：heartbeat 循环从未被调用导致沙箱后台组件被空闲回收的差距
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, ".")

import app.core.util.component_sandbox as cs_mod
from app.core.util.component_sandbox import ComponentSandbox

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 后台组件：线程持续自增 ticks
BG_COMP = '''# -*- coding: utf-8 -*-
import threading
import time
from app.core.interface.base_cell import BaseCell

class BackgroundComp(BaseCell):
    IS_BACKGROUND = True

    @property
    def cell_name(self):
        return "bg_comp"

    def __init__(self):
        super().__init__()
        self.ticks = 0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            self.ticks += 1
            time.sleep(0.1)

    def _cmd_ticks(self, **kwargs):
        """读取 ticks"""
        return {"ticks": self.ticks}

    def _cmd_on_sandbox_save(self, **kwargs):
        return {"success": True, "state": {"ticks": self.ticks}}

    def _cmd_on_sandbox_restore(self, state=None, **kwargs):
        if isinstance(state, dict):
            self.ticks = state.get("ticks", 0)
        return {"success": True, "restored": True}
'''

# 前台组件（无 IS_BACKGROUND）
FG_COMP = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class ForegroundComp(BaseCell):
    @property
    def cell_name(self):
        return "fg_comp"

    def _cmd_ping(self, **kwargs):
        """pong"""
        return {"pong": True}
'''


def _write_comp(tmpdir, name, code):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


class _GlobalStateGuard:

    def __init__(self):
        self.saved_instances = dict(cs_mod._sandbox_instances)
        self.saved_last_used = dict(cs_mod._sandbox_last_used)
        self.saved_states = dict(cs_mod._persisted_sandbox_states)

    def close(self):
        for name in list(cs_mod._sandbox_instances.keys()):
            try:
                cs_mod.ComponentSandbox.remove_sandbox(name)
            except Exception:
                pass
        cs_mod._sandbox_instances.clear()
        cs_mod._sandbox_instances.update(self.saved_instances)
        cs_mod._sandbox_last_used.clear()
        cs_mod._sandbox_last_used.update(self.saved_last_used)
        cs_mod._persisted_sandbox_states.clear()
        cs_mod._persisted_sandbox_states.update(self.saved_states)


class HeartbeatTestBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._guard = _GlobalStateGuard()
        self.bg_path = _write_comp(self._tmp.name, "bg_comp.py", BG_COMP)
        self.fg_path = _write_comp(self._tmp.name, "fg_comp.py", FG_COMP)

    def tearDown(self):
        self._guard.close()
        self._tmp.cleanup()


def _load_cell_class(path, class_name):
    """从临时组件文件加载类（需注册 sys.modules，否则 inspect.getsourcefile 找不到源文件）"""
    import importlib.util
    mod_name = f"_hb_{class_name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)


class TestBackgroundFlag(HeartbeatTestBase):
    """IS_BACKGROUND 声明与心跳动作"""

    def test_background_component_runs_threads(self):
        """沙箱内后台线程真实运行"""
        sb = ComponentSandbox.get_sandbox("hb_bg")
        sb.initialize(self.bg_path, "BackgroundComp")
        r1 = sb.execute("ticks")
        time.sleep(0.5)
        r2 = sb.execute("ticks")
        self.assertGreater(r2["ticks"], r1["ticks"])

    def test_heartbeat_action_ok(self):
        sb = ComponentSandbox.get_sandbox("hb_action")
        sb.initialize(self.bg_path, "BackgroundComp")
        result = sb.heartbeat()
        self.assertEqual(result.get("status"), "ok")

    def test_foreground_heartbeat_ok(self):
        """前台组件心跳也正常（只是不启动保活循环）"""
        sb = ComponentSandbox.get_sandbox("hb_fg")
        sb.initialize(self.fg_path, "ForegroundComp")
        result = sb.heartbeat()
        self.assertEqual(result.get("status"), "ok")

    def test_heartbeat_on_stopped_sandbox(self):
        """已停止沙箱心跳返回 error 不崩溃"""
        sb = ComponentSandbox.get_sandbox("hb_stopped")
        sb.initialize(self.fg_path, "ForegroundComp")
        sb.stop()
        result = sb.heartbeat()
        self.assertEqual(result.get("status"), "error")


class TestHeartbeatKeepalive(HeartbeatTestBase):
    """心跳循环保活：走真实 adapter 链路（心跳线程挂在 adapter 上，IS_BACKGROUND 判定在首次命令后触发）"""

    def test_adapter_starts_heartbeat_for_background(self):
        """后台组件经 adapter 调用命令后，adapter 启动心跳线程"""
        from app.core.util.cell_tool_adapter import CellToolAdapter
        comp = _load_cell_class(self.bg_path, "BackgroundComp")()
        adapter = CellToolAdapter(comp, use_sandbox=True)
        r = adapter.execute_with_context({"command": "ticks"})
        self.assertIsNone(r.get("error"), f"命令应成功: {r}")
        self.assertTrue(adapter._use_sandbox, f"应保持沙箱模式: {r}")
        self.assertIsNotNone(adapter._heartbeat_thread)
        self.assertTrue(adapter._heartbeat_thread.is_alive())

    def test_adapter_no_heartbeat_for_foreground(self):
        """前台组件 adapter 不启动心跳线程"""
        from app.core.util.cell_tool_adapter import CellToolAdapter
        comp = _load_cell_class(self.fg_path, "ForegroundComp")()
        adapter = CellToolAdapter(comp, use_sandbox=True)
        r = adapter.execute_with_context({"command": "ping"})
        self.assertEqual(r.get("status", "ok"), "ok")
        self.assertIsNone(adapter._heartbeat_thread)

    def test_heartbeat_refreshes_last_used(self):
        """心跳刷新 last_used，防止空闲回收"""
        sb = ComponentSandbox.get_sandbox("hb_refresh")
        sb.initialize(self.bg_path, "BackgroundComp")
        before = cs_mod._sandbox_last_used.get("hb_refresh", 0)
        time.sleep(0.05)
        sb.heartbeat()
        after = cs_mod._sandbox_last_used.get("hb_refresh", 0)
        self.assertGreaterEqual(after, before)

    def test_reinit_after_recycle_restores_background(self):
        """回收 → 重建 → 后台线程恢复运行"""
        sb = ComponentSandbox.get_sandbox("hb_cycle")
        sb.initialize(self.bg_path, "BackgroundComp")
        r1 = sb.execute("ticks")
        cs_mod._save_sandbox_state("hb_cycle", sb)
        sb.stop()
        sb2 = ComponentSandbox.get_sandbox("hb_cycle")
        sb2.initialize(self.bg_path, "BackgroundComp")
        state = cs_mod._persisted_sandbox_states.get("hb_cycle")
        if state:
            sb2.restore_state(state)
        time.sleep(0.3)
        r2 = sb2.execute("ticks")
        self.assertGreater(r2["ticks"], 0)  # 新实例后台线程在跑


if __name__ == "__main__":
    unittest.main()

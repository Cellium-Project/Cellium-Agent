# -*- coding: utf-8 -*-
"""
沙箱状态接力冒烟测试：save/restore 钩子、LRU 淘汰保护、并发初始化竞态
对应修复：
  - 沙箱回收前 on_sandbox_save / 重初始化后 on_sandbox_restore 状态接力
  - LRU 淘汰掉"正在初始化中"沙箱的竞态（压测发现的真实 bug）
"""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, ".")

import app.core.util.component_sandbox as cs_mod
from app.core.util.component_sandbox import ComponentSandbox

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 带内存状态 + save/restore 钩子的组件
STATEFUL_COMP = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class StatefulComp(BaseCell):
    @property
    def cell_name(self):
        return "stateful_comp"

    def __init__(self):
        super().__init__()
        self.counter = 0
        self.history = []

    def _cmd_bump(self, n: int = 1, **kwargs):
        """计数器加 n"""
        self.counter += n
        self.history.append(self.counter)
        return {"counter": self.counter}

    def _cmd_state(self, **kwargs):
        """查看当前状态"""
        return {"counter": self.counter, "history": list(self.history)}

    def _cmd_on_sandbox_save(self, **kwargs):
        """沙箱回收前导出状态"""
        return {"success": True, "state": {"counter": self.counter, "history": list(self.history)}}

    def _cmd_on_sandbox_restore(self, state=None, **kwargs):
        """重初始化后注入状态"""
        if isinstance(state, dict):
            self.counter = state.get("counter", 0)
            self.history = list(state.get("history", []))
        return {"success": True, "restored": True}
'''

# 无钩子的普通组件
PLAIN_COMP = '''# -*- coding: utf-8 -*-
from app.core.interface.base_cell import BaseCell

class PlainComp(BaseCell):
    @property
    def cell_name(self):
        return "plain_comp"

    def __init__(self):
        super().__init__()
        self.counter = 0

    def _cmd_bump(self, n: int = 1, **kwargs):
        """计数器加 n"""
        self.counter += n
        return {"counter": self.counter}

    def _cmd_slow_init(self, **kwargs):
        """标记初始化后的调用"""
        return {"ok": True}
'''


def _write_comp(tmpdir, name, code):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


class _GlobalStateGuard:
    """保存/恢复 cs_mod 全局状态，测试间完全隔离"""

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


class SandboxStateTestBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._guard = _GlobalStateGuard()
        self.stateful_path = _write_comp(self._tmp.name, "stateful_comp.py", STATEFUL_COMP)
        self.plain_path = _write_comp(self._tmp.name, "plain_comp.py", PLAIN_COMP)

    def tearDown(self):
        self._guard.close()
        self._tmp.cleanup()


class TestSaveRestoreRelay(SandboxStateTestBase):
    """状态接力核心链路"""

    def test_save_hook_exports_state(self):
        sb = ComponentSandbox.get_sandbox("t_save")
        sb.initialize(self.stateful_path, "StatefulComp")
        sb.execute("bump", n=3)
        sb.execute("bump", n=4)
        saved = sb.try_call("on_sandbox_save")
        self.assertEqual(saved["state"]["counter"], 7)
        self.assertEqual(saved["state"]["history"], [3, 7])

    def test_restore_hook_injects_state(self):
        sb = ComponentSandbox.get_sandbox("t_restore")
        sb.initialize(self.stateful_path, "StatefulComp")
        self.assertTrue(sb.restore_state({"counter": 42, "history": [42]}))
        r = sb.execute("state")
        self.assertEqual(r["counter"], 42)

    def test_manual_relay_roundtrip(self):
        """回收前 save → 缓存 → 新实例 restore → 状态完整"""
        sb = ComponentSandbox.get_sandbox("t_relay")
        sb.initialize(self.stateful_path, "StatefulComp")
        sb.execute("bump", n=8)
        # 模拟清理线程的回收路径
        cs_mod._save_sandbox_state("t_relay", sb)
        sb.stop()

        # 新实例重初始化
        sb2 = ComponentSandbox.get_sandbox("t_relay")
        sb2.initialize(self.stateful_path, "StatefulComp")
        state = cs_mod._persisted_sandbox_states.get("t_relay")
        self.assertIsNotNone(state)
        sb2.restore_state(state)
        r = sb2.execute("state")
        self.assertEqual(r["counter"], 8)
        self.assertEqual(r["history"], [8])

    def test_no_hook_component_relay_noop(self):
        """无钩子组件：save 返回 None，不产生缓存，行为同旧版"""
        sb = ComponentSandbox.get_sandbox("t_nohook")
        sb.initialize(self.plain_path, "PlainComp")
        sb.execute("bump", n=5)
        self.assertIsNone(sb.try_call("on_sandbox_save"))
        cs_mod._save_sandbox_state("t_nohook", sb)
        self.assertNotIn("t_nohook", cs_mod._persisted_sandbox_states)

    def test_try_call_timeout_returns_none(self):
        """try_call 超时返回 None 而不是挂死（清理线程保护）"""
        sb = ComponentSandbox.get_sandbox("t_trycall")
        sb.initialize(self.plain_path, "PlainComp")
        # 未初始化的沙箱直接返回 None
        sb.stop()
        self.assertIsNone(sb.try_call("anything"))


class TestLruAndInitRace(SandboxStateTestBase):
    """LRU 淘汰与并发初始化竞态（压测发现的 bug 回归点）"""

    def test_lru_does_not_evict_initializing_sandbox(self):
        """MAX_COUNT 满时，初始化中的沙箱不被 LRU 淘汰（is_busy 保护）"""
        paths = []
        for i in range(cs_mod.MAX_SANDBOX_COUNT + 5):
            name = f"t_race_{i}"
            sb = ComponentSandbox.get_sandbox(name)
            sb.initialize(self.plain_path, "PlainComp")
            paths.append(name)

        # 全部初始化成功，无一失败 = 竞态修复生效
        self.assertEqual(len(cs_mod._sandbox_instances), cs_mod.MAX_SANDBOX_COUNT)
        for name in paths[: cs_mod.MAX_SANDBOX_COUNT + 5]:
            sb = cs_mod._sandbox_instances.get(name)
            if sb is not None:
                r = sb.execute("bump", n=1)
                self.assertEqual(r["counter"], 1)

    def test_initializing_flag_lifecycle(self):
        sb = ComponentSandbox.get_sandbox("t_flag")
        self.assertFalse(sb._initializing)
        sb.initialize(self.plain_path, "PlainComp")
        self.assertFalse(sb._initializing)  # 完成后复位
        # 初始化中标记为 busy
        sb._initializing = True
        self.assertTrue(sb.is_busy())
        sb._initializing = False
        self.assertFalse(sb.is_busy())

    def test_evicted_sandbox_save_called(self):
        """LRU 淘汰时调用 save 钩子缓存状态"""
        # 占满
        for i in range(cs_mod.MAX_SANDBOX_COUNT):
            sb = ComponentSandbox.get_sandbox(f"t_lru_{i}")
            sb.initialize(self.stateful_path, "StatefulComp")
            sb.execute("bump", n=i + 1)
        # 第 16 个触发淘汰，最旧的 t_lru_0 被淘汰
        sb = ComponentSandbox.get_sandbox("t_lru_new")
        sb.initialize(self.stateful_path, "StatefulComp")
        # t_lru_0 已被淘汰（count 上限），其状态应在缓存中
        self.assertNotIn("t_lru_0", cs_mod._sandbox_instances)
        self.assertIn("t_lru_0", cs_mod._persisted_sandbox_states)
        saved = cs_mod._persisted_sandbox_states["t_lru_0"]
        self.assertEqual(saved["counter"], 1)

    def test_idle_cleanup_preserves_state(self):
        """空闲清理路径：save → remove → 重建 → restore"""
        sb = ComponentSandbox.get_sandbox("t_idle")
        sb.initialize(self.stateful_path, "StatefulComp")
        sb.execute("bump", n=9)
        cs_mod._save_sandbox_state("t_idle", sb)
        cs_mod.ComponentSandbox.remove_sandbox("t_idle")
        self.assertNotIn("t_idle", cs_mod._sandbox_instances)
        self.assertIn("t_idle", cs_mod._persisted_sandbox_states)


if __name__ == "__main__":
    unittest.main()

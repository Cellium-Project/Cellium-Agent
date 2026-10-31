# -*- coding: utf-8 -*-
"""
沙箱 IPC 冒烟测试：req_id 配对、超时错位防护、并发安全
对应修复：超时后迟到响应污染下一条命令的错位 bug
"""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, ".")

from app.core.util.component_sandbox import SandboxProcess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 简单组件：快速命令 + 可控耗时命令
SIMPLE_COMP = '''# -*- coding: utf-8 -*-
import time
from app.core.interface.base_cell import BaseCell

class SimpleComp(BaseCell):
    @property
    def cell_name(self):
        return "simple_comp"

    def _cmd_echo(self, marker: str = "", **kwargs):
        """回显 marker"""
        return {"marker": marker}

    def _cmd_slow(self, seconds: float = 2.0, **kwargs):
        """耗时 seconds 后返回 slept"""
        time.sleep(seconds)
        return {"slept": seconds}

    def _cmd_bad_result(self, **kwargs):
        """返回不可序列化对象"""
        import threading
        return {"lock": threading.Lock()}
'''


def _write_comp(tmpdir, name, code):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


class SandboxIPCTestBase(unittest.TestCase):
    """公共 setUp/tearDown：临时目录 + 沙箱生命周期"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.comp_path = _write_comp(self._tmp.name, "simple_comp.py", SIMPLE_COMP)

    def tearDown(self):
        ipc = getattr(self, "ipc", None)
        if ipc is not None:
            try:
                ipc.stop()
            except Exception:
                pass
        self._tmp.cleanup()

    def _start(self, timeout=10):
        self.ipc = SandboxProcess(timeout=10)
        self.ipc.start(self.comp_path, "SimpleComp")
        self.ipc._timeout = timeout  # 初始化完成后单独收紧执行超时
        return self.ipc


class TestReqIdPairing(SandboxIPCTestBase):
    """请求/响应按 req_id 配对"""

    def test_echo_returns_own_marker(self):
        ipc = self._start()
        for i in range(10):
            r = ipc.execute("echo", marker=f"m{i}")
            self.assertEqual(r["marker"], f"m{i}")

    def test_request_seq_increments(self):
        ipc = self._start()
        before = ipc._request_seq
        ipc.execute("echo", marker="x")
        self.assertEqual(ipc._request_seq, before + 1)


class TestTimeoutMisalignment(SandboxIPCTestBase):
    """超时后迟到响应不污染下一条命令（核心回归点）"""

    def test_stale_response_dropped(self):
        ipc = self._start(timeout=0.5)
        # 1.2s 慢命令 + 0.5s 超时 → 主进程超时，慢命令结果稍后到达
        with self.assertRaises(Exception):
            ipc.execute("slow", seconds=1.2)
        time.sleep(1.0)  # 等迟到响应进队列（略大于慢命令时长）
        # 下一条命令必须拿到自己的结果，而不是 {'slept': 1.2}
        r = ipc.execute("echo", marker="after_timeout")
        self.assertEqual(r["marker"], "after_timeout")

    def test_stale_dropped_repeatedly(self):
        ipc = self._start(timeout=0.5)
        # 连续两次超时 + 验证，陈旧响应逐个清理无残留
        for rnd in range(2):
            with self.assertRaises(Exception):
                ipc.execute("slow", seconds=1.2)
            time.sleep(1.0)
            r = ipc.execute("echo", marker=f"round{rnd}")
            self.assertEqual(r["marker"], f"round{rnd}")

    def test_command_error_no_misalignment(self):
        ipc = self._start()
        # 命令抛异常 → 响应也带 req_id，不影响后续命令
        with self.assertRaises(RuntimeError):
            ipc.execute("nonexistent_cmd")
        r = ipc.execute("echo", marker="ok")
        self.assertEqual(r["marker"], "ok")


class TestErrorPaths(SandboxIPCTestBase):
    """错误路径：不可序列化结果立即报错且不污染后续命令"""

    def test_unpicklable_result_raises_quickly(self):
        ipc = self._start()
        t0 = time.time()
        with self.assertRaises(RuntimeError) as ctx:
            ipc.execute("bad_result")
        # 立即收到明确错误，而非挂满超时
        self.assertIn("不可序列化", str(ctx.exception))
        self.assertLess(time.time() - t0, 5)

    def test_after_unpicklable_next_command_ok(self):
        ipc = self._start()
        with self.assertRaises(RuntimeError):
            ipc.execute("bad_result")
        r = ipc.execute("echo", marker="still_ok")
        self.assertEqual(r["marker"], "still_ok")


class TestConcurrentIpc(SandboxIPCTestBase):
    """多线程并发调用同一沙箱：_ipc_lock 互斥 + req_id 配对不串"""

    def test_threads_get_own_results(self):
        ipc = self._start()
        errors = []
        results = {}

        def worker(tid):
            try:
                for i in range(10):
                    marker = f"t{tid}_i{i}"
                    r = ipc.execute("echo", marker=marker)
                    if r.get("marker") != marker:
                        errors.append(f"tid={tid} got {r}")
                    else:
                        results[f"t{tid}_i{i}"] = True
            except Exception as e:
                errors.append(f"tid={tid} exc: {e}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 80)

    def test_concurrent_mixed_slow_and_fast(self):
        ipc = self._start(timeout=5)
        errors = []

        def slow_worker():
            try:
                r = ipc.execute("slow", seconds=0.3)
                assert r["slept"] == 0.3
            except Exception as e:
                errors.append(f"slow: {e}")

        def fast_worker():
            try:
                for i in range(10):
                    r = ipc.execute("echo", marker=f"f{i}")
                    if r.get("marker") != f"f{i}":
                        errors.append(f"fast got {r}")
            except Exception as e:
                errors.append(f"fast: {e}")

        threads = [threading.Thread(target=slow_worker)]
        threads += [threading.Thread(target=fast_worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestSandboxLifecycle(SandboxIPCTestBase):
    """生命周期边界"""

    def test_not_initialized_raises(self):
        ipc = SandboxProcess()
        with self.assertRaises(RuntimeError):
            ipc.execute("echo")

    def test_command_metadata(self):
        ipc = self._start()
        meta = ipc.get_commands_meta()
        self.assertIn("echo", meta)
        self.assertIn("slow", meta)
        self.assertTrue(ipc.has_method("_cmd_echo"))
        self.assertFalse(ipc.has_method("_cmd_nonexistent"))
        self.assertTrue(ipc.ping())

    def test_worker_crash_detected(self):
        ipc = self._start()
        self.assertTrue(ipc.ping())
        # 杀死 worker 进程
        ipc._process.terminate()
        ipc._process.join(timeout=3)
        time.sleep(0.2)
        self.assertFalse(ipc.ping())


if __name__ == "__main__":
    unittest.main()

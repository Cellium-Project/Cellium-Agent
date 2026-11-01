# -*- coding: utf-8 -*-
import multiprocessing
import os
import socket
import sys
import unittest

sys.path.insert(0, ".")

from app.core.util.process_context import get_process_context


def _scan_listening_fds(result_queue):
    listeners = []
    try:
        entries = os.listdir("/proc/self/fd")
    except OSError as e:
        result_queue.put({"error": f"/proc/self/fd 不可读: {e}"})
        return

    for entry in entries:
        try:
            fd = int(entry)
        except ValueError:
            continue

        probe = None
        is_listen = False
        try:
            probe = socket.socket(fileno=fd)
            is_listen = probe.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        except Exception:
            is_listen = False
        finally:
            if probe is not None:
                try:
                    probe.detach()
                except Exception:
                    pass

        if is_listen:
            listeners.append(fd)

    result_queue.put({"fds": listeners})


class TestProcessContext(unittest.TestCase):

    def test_start_method_by_platform(self):
        ctx = get_process_context()
        if sys.platform == "win32":
            self.assertEqual(ctx.get_start_method(), "spawn")
        else:
            try:
                multiprocessing.get_context("forkserver")
            except ValueError:
                self.skipTest("forkserver 不可用")
            self.assertEqual(ctx.get_start_method(), "forkserver")

    def test_context_cached(self):
        self.assertIs(get_process_context(), get_process_context())


class TestNoInheritedListener(unittest.TestCase):

    @unittest.skipUnless(sys.platform.startswith("linux"), "依赖 /proc/self/fd，仅 Linux 可验证")
    def test_child_has_no_listen_socket(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)

        ctx = get_process_context()
        result_queue = ctx.Queue()
        proc = ctx.Process(target=_scan_listening_fds, args=(result_queue,))
        proc.start()
        try:
            result = result_queue.get(timeout=60)
        finally:
            proc.join(timeout=10)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)
            listener.close()
            result_queue.close()

        self.assertNotIn("error", result)
        self.assertEqual(result.get("fds"), [])


if __name__ == "__main__":
    unittest.main()

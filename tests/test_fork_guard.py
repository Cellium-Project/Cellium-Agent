# -*- coding: utf-8 -*-
import socket
import sys
import unittest

from app.core.util import fork_guard


class ForkGuardInstallTest(unittest.TestCase):

    def test_install_does_not_raise(self):
        self.assertIsInstance(fork_guard.install(), bool)


@unittest.skipUnless(sys.platform.startswith("linux"), "fork_guard 仅在 Linux 生效")
class ForkGuardCloseTest(unittest.TestCase):

    def test_closes_listen_socket_keeps_connected_socket(self):
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen.bind(("127.0.0.1", 0))
        listen.listen(1)

        left, right = socket.socketpair()

        fork_guard._close_inherited_listeners()

        with self.assertRaises(OSError):
            listen.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        listen.detach()

        left.send(b"x")
        self.assertEqual(right.recv(1), b"x")

        left.close()
        right.close()


if __name__ == "__main__":
    unittest.main()

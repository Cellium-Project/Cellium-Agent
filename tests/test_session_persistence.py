# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest

from app.agent.loop.session_store import SessionStore


class TestClientLastSession(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = SessionStore(
            store_path=os.path.join(self.tmp, "sessions.json"),
            archive_dir=os.path.join(self.tmp, "archive"),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_per_client_last_session(self):
        self.store.get_or_create_session("s_a")
        self.store.get_or_create_session("s_b")
        self.store.set_client_last_session("s_a", "webui")
        self.store.set_client_last_session("s_b", "tui")
        self.assertEqual(self.store.get_client_last_session("webui"), "s_a")
        self.assertEqual(self.store.get_client_last_session("tui"), "s_b")
        self.assertIsNone(self.store.get_client_last_session(""))

    def test_unknown_channel_and_missing_session(self):
        self.store.get_or_create_session("s_only")
        self.assertIsNone(self.store.get_client_last_session("webui"))
        self.store.set_client_last_session("s_only", "webui")
        self.assertEqual(self.store.get_client_last_session("webui"), "s_only")

    def test_delete_session_drops_client_pointer(self):
        self.store.get_or_create_session("s_x")
        self.store.set_client_last_session("s_x", "webui")
        self.assertTrue(self.store.delete_session("s_x"))
        self.assertIsNone(self.store.get_client_last_session("webui"))


if __name__ == "__main__":
    unittest.main()

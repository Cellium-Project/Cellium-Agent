# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from app.server.routes import chat


class FakeTitleStore:
    def __init__(self, sessions=()):
        self.sessions = set(sessions)
        self.calls = []

    def session_exists(self, session_id):
        return session_id in self.sessions

    def set_session_title(self, session_id, title):
        self.calls.append((session_id, title))


class TestRenameSessionApi(unittest.TestCase):

    def _rename(self, store, session_id, title):
        with patch.object(chat, "get_session_store", return_value=store):
            return asyncio.run(chat.rename_session(session_id, chat.RenameSessionRequest(title=title)))

    def test_blank_title_rejected(self):
        store = FakeTitleStore({"s1"})
        for bad in ("", "   ", "\x00\n\t"):
            with self.assertRaises(HTTPException) as ctx:
                self._rename(store, "s1", bad)
            self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(store.calls, [])

    def test_unknown_session_rejected(self):
        store = FakeTitleStore()
        with self.assertRaises(HTTPException) as ctx:
            self._rename(store, "s404", "名字")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(store.calls, [])

    def test_title_sanitized_and_limited(self):
        store = FakeTitleStore({"s1"})
        result = self._rename(store, "s1", "  新\n 名字  ")
        self.assertEqual(result["title"], "新 名字")
        self.assertEqual(store.calls[-1], ("s1", "新 名字"))

        long = self._rename(store, "s1", "x" * 80)
        self.assertEqual(len(long["title"]), 60)


if __name__ == "__main__":
    unittest.main()

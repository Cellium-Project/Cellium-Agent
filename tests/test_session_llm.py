# -*- coding: utf-8 -*-
"""会话级 LLM 模型隔离"""
import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import session_llm
from app.agent.loop.agent_loop_manager import AgentLoopManager, LoopMetadata

MODELS = [
    {"name": "model-a", "model": "ma", "base_url": "https://a"},
    {"name": "model-b", "model": "mb", "base_url": "https://b"},
]


class TestSessionLlmStore(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_path = session_llm.STORE_PATH
        session_llm.STORE_PATH = os.path.join(self.tmp, "session_llm.json")
        with session_llm._LOCK:
            session_llm._OVERRIDES.clear()
            session_llm._ENGINES.clear()

    def tearDown(self):
        session_llm.STORE_PATH = self._old_path
        with session_llm._LOCK:
            session_llm._OVERRIDES.clear()
            session_llm._ENGINES.clear()

    def test_set_get_clear_and_persist(self):
        with patch.object(session_llm, "list_models", return_value=MODELS), \
             patch.object(session_llm, "global_model", return_value="model-a"):
            result = session_llm.set_model("s1", "model-b")
            self.assertTrue(result["success"])
            self.assertTrue(result["override"])
            self.assertEqual(session_llm.get_model("s1"), "model-b")
            self.assertTrue(session_llm.has_override("s1"))

            # 重新从磁盘加载
            session_llm._OVERRIDES.clear()
            session_llm._OVERRIDES.update(session_llm._load())
            self.assertEqual(session_llm.get_model("s1"), "model-b")

            result = session_llm.set_model("s1", "")
            self.assertTrue(result["success"])
            self.assertFalse(result["override"])
            self.assertFalse(session_llm.has_override("s1"))

    def test_invalid_model_rejected(self):
        with patch.object(session_llm, "list_models", return_value=MODELS):
            result = session_llm.set_model("s1", "nope")
        self.assertFalse(result["success"])
        self.assertIn("不存在", result["error"])

    def test_resolve_engine_isolated_and_cached(self):
        fake = object()
        with patch.object(session_llm, "get_model",
                          side_effect=lambda sid: "model-b" if sid == "s1" else ""), \
             patch.object(session_llm, "_build_engine", return_value=fake) as build:
            self.assertIsNone(session_llm.resolve_engine("s2"))
            self.assertIs(session_llm.resolve_engine("s1"), fake)
            self.assertIs(session_llm.resolve_engine("s1"), fake)
            self.assertEqual(build.call_count, 1)
            session_llm.drop_engine("s1")
            self.assertIs(session_llm.resolve_engine("s1"), fake)
            self.assertEqual(build.call_count, 2)


class TestReadyModels(unittest.TestCase):

    def test_unready_entries_hidden_and_global_falls_back(self):
        section = {
            "current_model": "model-1",
            "models": [
                {"name": "model-1", "api_key": "", "base_url": "", "model": ""},
                {"name": "ok", "api_key": "k", "base_url": "https://x", "model": "m"},
            ],
        }

        class _Cfg:
            def get_section(self, name):
                return section

        with patch("app.core.util.agent_config.get_config", return_value=_Cfg()):
            names = [m["name"] for m in session_llm.list_models()]
            self.assertEqual(names, ["ok"])
            self.assertEqual(session_llm.global_model(), "ok")

    def test_get_model_ignores_removed_model(self):
        with patch.object(session_llm, "list_models", return_value=MODELS):
            session_llm.set_model("s1", "model-b")
        with patch.object(session_llm, "list_models", return_value=[MODELS[0]]):
            self.assertEqual(session_llm.get_model("s1"), "")
            self.assertFalse(session_llm.has_override("s1"))


class TestSyncCurrentModel(unittest.TestCase):

    class _FakeCfg:
        def __init__(self, section):
            self.section = section

        def get_section(self, name):
            return self.section

        def set(self, key, value, persist=False):
            if key == "llm.models":
                self.section["models"] = value
            elif key == "llm.current_model":
                self.section["current_model"] = value

    def _sync(self, section, name, add=True):
        from app.server.routes import config as config_route
        with patch.object(config_route, "_get_agent_config", return_value=self._FakeCfg(section)):
            config_route._sync_model_to_llm_config(
                name,
                {"name": name, "api_key": "k", "base_url": "https://x", "model": "m"},
                add=add,
            )

    def test_add_model_promotes_current_when_placeholder_invalid(self):
        section = {
            "current_model": "model-1",
            "models": [{"name": "model-1", "api_key": "", "base_url": "", "model": ""}],
        }
        self._sync(section, "real")
        self.assertEqual(section["current_model"], "real")

    def test_add_model_keeps_valid_current(self):
        section = {
            "current_model": "a",
            "models": [{"name": "a", "api_key": "k", "base_url": "https://a", "model": "ma"}],
        }
        self._sync(section, "b")
        self.assertEqual(section["current_model"], "a")


class TestManagerIsolation(unittest.TestCase):

    def test_create_loop_uses_session_engine(self):
        mgr = AgentLoopManager()
        global_engine = object()
        session_engine = object()
        mgr._llm_engine = global_engine
        mgr._global_config = {}
        mgr._intent_enabled = False
        mgr._tools = None
        mgr._three_layer_memory = None
        created = {}

        def _fake_loop(**kwargs):
            created.update(kwargs)
            return SimpleNamespace(**kwargs)

        with patch.object(session_llm, "resolve_engine", return_value=session_engine), \
             patch("app.agent.loop.agent_loop.AgentLoop", side_effect=_fake_loop), \
             patch("app.agent.tools.shell_tool.ShellTool", return_value=object()):
            mgr._create_loop("s1")

        self.assertIs(created["llm_engine"], session_engine)

    def test_global_switch_skips_override_sessions(self):
        mgr = AgentLoopManager()
        old, new = object(), object()
        mgr._llm_engine = old
        loop_a = SimpleNamespace(llm=old)
        loop_b = SimpleNamespace(llm=old)
        mgr._loops = {
            "a": LoopMetadata(session_id="a", created_at=0, last_active=0, agent_loop=loop_a),
            "b": LoopMetadata(session_id="b", created_at=0, last_active=0, agent_loop=loop_b),
        }
        with patch.object(session_llm, "has_override", side_effect=lambda sid: sid == "a"):
            updated = mgr.update_llm_engine(new)
        self.assertEqual(updated, 1)
        self.assertIs(loop_a.llm, old)
        self.assertIs(loop_b.llm, new)

    def test_apply_session_engine_to_loop(self):
        mgr = AgentLoopManager()
        global_engine, session_engine = object(), object()
        mgr._llm_engine = global_engine
        loop = SimpleNamespace(llm=global_engine)
        mgr._loops = {"a": LoopMetadata(session_id="a", created_at=0, last_active=0, agent_loop=loop)}
        self.assertEqual(mgr.apply_session_engine("a", session_engine), 1)
        self.assertIs(loop.llm, session_engine)
        self.assertEqual(mgr.apply_session_engine("a", None), 1)
        self.assertIs(loop.llm, global_engine)


class TestSessionModelApi(unittest.TestCase):

    def test_get_and_update(self):
        from app.server.routes import chat

        with patch.object(session_llm, "list_models", return_value=MODELS), \
             patch.object(session_llm, "global_model", return_value="model-a"), \
             patch.object(session_llm, "get_model", return_value="model-b"), \
             patch.object(session_llm, "has_override", return_value=True), \
             patch.object(session_llm, "set_model",
                          return_value={"success": True, "session_id": "s1",
                                        "model": "model-b", "override": True}), \
             patch.object(session_llm, "apply_to_loop"):
            data = asyncio.run(chat.get_session_model("s1"))
            self.assertEqual(data["model"], "model-b")
            self.assertTrue(data["override"])
            self.assertEqual(len(data["models"]), 2)
            result = asyncio.run(chat.update_session_model(
                "s1", chat.SessionModelRequest(model="model-b")
            ))
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["model"], "model-b")


if __name__ == "__main__":
    unittest.main()

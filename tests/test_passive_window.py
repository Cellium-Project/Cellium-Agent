# -*- coding: utf-8 -*-
"""
QQ 群聊被动回复窗口测试：群级窗口、按 msg_id 计数、用户级回退
重点回归：preferred 失效时优先挂同一触发者的消息，而不是群最新消息（可能来自陌生人）
"""
import time
import unittest

from app.channels.qq.passive_window import (
    PassiveWindowTracker,
    PASSIVE_MAX_REPLIES,
    PASSIVE_WINDOW_SECONDS,
    PENDING_TTL_SECONDS,
    QUEUE_MODE_SECONDS,
)


class PassiveWindowTestBase(unittest.TestCase):

    def setUp(self):
        self.t = PassiveWindowTracker()

    def _msg(self, group, msg_id, user_id="", age=0.0):
        """注入一条入站消息，age 控制消息多旧"""
        self.t.note_inbound(group, msg_id, user_id)
        if age:
            for d in (self.t._windows, ):
                if group in d:
                    d[group]["received_at"] -= age
            if (group, user_id) in self.t._user_windows:
                self.t._user_windows[(group, user_id)]["received_at"] -= age


class TestGroupWindow(PassiveWindowTestBase):
    """群级窗口基础行为"""

    def test_resolve_preferred_valid(self):
        self._msg("g1", "mA", "uA")
        self.assertEqual(self.t.resolve("g1", "mA"), "mA")

    def test_resolve_latest_when_no_preferred(self):
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        self.assertEqual(self.t.resolve("g1"), "mB")

    def test_window_expiry(self):
        self._msg("g1", "mA", "uA", age=PASSIVE_WINDOW_SECONDS + 1)
        self.assertEqual(self.t.resolve("g1", "mA"), "")

    def test_max_replies_per_msg_id(self):
        self._msg("g1", "mA", "uA")
        for _ in range(PASSIVE_MAX_REPLIES):
            self.t.mark_used("mA")
        self.assertEqual(self.t.resolve("g1", "mA"), "")

    def test_mark_exhausted_blocks_window(self):
        self._msg("g1", "mA", "uA")
        self.t.mark_exhausted("mA")
        self.assertEqual(self.t.resolve("g1", "mA"), "")

    def test_remaining_counts(self):
        self._msg("g1", "mA", "uA")
        self.assertEqual(self.t.remaining("g1", "mA"), PASSIVE_MAX_REPLIES)
        self.t.mark_used("mA")
        self.assertEqual(self.t.remaining("g1", "mA"), PASSIVE_MAX_REPLIES - 1)


class TestUserFallback(PassiveWindowTestBase):
    """核心回归：preferred 失效只挂同一触发者，匹配不到就不发送"""

    def test_user_window_precedence(self):
        # A 发过 mA；B 发 mB 覆盖群窗口；A 的 preferred 无效 → 回退到 A 的 mA
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        self.assertEqual(self.t.resolve("g1", "nonexistent", "uA"), "mA")
        self.assertEqual(self.t.resolve("g1", "", "uA"), "mA")

    def test_no_match_returns_empty(self):
        # A 的消息耗尽 → 用户窗口无效 → 返回空（不发送，走排队兜底），绝不挂到 B 的消息
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        self.t.mark_exhausted("mA")
        self.assertEqual(self.t.resolve("g1", "mA", "uA"), "")

    def test_user_window_expiry_returns_empty(self):
        self._msg("g1", "mA", "uA", age=PASSIVE_WINDOW_SECONDS + 1)
        self._msg("g1", "mB", "uB")
        self.assertEqual(self.t.resolve("g1", "", "uA"), "")

    def test_unknown_user_returns_empty(self):
        # A 从未发言（无用户窗口）→ 不发送
        self._msg("g1", "mB", "uB")
        self.assertEqual(self.t.resolve("g1", "gone", "uA"), "")

    def test_no_user_id_falls_back_to_group(self):
        # 无 user_id（主动消息等场景）保留群级回退
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        self.assertEqual(self.t.resolve("g1", "gone"), "mB")


class TestPendingQueue(PassiveWindowTestBase):
    """排队补发行为"""

    def test_enqueue_and_peek(self):
        self.t.enqueue("g1", "hello")
        self.assertTrue(self.t.has_pending("g1"))
        self.assertEqual(self.t.peek_pending("g1"), ["hello"])

    def test_replace_pending(self):
        self.t.enqueue("g1", "a")
        self.t.replace_pending("g1", ["x", "y"])
        self.assertEqual(self.t.peek_pending("g1"), ["x", "y"])

    def test_clear_pending(self):
        self.t.enqueue("g1", "a")
        self.t.clear_pending("g1")
        self.assertFalse(self.t.has_pending("g1"))

    def test_queue_mode_ttl(self):
        self.t.set_queue_mode("g1")
        self.assertTrue(self.t.queue_mode_active("g1"))
        self.t._queue_mode_until["g1"] = time.time() - 1
        self.assertFalse(self.t.queue_mode_active("g1"))

    def test_pending_ttl_expiry(self):
        self.t.enqueue("g1", "old")
        self.t._pending["g1"][0]["created_at"] = time.time() - 2000
        self.t.note_inbound("g1", "m1", "u1")
        self.assertFalse(self.t.has_pending("g1"))

    def test_purge_cleans_user_windows(self):
        self._msg("g1", "mA", "uA", age=700)
        self.t.purge()
        self.assertEqual(self.t._user_windows.get(("g1", "uA")), None)


class TestPendingOwnerIsolation(PassiveWindowTestBase):
    """排队内容按主人隔离：B 触发补发不能把 A 的内容挂到 B 的窗口"""

    def test_owner_scoped_flush(self):
        self.t.enqueue("g1", "A的内容", "uA")
        self.t.enqueue("g1", "B的内容", "uB")
        # B 触发 flush：只能看到自己的内容
        self.assertTrue(self.t.has_pending("g1", "uB"))
        self.assertEqual(self.t.peek_pending("g1", "uB"), ["B的内容"])
        self.t.clear_pending("g1", "uB")
        # A 的内容仍在
        self.assertTrue(self.t.has_pending("g1", "uA"))
        self.assertEqual(self.t.peek_pending("g1", "uA"), ["A的内容"])

    def test_ownerless_flushes_on_any_window(self):
        # 无主人（主动消息兜底）可被任何触发者带出
        self.t.enqueue("g1", "广播内容", "")
        self.assertEqual(self.t.peek_pending("g1", "uB"), ["广播内容"])
        self.t.clear_pending("g1", "uB")
        self.assertFalse(self.t.has_pending("g1"))

    def test_owner_scoped_content_not_visible_to_others(self):
        self.t.enqueue("g1", "A的内容", "uA")
        self.assertFalse(self.t.has_pending("g1", "uB"))
        self.assertEqual(self.t.peek_pending("g1", "uB"), [])

    def test_replace_keeps_other_owners(self):
        self.t.enqueue("g1", "A的内容", "uA")
        self.t.enqueue("g1", "B的内容", "uB")
        # flush B 的内容后放回 leftover，A 的不受影响
        self.t.replace_pending("g1", ["B的剩余"], "uB")
        self.assertEqual(self.t.peek_pending("g1", "uA"), ["A的内容"])
        self.assertEqual(self.t.peek_pending("g1", "uB"), ["B的剩余"])


class TestCleanup(PassiveWindowTestBase):
    """清理有效性：所有结构都会被 purge 覆盖"""

    def test_purge_cleans_usage_after_windows_gone(self):
        self._msg("g1", "mA", "uA")
        self.t.mark_used("mA")
        self.t.mark_used("mA")
        self.t.mark_exhausted("mA")
        self.t.purge(now=time.time() + 601)
        self.assertEqual(self.t._usage.get("mA"), None)
        self.assertEqual(self.t._used_at.get("mA"), None)
        self.assertNotIn("mA", self.t._exhausted)

    def test_purge_keeps_usage_within_retention(self):
        self._msg("g1", "mA", "uA")
        self.t.mark_used("mA")
        self.t.purge(now=time.time() + 500)
        self.assertEqual(self.t._usage.get("mA"), 1)

    def test_remaining_via_user_window_after_group_overwrite(self):
        # B 覆盖群窗口后 A 的 msg_id 只在用户窗口，remaining 必须仍能查到
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        self.t.mark_used("mA")
        self.assertEqual(self.t.remaining("g1", "mA"), PASSIVE_MAX_REPLIES - 1)

    def test_user_window_resolved_msg_id_passes_media_gate(self):
        # 模拟媒体发送的窗口校验：resolve 返回用户窗口 msg_id 后 remaining 不能为 0
        self._msg("g1", "mA", "uA")
        self._msg("g1", "mB", "uB")
        resolved = self.t.resolve("g1", "", "uA")
        self.assertEqual(resolved, "mA")
        self.assertGreater(self.t.remaining("g1", resolved), 0)

    def test_purge_cleans_queue_mode(self):
        self.t.set_queue_mode("g1")
        self.t.purge(now=time.time() + QUEUE_MODE_SECONDS + 1)
        self.assertFalse(self.t.queue_mode_active("g1"))

    def test_purge_cleans_empty_group(self):
        self.t.enqueue("g1", "x", "uA")
        self.t.set_queue_mode("g1")
        self.t.purge(now=time.time() + PENDING_TTL_SECONDS + 1)
        self.assertEqual(self.t._pending.get("g1"), None)


class TestSyntheticMsgId(PassiveWindowTestBase):
    """合成 msg_id（scheduler_/component_ 前缀）不入窗口"""

    def test_scheduler_msg_ignored(self):
        self._msg("g1", "scheduler_123", "u1")
        self.assertEqual(self.t._windows.get("g1"), None)
        self.assertEqual(self.t.resolve("g1"), "")

    def test_component_msg_ignored(self):
        self._msg("g1", "component_task_1", "u1")
        self.assertEqual(self.t._windows.get("g1"), None)


if __name__ == "__main__":
    unittest.main()

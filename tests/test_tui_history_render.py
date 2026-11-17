# -*- coding: utf-8 -*-
from app.tui.widgets import HistoryMarkdown

DELEGATE_NOTICE = (
    "[会话委托完成]\n"
    "目标会话: 会话委托测试\n"
    "状态: 成功\n"
    "耗时: 24.28s\n"
    "结论:\n跑完了测试，结果正常。\n"
    "（如需回滚文件，请到该会话用 turn_id=m_547c4b28ac1a 撤回）"
)


def test_lazy_render_not_blank():
    w = HistoryMarkdown("")
    w.set_rendered("# 标题\n\n正文", None)
    assert w.render() is not None


def test_prebuilt_markdown_used():
    w = HistoryMarkdown("")
    w.set_rendered("正文", "已渲染")
    assert w._md == "已渲染"
    assert w.render() is not None


def test_refresh_theme_rebuilds_from_source():
    w = HistoryMarkdown("")
    w.set_rendered("正文 **加粗**", None)
    w.refresh_theme()
    assert w._md is not None


def test_is_delegation_notice():
    from app.tui.history_render import is_delegation_notice

    assert is_delegation_notice(DELEGATE_NOTICE)
    assert not is_delegation_notice("[会话委托完成]\n目标会话: a\n状态: 成功\n")
    assert not is_delegation_notice("请测试一下会话委托")


def test_parse_delegation_notice():
    from app.tui.history_render import parse_delegation_notice

    parsed = parse_delegation_notice(DELEGATE_NOTICE)
    assert parsed["title"] == "会话委托测试"
    assert parsed["status"] == "成功"
    assert parsed["elapsed"] == "24.28s"
    assert parsed["body"] == "跑完了测试，结果正常。"
    assert "如需回滚文件" in parsed["footer"]


def test_plan_slice_marks_notice():
    from app.tui.history_range import plan_slice

    plan = plan_slice([
        {"id": "m1", "role": "user", "content": DELEGATE_NOTICE},
        {"id": "m2", "role": "user", "content": "普通消息"},
    ], 0, 2)
    assert plan[0]["kind"] == "notice"
    assert plan[1]["kind"] == "user"


def test_build_history_plan_window_limits(monkeypatch):
    from app.tui import history_render

    messages = [{"role": "user", "content": f"m{i}"} for i in range(1000)]

    class _Archive:
        def count_messages(self, session_id):
            return len(messages)

        def get_recent_messages(self, session_id, limit, offset):
            end = len(messages) - offset
            return messages[max(0, end - limit):end]

    monkeypatch.setattr(history_render, "_resolve_archive", lambda: (None, _Archive()))
    plan, meta = history_render.build_history_plan_window("s", 100)
    assert len(plan) == 100
    assert meta["total"] == 1000
    assert meta["start"] == 900
    assert meta["has_more"] is True
    assert plan[0]["span"][0] == 900


def test_load_history_window_offset(monkeypatch):
    from app.tui import history_render

    messages = [{"role": "user", "content": f"m{i}"} for i in range(1000)]

    class _Archive:
        def count_messages(self, session_id):
            return len(messages)

        def get_recent_messages(self, session_id, limit, offset):
            end = len(messages) - offset
            return messages[max(0, end - limit):end]

    monkeypatch.setattr(history_render, "_resolve_archive", lambda: (None, _Archive()))
    window, total = history_render.load_history_window("s", 100, 800)
    assert total == 1000
    assert window[0]["content"] == "m100"
    assert window[-1]["content"] == "m199"


def test_load_history_window_after_new_messages(monkeypatch):
    from app.tui import history_render

    messages = [{"role": "user", "content": f"m{i}"} for i in range(1000)]

    class _Archive:
        def count_messages(self, session_id):
            return len(messages)

        def get_recent_messages(self, session_id, limit, offset):
            end = len(messages) - offset
            return messages[max(0, end - limit):end]

    monkeypatch.setattr(history_render, "_resolve_archive", lambda: (None, _Archive()))
    _, total = history_render.load_history_window("s", 100, 0)
    assert total == 1000
    messages.extend({"role": "user", "content": f"new{i}"} for i in range(50))
    total_now = history_render.history_total("s")
    offset = total_now - 900
    window, total2 = history_render.load_history_window("s", 100, offset)
    assert total2 == 1050
    assert window[0]["content"] == "m800"
    assert window[-1]["content"] == "m899"
    assert not any(str(m["content"]).startswith("new") for m in window)


def test_jsonl_archive_message_window(tmp_path):
    from app.agent.memory.archive_store import ArchiveStore

    archive = ArchiveStore(str(tmp_path / "archive"))
    for i in range(3):
        archive.append_messages(session_id="s", messages=[
            {"role": "user", "content": f"u{i}"},
            {"role": "assistant", "content": f"a{i}"},
        ])
    assert archive.count_messages("s") == 6
    assert [m["content"] for m in archive.get_recent_messages("s", 2, 2)] == ["u1", "a1"]
    assert [m["content"] for m in archive.get_recent_messages("s", 2, 0)] == ["u2", "a2"]
    assert [m["content"] for m in archive.get_message_range("s", 1, 3)] == ["a0", "u1"]


def test_history_page_aligns_start_to_user(monkeypatch):
    from app.tui import history_render

    messages = [
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]

    class _Archive:
        def count_messages(self, session_id):
            return len(messages)

        def get_message_range(self, session_id, start, end=None):
            return messages[start:end]

        def get_recent_messages(self, session_id, limit, offset):
            end = len(messages) - offset
            return messages[max(0, end - limit):end]

    monkeypatch.setattr(history_render, "_resolve_archive", lambda: (None, _Archive()))
    page, start, end, total = history_render.load_history_page("s", 7, 3)
    assert total == 9
    assert start == 2
    assert end == 7
    assert page[0]["content"] == "u1"
    assert [m["role"] for m in page] == ["user", "assistant", "tool", "user", "assistant"]

    older, older_start, older_end, _ = history_render.load_history_page("s", start, 3)
    assert older_start == 0
    assert older_end == 2
    assert older[0]["content"] == "u0"


def test_history_page_absolute_index_after_append(monkeypatch):
    from app.tui import history_render

    messages = [{"role": "user", "content": f"m{i}"} for i in range(1000)]

    class _Archive:
        def count_messages(self, session_id):
            return len(messages)

        def get_message_range(self, session_id, start, end=None):
            return messages[start:end]

    monkeypatch.setattr(history_render, "_resolve_archive", lambda: (None, _Archive()))
    messages.extend({"role": "user", "content": f"new{i}"} for i in range(50))
    page, start, end, total = history_render.load_history_page("s", 900, 100)
    assert total == 1050
    assert start == 800
    assert end == 900
    assert page[0]["content"] == "m800"
    assert page[-1]["content"] == "m899"


def test_delegation_notice_widget_toggle():
    import asyncio

    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll

    from app.tui.widgets import DelegationNotice

    long_notice = DELEGATE_NOTICE.replace(
        "跑完了测试，结果正常。", "\n".join(f"结论行{i}" for i in range(5))
    )

    class _Probe(App):
        CSS = """
        .delegation-notice {
            padding: 0 1;
            border-left: solid $primary;
            background: $panel 55%;
        }
        """

        def tr(self, key, *args):
            from app.tui.i18n import t
            return t("zh-CN", key, *args)

        def compose(self) -> ComposeResult:
            with VerticalScroll():
                yield DelegationNotice(long_notice)

    async def _run():
        app = _Probe()
        async with app.run_test(size=(80, 30)) as pilot:
            w = app.query_one(DelegationNotice)
            await pilot.pause(0.1)
            collapsed = w.content.plain
            w._expanded = True
            w._refresh()
            await pilot.pause(0.1)
            return collapsed, w.content.plain

    collapsed, expanded = asyncio.run(_run())
    assert "会话委托测试" in collapsed
    assert "✓ 成功" in collapsed
    assert "24.28s" in collapsed
    assert "点击展开" in collapsed
    assert "如需回滚文件" in collapsed
    assert len(expanded) > len(collapsed)

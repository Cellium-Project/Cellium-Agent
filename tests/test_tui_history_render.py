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

# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.server import session_mode


def test_subagent_inherits_plan_but_not_delegate():
    from components.sub_agent import _resolve_parent_mode

    session_mode.set_mode("mode-test-plan", "plan", broadcast=False)
    session_mode.set_mode("mode-test-delegate", "delegate", broadcast=False)
    session_mode.set_mode("mode-test-run", "run", broadcast=False)

    assert _resolve_parent_mode("mode-test-plan") == "plan"
    assert _resolve_parent_mode("mode-test-delegate") == "run"
    assert _resolve_parent_mode("mode-test-run") == "run"
    assert _resolve_parent_mode(None) == "run"


def test_delegation_mode_uses_target_not_parent():
    from components.session_agent import _resolve_delegation_mode

    session_mode.set_mode("mode-test-parent", "delegate", broadcast=False)
    session_mode.set_mode("mode-test-target-run", "run", broadcast=False)
    session_mode.set_mode("mode-test-target-plan", "plan", broadcast=False)

    assert _resolve_delegation_mode("mode-test-target-run") == "run"
    assert _resolve_delegation_mode("mode-test-target-plan") == "plan"

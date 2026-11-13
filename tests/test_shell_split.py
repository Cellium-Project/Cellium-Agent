# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.tools.shell_tool import ShellTool, ShellTaskTool, ShellSessionTool


def test_shell_schema_run_only():
    params = ShellTool().definition["function"]["parameters"]
    assert params["required"] == ["command"]
    assert set(params["properties"]) == {"command", "background", "timeout"}
    assert "cmd" not in params["properties"]
    assert "action" not in params["properties"]


def test_shell_task_schema():
    params = ShellTaskTool().definition["function"]["parameters"]
    assert params["required"] == ["action"]
    assert params["properties"]["action"]["enum"] == ["list", "output", "kill"]
    assert "task_id" in params["properties"]


def test_shell_session_schema():
    params = ShellSessionTool().definition["function"]["parameters"]
    assert params["required"] == ["action"]
    assert params["properties"]["action"]["enum"] == ["start", "send", "output", "close", "list"]
    assert {"command", "session_id", "text", "wait", "window"} <= set(params["properties"])


def test_shell_task_routing(monkeypatch):
    tool = ShellTaskTool()
    monkeypatch.setattr(ShellTool, "_cmd_list", lambda self, args: {"hit": "list"})
    monkeypatch.setattr(ShellTool, "_cmd_output", lambda self, args: {"hit": "output", "args": args})
    monkeypatch.setattr(ShellTool, "_cmd_kill", lambda self, args: {"hit": "kill"})

    assert tool.execute({"action": "list"})["hit"] == "list"
    assert tool.execute({"action": "output", "task_id": "t1"})["hit"] == "output"
    assert tool.execute({"action": "kill", "task_id": "t1"})["hit"] == "kill"
    assert tool.execute({"action": "unknown"})["success"] is False


def test_shell_session_routing(monkeypatch):
    tool = ShellSessionTool()
    seen = {}

    def fake_session(self, args):
        seen.update(args)
        return {"hit": "session"}

    monkeypatch.setattr(ShellTool, "_cmd_session", fake_session)

    assert tool.execute({"action": "send", "session_id": "s1", "input": "ls"})["hit"] == "session"
    assert seen["text"] == "ls"
    assert tool.execute({"action": "list"})["hit"] == "session"


def test_shell_new_format_runs_command(monkeypatch):
    tool = ShellTool()
    seen = {}

    def fake_run(self, args):
        seen.update(args)
        return {"success": True}

    monkeypatch.setattr(ShellTool, "_cmd_run", fake_run)

    assert tool.execute({"command": "ls -la"})["success"] is True
    assert seen["cmd"] == "ls -la"
    assert tool.execute({"command": ["python", "-V"]})["success"] is True
    assert seen["cmd"] == ["python", "-V"]


def test_shell_legacy_format_compat(monkeypatch):
    tool = ShellTool()
    monkeypatch.setattr(ShellTool, "_cmd_run", lambda self, args: {"hit": "run", "cmd": args.get("cmd")})
    monkeypatch.setattr(ShellTool, "_cmd_list", lambda self, args: {"hit": "list"})
    monkeypatch.setattr(ShellTool, "_cmd_session", lambda self, args: {"hit": "session"})

    assert tool.execute({"command": "run", "cmd": "ls -la"})["hit"] == "run"
    assert tool.execute({"command": "list"})["hit"] == "list"
    assert tool.execute({"command": "session", "action": "list"})["hit"] == "session"


def test_legacy_argv_supported(monkeypatch):
    tool = ShellTool()
    seen = {}
    monkeypatch.setattr(ShellTool, "_cmd_run", lambda self, args: seen.update(args) or {"success": True})

    assert tool.execute({"command": "run", "argv": ["python", "-V"]})["success"] is True
    assert seen["cmd"] == ["python", "-V"]


def test_session_start_maps_command_to_cmd(monkeypatch):
    tool = ShellSessionTool()
    seen = {}
    monkeypatch.setattr(ShellTool, "_cmd_session", lambda self, args: seen.update(args) or {"success": True})

    tool.execute({"action": "start", "command": "ssh host"})
    assert seen["cmd"] == "ssh host"

    tool.execute({"action": "start", "command": ["python", "-i"]})
    assert seen["cmd"] == ["python", "-i"]


def test_run_parity_old_vs_new():
    from app.agent.shell.cellium_shell import CelliumShell

    tool = ShellTool(shell=CelliumShell())
    old = tool.execute({"command": "run", "cmd": ["python", "--version"]})
    new = tool.execute({"command": ["python", "--version"]})
    assert old.get("success") == new.get("success") is True
    assert old.get("output", "").strip() == new.get("output", "").strip()
    assert old.get("exit_code") == new.get("exit_code")


def test_plan_mode_session_parity():
    from app.core.security.run_mode import plan_block_reason

    assert plan_block_reason("shell_session", {"action": "list"}) is None
    assert plan_block_reason("shell_session", {"action": "output", "session_id": "s"}) is None
    assert "Plan 模式禁止" in plan_block_reason("shell_session", {"action": "start", "command": "ssh host"})
    assert "Plan 模式禁止" in plan_block_reason("shell_session", {"action": "send", "session_id": "s", "text": "ls"})
    assert "Plan 模式禁止" in plan_block_reason("shell_session", {"action": "close", "session_id": "s"})


class _FakeSessionShell:
    _platform = "linux"

    def __init__(self):
        self.calls = []

    def _check_security(self, cmd):
        self.calls.append(("check", cmd))
        return {"allowed": True}

    def resolve_session_argv(self, cmd):
        self.calls.append(("resolve", cmd))
        return ["resolved"]

    def start_session(self, argv, cwd=None, window=False, pty=False):
        return {"argv": argv, "cwd": cwd, "window": window, "pty": pty}


def test_session_start_array_command_keeps_argv():
    shell = _FakeSessionShell()
    result = ShellSessionTool(shell=shell).execute(
        {"action": "start", "command": ["echo", "hi"]}
    )
    assert result["argv"] == ["echo", "hi"]
    assert ("check", "echo hi") in shell.calls
    assert not any(call[0] == "resolve" for call in shell.calls)


def test_session_start_string_command_resolves():
    shell = _FakeSessionShell()
    result = ShellSessionTool(shell=shell).execute(
        {"action": "start", "command": "ssh host"}
    )
    assert result["argv"] == ["resolved"]
    assert ("resolve", "ssh host") in shell.calls


def test_anthropic_schema_flattens_oneof():
    import json

    from app.agent.llm.anthropic_transport import AnthropicTransport

    converted = AnthropicTransport._convert_tools([
        ShellTool().definition,
        ShellSessionTool().definition,
    ])
    for tool in converted:
        text = json.dumps(tool["input_schema"])
        assert "oneOf" not in text
        assert "anyOf" not in text

    cmd_schema = converted[0]["input_schema"]["properties"]["command"]
    assert cmd_schema["type"] == "string"


def test_shell_description_uses_real_cmd_for_legacy():
    from app.agent.loop.tool_executor import ToolDescriptionGenerator

    desc = ToolDescriptionGenerator.generate("shell", {"command": "run", "cmd": "mycmd --flag"})
    assert "mycmd" in desc


def _fake_session(argv):
    class _Sess:
        def __init__(self):
            self.argv = argv
            self.sent = []

        def is_alive(self):
            return True

        def send(self, text):
            self.sent.append(text)
            return True

    return _Sess()


def test_session_send_checks_local_shell_input():
    from app.agent.shell.cellium_shell import CelliumShell

    shell = CelliumShell()
    try:
        shell._sessions["sh1"] = _fake_session(["bash"])

        blocked = shell.session_send("sh1", "rm -rf /")
        assert blocked["success"] is False
        assert "安全拦截" in blocked["error"]

        assert shell.session_send("sh1", "echo ok")["success"] is True
        assert shell.session_send("sh1", "echo a; rm -rf /")["success"] is False
        assert shell.session_send("sh1", "env rm -rf /")["success"] is False
        # 密码形态不以危险命令开头，不误伤
        assert shell.session_send("sh1", "Shutdown123!")["success"] is True

        shell._sessions["ssh1"] = _fake_session(["ssh", "host"])
        assert shell.session_send("ssh1", "Shutdown123!&")["success"] is True
        assert shell.session_send("ssh1", "rm -rf /")["success"] is True

        shell._sessions["py1"] = _fake_session(["python", "-i"])
        assert shell.session_send("py1", "os.system('rm -rf /')")["success"] is True

        shell._session_send_check = False
        assert shell.session_send("sh1", "rm -rf /")["success"] is True
    finally:
        shell._sessions.clear()
        shell.terminate()


def test_session_send_blocks_quoted_and_wrapped_commands():
    from app.agent.shell.cellium_shell import CelliumShell

    shell = CelliumShell()
    try:
        shell._sessions["sh1"] = _fake_session(["bash"])
        assert shell.session_send("sh1", '"rm" "-rf" "/"')["success"] is False
        assert shell.session_send("sh1", 'eval "rm -rf /"')["success"] is False
        assert shell.session_send("sh1", "sudo rm -rf /")["success"] is False
        assert shell.session_send("sh1", "r''m -rf /")["success"] is False
    finally:
        shell._sessions.clear()
        shell.terminate()


def test_session_kind_detection():
    from app.agent.shell.cellium_shell import _session_kind

    assert _session_kind(["bash"]) == "shell"
    assert _session_kind(["C:\\Windows\\System32\\cmd.exe"]) == "shell"
    assert _session_kind(["ssh", "host"]) == "remote"
    assert _session_kind(["python.exe", "-i"]) == "repl"
    assert _session_kind(None) == "shell"


def test_shell_task_output_truncates_large_file(tmp_path):
    from app.agent.tools.shell_tool import OUTPUT_PREVIEW_BYTES

    class _Proc:
        returncode = 0

        def poll(self):
            return 0

    class _Shell:
        def __init__(self, path):
            self._background_tasks = {
                "t1": {"output_file": str(path), "process_ref": {"p": _Proc()}}
            }

    out = tmp_path / "out.log"
    out.write_text("A" * (OUTPUT_PREVIEW_BYTES + 1024) + "TAIL", encoding="utf-8")

    res = ShellTaskTool(shell=_Shell(out)).execute({"action": "output", "task_id": "t1"})
    assert res["truncated"] is True
    assert res["total_bytes"] > OUTPUT_PREVIEW_BYTES
    assert res["output_file"] == str(out)
    assert res["output"].endswith("TAIL")


def test_shell_task_output_small_file_not_truncated(tmp_path):
    class _Proc:
        returncode = 0

        def poll(self):
            return 0

    class _Shell:
        def __init__(self, path):
            self._background_tasks = {
                "t1": {"output_file": str(path), "process_ref": {"p": _Proc()}}
            }

    out = tmp_path / "small.log"
    out.write_bytes(b"hello\nworld\n")

    res = ShellTaskTool(shell=_Shell(out)).execute({"action": "output", "task_id": "t1"})
    assert res["truncated"] is False
    assert res["output"] == "hello\nworld\n"

# -*- coding: utf-8 -*-
import os
import sys
import time

from app.agent.shell import cellium_shell
from app.agent.shell.cellium_shell import CelliumShell


def _wait(shell, task_id, timeout=60):
    return shell._background_tasks[task_id]["future"].result(timeout=timeout)


def test_background_flood_without_newlines_does_not_block(tmp_path):
    """大量无换行输出 + stderr 洪水：不能写满管道阻塞子进程"""
    shell = CelliumShell()
    script = (
        "import sys\n"
        "for _ in range(200):\n"
        "    sys.stdout.write('\\r' + 'x' * 2000)\n"
        "    sys.stdout.flush()\n"
        "sys.stderr.write('E' * 200000)\n"
        "sys.stderr.flush()\n"
        "print('done')\n"
    )

    started = shell._run_background_argv([sys.executable, "-c", script], 60, str(tmp_path))
    assert started["status"] == "background_started"

    done = _wait(shell, started["task_id"])
    assert done["exit_code"] == 0

    with open(done["output_file"], encoding="utf-8") as f:
        data = f.read()
    assert "done" in data
    assert data.count("E") >= 200000
    assert len(data) > 500000


def test_background_string_command_writes_output(tmp_path):
    """字符串命令路径同样直接写文件句柄"""
    shell = CelliumShell()
    started = shell._run_background("echo cellium_bg_ok", 30, str(tmp_path))
    assert started["status"] == "background_started"

    done = _wait(shell, started["task_id"])
    assert done["exit_code"] == 0
    with open(done["output_file"], "rb") as f:
        assert b"cellium_bg_ok" in f.read()


def test_cleanup_stale_background_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cellium_shell.tempfile, "gettempdir", lambda: str(tmp_path))

    old = tmp_path / "bg_old.output"
    old.write_bytes(b"old")
    os.utime(old, (time.time() - 4 * 86400,) * 2)
    fresh = tmp_path / "bg_new.output"
    fresh.write_bytes(b"new")

    assert cellium_shell._cleanup_stale_background_outputs() == 1
    assert not old.exists()
    assert fresh.exists()


def test_background_timeout_kills_silent_process(tmp_path):
    """静默无输出的子进程也要按时超时，而不是卡在读管道上"""
    shell = CelliumShell()
    started = shell._run_background_argv(
        [sys.executable, "-c", "import time; time.sleep(30)"], 1, str(tmp_path)
    )

    done = _wait(shell, started["task_id"], timeout=15)
    assert done["error"] == "timeout"


def test_foreground_large_output_returns_tail_and_file(tmp_path):
    """前台大输出只回尾部，完整内容留在 output_file"""
    shell = CelliumShell()
    res = shell._execute_argv_sync(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100000)"],
        30,
        str(tmp_path),
    )

    limit = cellium_shell.MAX_CONTEXT_OUTPUT_CHARS
    assert res["exit_code"] == 0
    assert res["truncated"] is True
    assert len(res["output"]) == limit
    assert res["output"] == "x" * limit
    assert os.path.getsize(res["output_file"]) == 100000
    os.remove(res["output_file"])

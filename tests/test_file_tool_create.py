# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agent.tools.file_tool import FileTool
from app.core.util import workdir


def test_create_uses_path_as_base(tmp_path, monkeypatch):
    monkeypatch.setattr(workdir, "_workdir", str(tmp_path))
    tool = FileTool()
    base = tmp_path / "config"

    result = tool._cmd_fs(
        action="create",
        path=str(base),
        files={"tavily.yaml": "key: 1", "sub/a.txt": "hi"},
    )

    assert result["success"] is True
    assert result["files_created"] == 2
    assert (base / "tavily.yaml").read_text(encoding="utf-8") == "key: 1"
    assert (base / "sub" / "a.txt").read_text(encoding="utf-8") == "hi"


def test_create_without_path_uses_workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(workdir, "_workdir", str(tmp_path))
    tool = FileTool()

    result = tool._cmd_fs(action="create", files={"a.txt": "hi"})

    assert result["success"] is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hi"


def test_create_absolute_key_ignores_base(tmp_path, monkeypatch):
    monkeypatch.setattr(workdir, "_workdir", str(tmp_path))
    tool = FileTool()
    base = tmp_path / "config"
    abs_target = tmp_path / "elsewhere" / "b.txt"

    result = tool._cmd_fs(
        action="create",
        path=str(base),
        files={str(abs_target): "abs"},
    )

    assert result["success"] is True
    assert abs_target.read_text(encoding="utf-8") == "abs"
    assert not (base / "elsewhere").exists()


def test_create_rejects_file_as_path(tmp_path, monkeypatch):
    monkeypatch.setattr(workdir, "_workdir", str(tmp_path))
    tool = FileTool()
    target = tmp_path / "tavily.yaml"
    target.write_text("x", encoding="utf-8")

    result = tool._cmd_fs(action="create", path=str(target), files={"tavily.yaml": "y"})

    assert result["success"] is False
    assert "必须是目录" in result["error"]
    assert target.read_text(encoding="utf-8") == "x"

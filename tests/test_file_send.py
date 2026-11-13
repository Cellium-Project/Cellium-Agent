# -*- coding: utf-8 -*-
from app.agent.tools.file_tool import FileTool


def test_file_send_copies_and_returns_attachments(tmp_path, monkeypatch):
    from app.core.util import attachment_store as store
    monkeypatch.setattr(store, "UPLOAD_DIR", str(tmp_path / "uploads"))

    src = tmp_path / "note.txt"
    src.write_text("hello", encoding="utf-8")

    result = FileTool().execute({"command": "send", "paths": [str(src)]})
    assert result["success"] is True
    assert len(result["attachments"]) == 1
    att = result["attachments"][0]
    assert att["filename"] == "note.txt"
    assert att["url"].startswith("/api/files/")
    assert "hello" in open(att["local_path"], encoding="utf-8").read()


def test_file_send_infers_command_from_paths(tmp_path, monkeypatch):
    from app.core.util import attachment_store as store
    monkeypatch.setattr(store, "UPLOAD_DIR", str(tmp_path / "uploads"))

    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")

    result = FileTool().execute({"paths": [str(src)]})
    assert result["success"] is True
    assert len(result["attachments"]) == 1


def test_file_send_missing_path():
    result = FileTool().execute({"command": "send", "paths": ["/no/such/file.png"]})
    assert result["success"] is False
    assert "不存在" in result["error"]


def test_file_send_caps_count(tmp_path, monkeypatch):
    from app.core.util import attachment_store as store
    monkeypatch.setattr(store, "UPLOAD_DIR", str(tmp_path / "uploads"))

    paths = []
    for i in range(12):
        p = tmp_path / f"f{i}.txt"
        p.write_text(str(i), encoding="utf-8")
        paths.append(str(p))

    result = FileTool().execute({"command": "send", "paths": paths})
    assert result["success"] is True
    assert len(result["attachments"]) == 10

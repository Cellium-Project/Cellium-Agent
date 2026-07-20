# -*- coding: utf-8 -*-
import os
import sys
import time
import tempfile
import shutil
import stat

from app.agent.tools.read_tool import ReadTool
from app.agent.tools.edit_tool import EditTool
from app.agent.tools import file_cache as fc
from app.agent.runtime.transaction import EditTransaction


def make_temp_file(content="", suffix=".txt"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


class TestReadEditWorkflow:

    def setup_method(self):
        self.rt = ReadTool()
        self.et = EditTool()
        fc.clear_read_cache()

    def test_basic_read_edit(self):
        f = make_temp_file("line1\nline2\nline3\n")
        try:
            r = self.rt._cmd_read(file_path=f)
            assert r["success"]
            r = self.et._cmd_edit(file_path=f, old_string="line2", new_string="LINE2")
            assert r["success"]
            assert r.get("count") == 1
            with open(f, "r", encoding="utf-8") as fp:
                content = fp.read()
            assert "LINE2" in content and "line2" not in content
        finally:
            os.remove(f)

    def test_multi_line_replace(self):
        f = make_temp_file("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f,
                                  old_string="def foo():\n    return 1",
                                  new_string="def foo():\n    return 100")
            assert r["success"]
        finally:
            os.remove(f)

    def test_indentation_sensitive(self):
        f = make_temp_file("if True:\n    a = 1\n    b = 2\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f,
                                  old_string="    a = 1\n    b = 2",
                                  new_string="    a = 10\n    b = 20")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_without_read_fails(self):
        f = make_temp_file("test content\n")
        try:
            r = self.et._cmd_edit(file_path=f, old_string="test", new_string="TEST")
            assert not r["success"]
            assert "read" in r.get("error", "").lower()
        finally:
            os.remove(f)

    def test_edit_nonexistent_file(self):
        r = self.et._cmd_edit(file_path="C:\\__nonexistent_xyz__.txt", old_string="a", new_string="b")
        assert not r["success"]

    def test_edit_after_partial_read_works(self):
        f = make_temp_file("\n".join(f"line{i}" for i in range(100)))
        try:
            self.rt._cmd_read(file_path=f, offset=10, limit=10)
            r = self.et._cmd_edit(file_path=f, old_string="line50", new_string="LINE50")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_after_limit_read_works(self):
        f = make_temp_file("\n".join(f"line{i:03d}" for i in range(100)))
        try:
            self.rt._cmd_read(file_path=f, limit=10)
            r = self.et._cmd_edit(file_path=f, old_string="line005", new_string="LINE005")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_after_target_read_works(self):
        f = make_temp_file("\n".join(f"line{i}" for i in range(100)))
        try:
            self.rt._cmd_read(file_path=f, target="line50")
            r = self.et._cmd_edit(file_path=f, old_string="line50", new_string="LINE50")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_after_needle_read_works(self):
        f = make_temp_file("def hello():\n    return 1\n")
        try:
            self.rt._cmd_read(file_path=f, needle="def hello")
            r = self.et._cmd_edit(file_path=f, old_string="def hello():", new_string="def hi():")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_after_full_default_read(self):
        f = make_temp_file("\n".join(f"line{i}" for i in range(50)))
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line10", new_string="LINE10")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_after_large_file_streaming(self):
        f = make_temp_file(suffix=".txt")
        try:
            with open(f, "w", encoding="utf-8") as fp:
                for i in range(10000):
                    fp.write(f"line {i} {'x' * 100}\n")
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line 100 xxxxxxxxxxx", new_string="MODIFIED")
            assert r["success"]
        finally:
            os.remove(f)

    def test_external_modification_detected(self):
        f = make_temp_file("original content line1\noriginal content line2\n")
        try:
            self.rt._cmd_read(file_path=f)
            time.sleep(1.1)
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("CHANGED content line1\nCHANGED content line2\n")
            r = self.et._cmd_edit(file_path=f, old_string="original content line1", new_string="NEW")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_external_same_content_still_works(self):
        f = make_temp_file("original content\n")
        try:
            self.rt._cmd_read(file_path=f)
            time.sleep(1.1)
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("original content\n")
            r = self.et._cmd_edit(file_path=f, old_string="original content", new_string="NEW")
            assert r["success"]
        finally:
            os.remove(f)

    def test_external_deletion_detected(self):
        f = make_temp_file("test content\n")
        try:
            self.rt._cmd_read(file_path=f)
            time.sleep(1.1)
            os.remove(f)
            r = self.et._cmd_edit(file_path=f, old_string="test content", new_string="NEW")
            assert not r["success"]
        finally:
            pass

    def test_exact_match(self):
        f = make_temp_file("hello world\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="hello world", new_string="hi world")
            assert r["success"]
        finally:
            os.remove(f)

    def test_smart_quote_normalization(self):
        f = make_temp_file('message = "hello"\n', suffix=".py")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f,
                                  old_string='message = \u201chello\u201d',
                                  new_string='message = "hi"')
            assert r["success"]
        finally:
            os.remove(f)

    def test_trailing_whitespace(self):
        f = make_temp_file("line with trailing space   \nline2\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line with trailing space",
                                  new_string="clean line")
            assert r["success"]
        finally:
            os.remove(f)

    def test_nonexistent_old_string(self):
        f = make_temp_file("hello world\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="not in file", new_string="x")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_multiple_matches_fails(self):
        f = make_temp_file("foo\nfoo\nbar\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="foo", new_string="FOO")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_replace_all(self):
        f = make_temp_file("foo\nfoo\nbar\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="foo", new_string="FOO", replace_all=True)
            assert r["success"]
            assert r.get("count") == 2
        finally:
            os.remove(f)

    def test_crlf_compatibility(self):
        f = make_temp_file("line1\r\nline2\r\nline3\r\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line2", new_string="LINE2")
            assert r["success"]
        finally:
            os.remove(f)

    def test_large_crlf_file(self):
        f = make_temp_file(suffix=".txt")
        try:
            with open(f, "wb") as fp:
                for i in range(10000):
                    fp.write(f"line {i}\r\n".encode("utf-8"))
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line 5000", new_string="MODIFIED")
            assert r["success"]
        finally:
            os.remove(f)

    def test_read_edit_cycle(self):
        f = make_temp_file("v1\nv2\nv3\n")
        try:
            self.rt._cmd_read(file_path=f)
            self.et._cmd_edit(file_path=f, old_string="v1", new_string="v1.1")
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="v2", new_string="v2.1")
            assert r["success"]
        finally:
            os.remove(f)

    def test_immediate_edit_after_read(self):
        f = make_temp_file("quick test\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="quick test", new_string="fast test")
            assert r["success"]
        finally:
            os.remove(f)

    def test_edit_across_instances(self):
        f = make_temp_file("multi edit\n")
        try:
            rt1, et1 = ReadTool(), EditTool()
            rt1._cmd_read(file_path=f)
            et1._cmd_edit(file_path=f, old_string="multi edit", new_string="first")
            rt2, et2 = ReadTool(), EditTool()
            r2 = et2._cmd_edit(file_path=f, old_string="first", new_string="second")
            assert r2["success"]
        finally:
            os.remove(f)

    def test_edit_large_file(self):
        f = make_temp_file(suffix=".txt")
        try:
            with open(f, "w", encoding="utf-8") as fp:
                for i in range(20000):
                    fp.write(f"line {i} {'x' * 200}\n")
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="line 10000 xxx", new_string="EDITED")
            assert r["success"]
        finally:
            os.remove(f)

    def test_escaped_chars_no_crash(self):
        f = make_temp_file('print("hello\\nworld")\n', suffix=".py")
        try:
            self.rt._cmd_read(file_path=f)
            self.et._cmd_edit(file_path=f,
                              old_string='print("hello\\nworld")',
                              new_string='print("hi")')
        finally:
            os.remove(f)

    def test_empty_old_string(self):
        f = make_temp_file("content\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="", new_string="x")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_identical_strings(self):
        f = make_temp_file("same\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="same", new_string="same")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_edit_no_file_path(self):
        r = self.et._cmd_edit(file_path="", old_string="a", new_string="b")
        assert not r["success"]

    def test_edit_no_old_string(self):
        f = make_temp_file("x")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string=None, new_string="y")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_null_char_in_old_string(self):
        f = make_temp_file("a\x00b\n")
        try:
            self.rt._cmd_read(file_path=f)
            self.et._cmd_edit(file_path=f, old_string="a\x00b", new_string="abc")
        finally:
            os.remove(f)

    def test_none_new_string(self):
        f = make_temp_file("content\n")
        try:
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="content", new_string=None)
            assert not r["success"]
        finally:
            os.remove(f)

    def test_target_not_found(self):
        f = make_temp_file("a\nb\nc\n")
        try:
            r = self.rt._cmd_read(file_path=f, target="nonexistent")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_needle_not_found(self):
        f = make_temp_file("a\nb\nc\n")
        try:
            r = self.rt._cmd_read(file_path=f, needle="not in file")
            assert not r["success"]
        finally:
            os.remove(f)

    def test_needle_crlf_tolerance(self):
        f = make_temp_file("line1\r\nline2 with needle\r\nline3\r\n")
        try:
            r = self.rt._cmd_read(file_path=f, needle="line2 with needle")
            assert r["success"]
        finally:
            os.remove(f)

    def test_dedup_same_instance(self):
        f = make_temp_file("dedup test\n")
        try:
            r1 = self.rt._cmd_read(file_path=f)
            assert r1["success"] and not r1.get("_dedup")
            r2 = self.rt._cmd_read(file_path=f)
            assert r2.get("_dedup") is True
        finally:
            os.remove(f)

    def test_dedup_cross_instance(self):
        f = make_temp_file("cross instance\n")
        try:
            rt1, rt2 = ReadTool(), ReadTool()
            rt1._cmd_read(file_path=f)
            r2 = rt2._cmd_read(file_path=f)
            assert not r2.get("_dedup")
        finally:
            os.remove(f)

    def test_gbk_encoding(self):
        f = make_temp_file(suffix=".txt")
        try:
            with open(f, "w", encoding="gbk") as fp:
                fp.write("你好世界\n")
            r = self.rt._cmd_read(file_path=f)
            assert r["success"]
            r2 = self.et._cmd_edit(file_path=f, old_string="你好世界", new_string="世界你好")
            assert r2["success"]
        finally:
            os.remove(f)

    def test_utf8_bom(self):
        f = make_temp_file(suffix=".txt")
        try:
            with open(f, "wb") as fp:
                fp.write(b'\xef\xbb\xbf')
                fp.write("BOM test\n".encode("utf-8"))
            r = self.rt._cmd_read(file_path=f)
            assert r["success"]
            r2 = self.et._cmd_edit(file_path=f, old_string="BOM test", new_string="BOM modified")
            assert r2["success"]
        finally:
            os.remove(f)

    def test_relative_path(self):
        old_cwd = os.getcwd()
        d = tempfile.mkdtemp()
        try:
            f_rel = os.path.join(d, "rel.txt")
            with open(f_rel, "w", encoding="utf-8") as fp:
                fp.write("relative test\n")
            os.chdir(d)
            self.rt._cmd_read(file_path="rel.txt")
            r = self.et._cmd_edit(file_path="rel.txt", old_string="relative test", new_string="REL")
            assert r["success"]
        finally:
            os.chdir(old_cwd)
            shutil.rmtree(d, ignore_errors=True)

    def test_path_with_spaces(self):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "file with spaces.txt")
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("space test\n")
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="space test", new_string="SPACE")
            assert r["success"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_chinese_path(self):
        d = tempfile.mkdtemp()
        try:
            f = os.path.join(d, "中文文件.txt")
            with open(f, "w", encoding="utf-8") as fp:
                fp.write("chinese test\n")
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="chinese test", new_string="中文测试")
            assert r["success"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_edit_failure_no_file_change(self):
        f = make_temp_file("original\nunique_marker\nmore content\n")
        try:
            self.rt._cmd_read(file_path=f)
            with open(f, "r", encoding="utf-8") as fp:
                original_content = fp.read()
            r = self.et._cmd_edit(file_path=f, old_string="original\n", new_string="X\n", replace_all=False)
            if not r["success"]:
                with open(f, "r", encoding="utf-8") as fp:
                    assert original_content == fp.read()
        finally:
            os.remove(f)

    def test_atomic_write(self):
        f = make_temp_file("atomic test\n")
        try:
            with open(f, "r", encoding="utf-8") as fp:
                content = fp.read()
            patch = {"mode": "replace", "old_text": "atomic test", "new_text": "ATOMIC", "replace_all": False}
            result = EditTransaction.apply_edit(f, content, patch)
            assert result["success"]
            with open(f, "r", encoding="utf-8") as fp:
                assert "ATOMIC" in fp.read()
        finally:
            os.remove(f)

    def test_insert_patch(self):
        f = make_temp_file("\n".join(f"line{i}" for i in range(10)) + "\n")
        try:
            with open(f, "r", encoding="utf-8") as fp:
                content = fp.read()
            patch = {"mode": "insert", "line": 3, "content": "inserted\n"}
            result = EditTransaction.apply_edit(f, content, patch)
            assert result["success"]
            with open(f, "r", encoding="utf-8") as fp:
                assert "inserted" in fp.read()
        finally:
            os.remove(f)

    def test_readonly_file(self):
        if sys.platform == "win32":
            return
        if os.environ.get("CI") or os.geteuid() == 0:
            return
        f = make_temp_file("read only test\n")
        try:
            os.chmod(f, stat.S_IRUSR)
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="read only test", new_string="changed")
            assert not r["success"]
        finally:
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
            os.remove(f)

    def test_no_perm_file(self):
        if sys.platform == "win32":
            return
        f = make_temp_file("no perm\n")
        try:
            os.chmod(f, 0)
            self.rt._cmd_read(file_path=f)
            r = self.et._cmd_edit(file_path=f, old_string="no perm", new_string="changed")
            assert not r["success"]
        finally:
            os.chmod(f, stat.S_IRUSR | stat.S_IWUSR)
            os.remove(f)

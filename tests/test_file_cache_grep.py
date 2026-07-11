# -*- coding: utf-8 -*-
import os
import time
import threading
import tempfile
import shutil

from app.agent.tools import file_cache as fc
from app.agent.tools.grep_tool import GrepTool, _fallback_search, _make_rel, _make_rel_line, _apply_limit, _find_rg


class TestFileCache:

    def setup_method(self):
        fc.clear_read_cache()
        self.d = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_basic_cache(self):
        f = os.path.join(self.d, "a.txt")
        with open(f, "w") as fp:
            fp.write("line1\nline2\nline3\n")
        fc.cache_read(f, "content1")
        assert fc.is_file_read(f)
        assert fc.get_read_state(f) is not None
        assert fc.get_read_state(f)["content"] == "content1"
        assert fc.get_read_state(f)["timestamp"] > 0
        assert fc.get_read_state(f)["offset"] is None
        assert fc.get_read_state(f)["limit"] is None

    def test_path_normalization(self):
        f = os.path.join(self.d, "a.txt")
        with open(f, "w") as fp:
            fp.write("line1\n")
        fc.cache_read(f, "x")
        fc.cache_read(os.path.join(self.d, ".\\a.txt"), "x")
        state = fc.get_read_state(f)
        assert state is not None and state["content"] == "x"

    def test_overwrite(self):
        f = os.path.join(self.d, "a.txt")
        with open(f, "w") as fp:
            fp.write("line1\n")
        fc.cache_read(f, "old")
        fc.cache_read(f, "new")
        assert fc.get_read_state(f)["content"] == "new"

    def test_nonexistent(self):
        assert not fc.is_file_read("C:\\__nonexistent__")

    def test_clear(self):
        f = os.path.join(self.d, "a.txt")
        with open(f, "w") as fp:
            fp.write("line1\n")
        fc.cache_read(f, "x")
        assert fc.is_file_read(f)
        fc.clear_read_cache()
        assert not fc.is_file_read(f)

    def test_external_modification_content_changed(self):
        f = os.path.join(self.d, "modify.txt")
        with open(f, "w") as fp:
            fp.write("original content\n")
        fc.cache_read(f, "original content\n")
        assert fc.is_file_read(f)
        time.sleep(1.1)
        with open(f, "w") as fp:
            fp.write("modified content\n")
        assert not fc.is_file_read(f)

    def test_external_modification_same_content(self):
        f = os.path.join(self.d, "modify.txt")
        with open(f, "w") as fp:
            fp.write("same content\n")
        fc.cache_read(f, "same content\n")
        time.sleep(1.1)
        with open(f, "w") as fp:
            fp.write("same content\n")
        assert fc.is_file_read(f)

    def test_external_modification_deletion(self):
        f = os.path.join(self.d, "delete.txt")
        with open(f, "w") as fp:
            fp.write("test\n")
        fc.cache_read(f, "test\n")
        assert fc.is_file_read(f)
        os.remove(f)
        assert not fc.is_file_read(f)

    def test_partial_view_full(self):
        f = os.path.join(self.d, "p.txt")
        with open(f, "w") as fp:
            fp.write("\n".join(f"line{i}" for i in range(100)))
        fc.cache_read(f, "full content")
        assert not fc.is_partial_view(f)

    def test_partial_view_offset(self):
        f = os.path.join(self.d, "p.txt")
        with open(f, "w") as fp:
            fp.write("\n".join(f"line{i}" for i in range(100)))
        fc.cache_read(f, "partial", offset=10, limit=20)
        assert fc.is_partial_view(f)

    def test_partial_view_only_offset(self):
        f = os.path.join(self.d, "p.txt")
        with open(f, "w") as fp:
            fp.write("\n".join(f"line{i}" for i in range(100)))
        fc.cache_read(f, "partial", offset=10)
        assert fc.is_partial_view(f)

    def test_partial_view_only_limit(self):
        f = os.path.join(self.d, "p.txt")
        with open(f, "w") as fp:
            fp.write("\n".join(f"line{i}" for i in range(100)))
        fc.cache_read(f, "partial", limit=20)
        assert fc.is_partial_view(f)

    def test_partial_view_cleared_by_touch(self):
        f = os.path.join(self.d, "p.txt")
        with open(f, "w") as fp:
            fp.write("\n".join(f"line{i}" for i in range(100)))
        fc.cache_read(f, "partial", offset=0, limit=100)
        assert fc.is_partial_view(f)
        fc.touch_read_state(f, "new full content")
        assert not fc.is_partial_view(f)

    def test_partial_view_nonexistent(self):
        assert not fc.is_partial_view("C:\\__nonexistent__")

    def test_lru_eviction(self):
        files = [os.path.join(self.d, f"f{i}.txt") for i in range(110)]
        for fp in files:
            with open(fp, "w") as f:
                f.write(f"content {fp}\n")
        for fp in files:
            fc.cache_read(fp, f"content {fp}\n")
        assert fc.get_read_state(files[0]) is None
        assert fc.get_read_state(files[-1]) is not None

    def test_lru_recently_accessed_kept(self):
        files = [os.path.join(self.d, f"f{i}.txt") for i in range(110)]
        for fp in files:
            with open(fp, "w") as f:
                f.write(f"content {fp}\n")
        for fp in files:
            fc.cache_read(fp, f"content {fp}\n")
        fc.is_file_read(files[20])
        fc.cache_read(files[0], "refresh")
        assert fc.get_read_state(files[20]) is not None
        assert fc.get_read_state(files[10]) is None

    def test_concurrent_access(self):
        f = os.path.join(self.d, "concurrent.txt")
        with open(f, "w") as fp:
            fp.write("shared\n")
        errors = []

        def worker(i):
            try:
                for j in range(100):
                    fc.cache_read(f, f"worker{i}-iter{j}")
                    fc.is_file_read(f)
                    fc.get_read_state(f)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0, str(errors)
        assert fc.get_read_state(f) is not None

    def test_touch_read_state(self):
        f = os.path.join(self.d, "touch.txt")
        with open(f, "w") as fp:
            fp.write("touch test\n")
        fc.cache_read(f, "partial content", offset=5, limit=10)
        assert fc.is_partial_view(f)
        fc.touch_read_state(f, "new full content")
        state = fc.get_read_state(f)
        assert state["content"] == "new full content"
        assert state["offset"] is None
        assert state["limit"] is None
        assert state["timestamp"] > 0


class TestGrepToolFunctions:

    def test_make_rel_same_drive(self):
        old = os.getcwd()
        try:
            os.chdir("C:\\Windows")
            r = _make_rel("C:\\Windows\\System32")
            assert r == "System32"
        finally:
            os.chdir(old)

    def test_make_rel_different_drive(self):
        old = os.getcwd()
        try:
            os.chdir("C:\\Windows")
            r = _make_rel("D:\\Other")
            assert r == "D:\\Other" or "Other" in r
        finally:
            os.chdir(old)

    def test_apply_limit(self):
        items = list(range(300))
        sliced, limit = _apply_limit(items, 50, 0)
        assert len(sliced) == 50 and limit == 50
        sliced, limit = _apply_limit(items, None, 0)
        assert len(sliced) == 250 and limit == 250
        sliced, limit = _apply_limit(items, 50, 250)
        assert len(sliced) == 50 and limit is None
        sliced, limit = _apply_limit(items, 50, 200)
        assert len(sliced) == 50 and limit == 50

    def test_apply_limit_boundary(self):
        items = list(range(300))
        sliced, limit = _apply_limit(items, 50, 300)
        assert len(sliced) == 0 and limit is None
        sliced, limit = _apply_limit(items, 50, 999)
        assert len(sliced) == 0 and limit is None
        sliced, limit = _apply_limit(items, 50, 240)
        assert len(sliced) == 50 and limit == 50

    def test_apply_limit_default_for_zero_negative(self):
        items = list(range(300))
        sliced, limit = _apply_limit(items, 0, 0)
        assert len(sliced) == 250
        sliced, limit = _apply_limit(items, -5, 0)
        assert len(sliced) == 250

    def test_make_rel_line(self):
        line = "C:\\Users\\test\\file.py:10:def hello():"
        r = _make_rel_line(line)
        assert "file.py:10:" in r

    def test_find_rg(self):
        r = _find_rg()
        assert r is None or isinstance(r, str)


class TestGrepToolRipgrep:

    def setup_method(self):
        self.gt = GrepTool()

    def test_content_mode(self):
        r = self.gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="content", head_limit=5)
        assert r["success"] and r["mode"] == "content"
        assert r["num_lines"] >= 0
        if r["num_lines"] > 0:
            assert ":" in r["content"].split('\n')[0]

    def test_content_with_context(self):
        r = self.gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-A": 2})
        assert r["success"]
        r = self.gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-B": 2})
        assert r["success"]
        r = self.gt._cmd_grep(query="def ", path=".", glob="*.py", output_mode="content", head_limit=5, **{"-C": 2})
        assert r["success"]

    def test_files_with_matches(self):
        r = self.gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", head_limit=10)
        assert r["success"] and r["mode"] == "files_with_matches"
        assert isinstance(r["filenames"], list)

    def test_count_mode(self):
        r = self.gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="count", head_limit=10)
        assert r["success"] and r["mode"] == "count"
        assert "num_matches" in r
        assert "num_files" in r

    def test_multiline(self):
        r = self.gt._cmd_grep(query="def.*\\n.*return", path=".", glob="*.py", output_mode="content", multiline=True, head_limit=3)
        assert r["success"]

    def test_ignore_case(self):
        r1 = self.gt._cmd_grep(query="DEF", path=".", glob="*.py", output_mode="files_with_matches", head_limit=5)
        r2 = self.gt._cmd_grep(query="DEF", path=".", glob="*.py", output_mode="files_with_matches", **{"-i": True}, head_limit=5)
        assert r2["num_files"] >= r1["num_files"]

    def test_type_filter(self):
        r = self.gt._cmd_grep(query="import", path=".", type="py", output_mode="files_with_matches", head_limit=5)
        assert r["success"]
        assert all(f.endswith(".py") for f in r["filenames"])

    def test_glob_multi(self):
        r = self.gt._cmd_grep(query="import", path=".", glob="*.py,*.txt", output_mode="files_with_matches", head_limit=10)
        assert r["success"]

    def test_query_starts_with_dash(self):
        r = self.gt._cmd_grep(query="-v", path=".", glob="*.py", output_mode="files_with_matches", head_limit=5)
        assert r["success"]

    def test_head_limit_default(self):
        r = self.gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", head_limit=0)
        assert r["success"] and r["num_files"] <= 250

    def test_offset(self):
        r = self.gt._cmd_grep(query="def", path=".", glob="*.py", output_mode="files_with_matches", offset=5, head_limit=3)
        assert r["success"] and r["offset"] == 5

    def test_relative_path(self):
        d = tempfile.mkdtemp()
        try:
            old = os.getcwd()
            os.chdir(d)
            with open(os.path.join(d, "t.py"), "w") as fp:
                fp.write("test\n")
            r = self.gt._cmd_grep(query="test", path=".", output_mode="files_with_matches")
            assert r["success"]
            os.chdir(old)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_absolute_path(self):
        r = self.gt._cmd_grep(query="def", path="C:\\Windows", output_mode="files_with_matches", head_limit=1)
        assert r["success"]

    def test_nonexistent_path(self):
        r = self.gt._cmd_grep(query="test", path="C:\\__nonexistent_xyz__", output_mode="files_with_matches")
        assert r["success"] and r["num_files"] == 0

    def test_empty_path(self):
        r = self.gt._cmd_grep(query="def", path="", output_mode="files_with_matches", head_limit=1)
        assert r["success"]

    def test_binary_file(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "binary.bin"), "wb") as fp:
                fp.write(b'\x00\x01\x02\x03HelloWorld\xff\xfe\xfd')
            r = self.gt._cmd_grep(query="Hello", path=d, output_mode="files_with_matches")
            assert r["success"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_empty_file(self):
        d = tempfile.mkdtemp()
        try:
            open(os.path.join(d, "empty.txt"), "w").close()
            r = self.gt._cmd_grep(query="anything", path=d, output_mode="files_with_matches")
            assert r["success"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_large_file(self):
        d = tempfile.mkdtemp()
        try:
            big = os.path.join(d, "big.txt")
            with open(big, "w") as fp:
                fp.write("x" * (5 * 1024 * 1024))
            r = self.gt._cmd_grep(query="x", path=big, output_mode="content", head_limit=1)
            assert r["success"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_mtime_sorted(self):
        d = tempfile.mkdtemp()
        try:
            names = ["old.txt", "mid.txt", "new.txt"]
            for i, name in enumerate(names):
                fp = os.path.join(d, name)
                with open(fp, "w") as f:
                    f.write(f"content {name}\n")
                os.utime(fp, (time.time() - 100 + i * 10, time.time() - 100 + i * 10))
            r = self.gt._cmd_grep(query="content", path=d, output_mode="files_with_matches", head_limit=10)
            assert r["success"] and r["num_files"] == 3
            if r["num_files"] >= 1:
                assert "new.txt" in r["filenames"][0]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_special_chars_in_query(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "test.txt"), "w") as fp:
                fp.write("line with $pecial ch@r$ & more\n")
            r = self.gt._cmd_grep(query="\\$pecial", path=d, output_mode="content", head_limit=5)
            assert r["success"] and r["num_lines"] >= 1
            r = self.gt._cmd_grep(query="ch@r", path=d, output_mode="content", head_limit=5)
            assert r["success"] and r["num_lines"] >= 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_invalid_output_mode(self):
        r = self.gt._cmd_grep(query="def", path=".", output_mode="invalid", head_limit=5)
        assert r["success"]

    def test_no_keyword_error(self):
        r = self.gt._cmd_grep(query="", path=".", output_mode="files_with_matches")
        assert not r["success"]

    def test_no_pattern_error(self):
        r = self.gt._cmd_grep(pattern="", query="", path=".", output_mode="files_with_matches")
        assert not r["success"]

    def test_query_space(self):
        r = self.gt._cmd_grep(query=" ", path=".", output_mode="files_with_matches")
        assert r["success"]


class TestFallbackSearch:

    def setup_method(self):
        self.d = tempfile.mkdtemp()
        with open(os.path.join(self.d, "a.py"), "w") as f:
            f.write("def hello():\n    return 'hi'\n\ndef world():\n    return 'earth'\n")
        with open(os.path.join(self.d, "b.txt"), "w") as f:
            f.write("Hello World\n")

    def teardown_method(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_basic(self):
        r = _fallback_search(query="hello", path=self.d, ext=None, pattern=None, offset=0, head_limit=10)
        assert r["success"] and r["total"] >= 1
        assert r["engine"] == "fallback"

    def test_ext_filter(self):
        r = _fallback_search(query="hello", path=self.d, ext=".py", pattern=None, offset=0, head_limit=10)
        assert r["success"] and r["total"] >= 1
        r = _fallback_search(query="hello", path=self.d, ext=".cpp", pattern=None, offset=0, head_limit=10)
        assert r["success"] and r["total"] == 0

    def test_glob_filter(self):
        r = _fallback_search(query="hello", path=self.d, ext=None, pattern="*.py", offset=0, head_limit=10)
        assert r["success"] and r["total"] >= 1
        r = _fallback_search(query="hello", path=self.d, ext=None, pattern="*.cpp", offset=0, head_limit=10)
        assert r["success"] and r["total"] == 0

    def test_regex(self):
        r = _fallback_search(query="def\\s+\\w+", path=self.d, ext=".py", pattern=None, offset=0, head_limit=10)
        assert r["success"] and r["total"] >= 2

    def test_empty_query(self):
        r = _fallback_search(query="", path=self.d, ext=None, pattern=None, offset=0, head_limit=10)
        assert r["success"]

    def test_nonexistent_path(self):
        r = _fallback_search(query="hello", path="C:\\__nonexistent__", ext=None, pattern=None, offset=0, head_limit=10)
        assert r["success"] and r["total"] == 0

    def test_offset(self):
        r = _fallback_search(query="def", path=self.d, ext=".py", pattern=None, offset=1, head_limit=1)
        assert r["success"]

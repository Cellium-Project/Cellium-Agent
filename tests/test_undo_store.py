# -*- coding: utf-8 -*-
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from app.core.util import undo_store as us


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = us.UndoStore()
        self.store._root = os.path.join(self.tmp, "undo")
        os.makedirs(self.store._root, exist_ok=True)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.work, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


class TestCopyRollback(_Base):

    def test_shell_copy_restores_modified_deleted_created(self):
        keep_a = self._write("a.txt", "v1")
        keep_b = self._write("b.txt", "keep")
        with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
            self.store.checkpoint_shell("s1", "t1", self.work)
        self._write("a.txt", "v2")
        os.remove(keep_b)
        self._write("c.txt", "new")
        result = self.store.rollback("s1", [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(self._read(keep_a), "v1")
        self.assertTrue(os.path.exists(keep_b))
        self.assertFalse(os.path.exists(os.path.join(self.work, "c.txt")))
        files = {os.path.basename(f["path"]): f for f in result["files"]}
        self.assertEqual(files["a.txt"]["kind"], "modified")
        self.assertEqual(files["a.txt"]["added"], 1)
        self.assertEqual(files["a.txt"]["removed"], 1)
        self.assertEqual(files["b.txt"]["kind"], "restored")
        self.assertEqual(files["c.txt"]["kind"], "deleted")

    def test_preview_does_not_modify_files(self):
        path = self._write("p.txt", "v1")
        with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
            self.store.checkpoint_shell("sp", "t1", self.work)
        self._write("p.txt", "v2")
        preview = self.store.preview("sp", [])
        self.assertEqual(self._read(path), "v2")
        row = [f for f in preview["files"] if os.path.basename(f["path"]) == "p.txt"][0]
        self.assertEqual((row["added"], row["removed"]), (1, 1))
        self.assertTrue(os.path.isdir(os.path.join(self.store._root, "sp", "t1")))
        self.store.rollback("sp", [])
        self.assertEqual(self._read(path), "v1")

    def test_paths_restore_and_delete(self):
        exist = self._write("d.txt", "old")
        missing = os.path.join(self.work, "e.txt")
        self.store.checkpoint_paths("s2", "t1", [exist, missing])
        self._write("d.txt", "new")
        self._write("e.txt", "created")
        result = self.store.rollback("s2", [])
        self.assertEqual(self._read(exist), "old")
        self.assertFalse(os.path.exists(missing))
        self.assertGreaterEqual(result["restored"], 2)

    def test_keep_turn_not_rolled_back(self):
        self.store.begin_turn("s3", "t_keep")
        self.store.begin_turn("s3", "t_drop")
        path = self._write("f.txt", "v1")
        self.store.checkpoint_paths("s3", "t_drop", [path])
        self._write("f.txt", "v2")
        self.store.rollback("s3", ["t_keep"])
        self.assertEqual(self._read(path), "v1")
        self.assertTrue(os.path.isdir(os.path.join(self.store._root, "s3", "t_keep")))


class TestDropSession(_Base):

    def test_drop_session_removes_snapshots(self):
        self.store.begin_turn("s_drop", "t1")
        self.store.checkpoint_paths("s_drop", "t1", [self._write("x.txt", "v")])
        self.assertTrue(os.path.isdir(os.path.join(self.store._root, "s_drop")))
        self.store.drop_session("s_drop")
        self.assertFalse(os.path.isdir(os.path.join(self.store._root, "s_drop")))
        self.store.drop_session("not-exist")


class TestRetention(_Base):

    def test_keeps_latest_five_turns(self):
        for i in range(6):
            self.store.begin_turn("s4", f"t{i}")
            time.sleep(0.01)
        turns = sorted(os.listdir(os.path.join(self.store._root, "s4")))
        self.assertEqual(len(turns), 5)
        self.assertNotIn("t0", turns)
        self.assertIn("t5", turns)


@unittest.skipUnless(shutil.which("git"), "git 不可用")
class TestGitRollback(_Base):

    def _git(self, *args):
        subprocess.run(["git", "-C", self.work, *args], check=True,
                       capture_output=True, text=True)

    def test_git_checkpoint_restores_modified_and_new(self):
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "tester")
        tracked = self._write("tracked.txt", "base")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s5", "t1", self.work)
        self._write("tracked.txt", "changed")
        self._write("new.txt", "untracked")
        result = self.store.rollback("s5", [])
        self.assertEqual(self._read(tracked), "base")
        self.assertFalse(os.path.exists(os.path.join(self.work, "new.txt")))
        self.assertEqual(result["skipped"], [])
        kinds = {os.path.basename(f["path"]): f["kind"] for f in result["files"]}
        self.assertEqual(kinds["tracked.txt"], "modified")
        self.assertEqual(kinds["new.txt"], "deleted")


if __name__ == "__main__":
    unittest.main()

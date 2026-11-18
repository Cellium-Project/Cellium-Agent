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
        self.store.checkpoint_paths("s3", "t_keep", [self._write("keep.txt", "k")])
        self.store.begin_turn("s3", "t_drop")
        path = self._write("f.txt", "v1")
        self.store.checkpoint_paths("s3", "t_drop", [path])
        self._write("f.txt", "v2")
        self.store.rollback("s3", ["t_keep"])
        self.assertEqual(self._read(path), "v1")
        self.assertTrue(os.path.isdir(os.path.join(self.store._root, "s3", "t_keep")))


class TestTracking(_Base):

    def test_plain_turn_is_untracked(self):
        self.store.begin_turn("s_tr", "t_chat")
        self.assertFalse(self.store.has_tracking("s_tr", "t_chat"))
        self.assertEqual(self.store.tracked_turn_ids("s_tr"), set())

    def test_turns_with_snapshot_are_tracked(self):
        self.store.begin_turn("s_tr", "t_chat")
        self.store.checkpoint_paths("s_tr", "t_files", [os.path.join(self.work, "probe.txt")])
        with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
            self.store.checkpoint_shell("s_tr", "t_shell", self.work)
        ids = self.store.tracked_turn_ids("s_tr")
        self.assertEqual(ids, {"t_files", "t_shell"})
        self.assertTrue(self.store.has_tracking("s_tr", "t_files"))
        self.assertFalse(self.store.has_tracking("s_tr", "t_chat"))

    def test_skipped_shell_is_untracked(self):
        with patch.object(us.UndoStore, "_measure", lambda self, base: (us.COPY_MAX_FILES + 1, 0)):
            with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
                self.store.checkpoint_shell("s_sk", "t1", self.work)
        self.assertEqual(self.store.tracked_turn_ids("s_sk"), set())

    def test_preview_unknown_session_leaves_no_dir(self):
        result = self.store.preview("ghost", [])
        self.assertEqual(result["files"], [])
        self.assertFalse(os.path.isdir(os.path.join(self.store._root, "ghost")))
        result = self.store.rollback("ghost", [])
        self.assertEqual(result["files"], [])
        self.assertFalse(os.path.isdir(os.path.join(self.store._root, "ghost")))

    def test_safe_name_collision_keeps_turns_distinct(self):
        p1 = self._write("c1.txt", "1")
        p2 = self._write("c2.txt", "2")
        self.store.begin_turn("s_n", "a/1")
        self.store.checkpoint_paths("s_n", "a/1", [p1])
        self.store.begin_turn("s_n", "a_1")
        self.store.checkpoint_paths("s_n", "a_1", [p2])
        self.assertEqual(self.store.tracked_turn_ids("s_n"), {"a/1", "a_1"})
        self.assertTrue(self.store.has_tracking("s_n", "a/1"))
        self.assertTrue(self.store.has_tracking("s_n", "a_1"))

    def test_corrupt_manifest_drops_orphan_backups(self):
        path = self._write("o.txt", "v")
        self.store.checkpoint_paths("s_bad", "t1", [path])
        tdir = os.path.join(self.store._root, "s_bad", self.store._safe_name("t1"))
        with open(os.path.join(tdir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write("{")
        self.store.begin_turn("s_bad", "t1")
        self.assertEqual(os.listdir(os.path.join(tdir, "files")), [])
        self.assertFalse(self.store.has_tracking("s_bad", "t1"))


class TestDropSession(_Base):

    def test_drop_session_removes_snapshots(self):
        self.store.begin_turn("s_drop", "t1")
        self.store.checkpoint_paths("s_drop", "t1", [self._write("x.txt", "v")])
        self.assertTrue(os.path.isdir(os.path.join(self.store._root, "s_drop")))
        self.store.drop_session("s_drop")
        self.assertFalse(os.path.isdir(os.path.join(self.store._root, "s_drop")))
        self.store.drop_session("not-exist")


class TestRetention(_Base):

    def test_keeps_latest_seven_tracked_turns(self):
        for i in range(9):
            path = self._write(f"r{i}.txt", f"v{i}")
            self.store.begin_turn("s4", f"t{i}")
            self.store.checkpoint_paths("s4", f"t{i}", [path])
            time.sleep(0.01)
        turns = sorted(os.listdir(os.path.join(self.store._root, "s4")))
        self.assertEqual(len(turns), 7)
        self.assertNotIn("t0", turns)
        self.assertNotIn("t1", turns)
        self.assertIn("t8", turns)
        self.assertEqual(self.store.tracked_turn_ids("s4"), {f"t{i}" for i in range(2, 9)})

    def test_untracked_turns_do_not_linger_or_squeeze(self):
        for i in range(6):
            self.store.begin_turn("s4b", f"c{i}")
            time.sleep(0.01)
        for i in range(7):
            path = self._write(f"k{i}.txt", f"v{i}")
            self.store.begin_turn("s4b", f"t{i}")
            self.store.checkpoint_paths("s4b", f"t{i}", [path])
            time.sleep(0.01)
        turns = set(os.listdir(os.path.join(self.store._root, "s4b")))
        self.assertEqual(turns, {f"t{i}" for i in range(7)})


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

    def _init_repo(self):
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "tester")

    def test_git_preview_direction_matches_undo(self):
        """纯追加改动：预览应显示回滚会删除 2 行"""
        self._init_repo()
        tracked = self._write("t.txt", "a\nb\nc\n")
        self._git("add", "t.txt")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s7", "t1", self.work)
        self._write("t.txt", "a\nb\nc\nd\ne\n")
        preview = self.store.preview("s7", [])
        row = [f for f in preview["files"] if os.path.basename(f["path"]) == "t.txt"][0]
        self.assertEqual((row["added"], row["removed"]), (0, 2))

    def test_git_rename_rollback(self):
        """git mv 后撤回：旧文件恢复，新文件删除"""
        self._init_repo()
        old = self._write("old.txt", "base\n")
        self._git("add", "old.txt")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s8", "t1", self.work)
        self._git("mv", "old.txt", "new.txt")
        result = self.store.rollback("s8", [])
        self.assertTrue(os.path.isfile(old))
        self.assertEqual(self._read(old), "base\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, "new.txt")))
        del result

    def test_git_unicode_path_rollback(self):
        self._init_repo()
        tracked = self._write("中文 文件.txt", "base\n")
        self._git("add", "中文 文件.txt")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s9", "t1", self.work)
        self._write("中文 文件.txt", "base\nchanged\n")
        preview = self.store.preview("s9", [])
        self.assertTrue(any(
            os.path.basename(f["path"]) == "中文 文件.txt" for f in preview["files"]
        ))
        self.store.rollback("s9", [])
        self.assertEqual(self._read(tracked), "base\n")

    def test_git_restore_keeps_unstaged_status(self):
        """撤回只恢复工作区，不应把改动变成已暂存"""
        self._init_repo()
        self._write("t.txt", "a\n")
        self._git("add", "t.txt")
        self._git("commit", "-m", "init")
        self._write("t.txt", "a\nb\n")
        self.store.checkpoint_shell("s10", "t1", self.work)
        self._write("t.txt", "a\nb\nc\n")
        self.store.rollback("s10", [])
        status = subprocess.run(
            ["git", "-C", self.work, "status", "--porcelain"],
            capture_output=True, text=True,
        ).stdout
        # 未暂存应为 " M t.txt"，已暂存是 "M  t.txt"
        self.assertTrue(status.startswith(" M"), repr(status))

    def test_git_binary_preview_flag(self):
        self._init_repo()
        path = os.path.join(self.work, "bin.dat")
        with open(path, "wb") as f:
            f.write(b"\x00\x01\x02")
        self._git("add", "bin.dat")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s11", "t1", self.work)
        with open(path, "wb") as f:
            f.write(b"\x00\x01\x03\x04")
        preview = self.store.preview("s11", [])
        row = [f for f in preview["files"] if os.path.basename(f["path"]) == "bin.dat"][0]
        self.assertTrue(row.get("binary"))

    def test_copy_rollback_removes_new_empty_dir(self):
        self._write("a.txt", "v1")
        with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
            self.store.checkpoint_shell("s12", "t1", self.work)
        new_dir = os.path.join(self.work, "new_empty")
        os.makedirs(new_dir)
        self.store.rollback("s12", [])
        self.assertFalse(os.path.isdir(new_dir))

    def test_git_preview_single_subprocess_per_repo(self):
        """多轮预览只跑一次 status / numstat，避免弹窗卡顿"""
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "tester")
        self._write("tracked.txt", "base")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "init")
        self.store.checkpoint_shell("s6", "t1", self.work)
        self._write("tracked.txt", "changed\nline2")
        self.store.checkpoint_shell("s6", "t2", self.work)

        calls = []
        real_run = subprocess.run

        def spy(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, (list, tuple)) and len(cmd) > 3 and cmd[1] == "-C":
                if "status" in cmd:
                    calls.append("status")
                elif "diff" in cmd and "--numstat" in cmd:
                    calls.append("numstat")
            return real_run(*args, **kwargs)

        with patch.object(us.subprocess, "run", side_effect=spy):
            preview = self.store.preview("s6", [])
        self.assertEqual(calls.count("status"), 1, calls)
        self.assertEqual(calls.count("numstat"), 1, calls)
        row = [f for f in preview["files"] if os.path.basename(f["path"]) == "tracked.txt"][0]
        self.assertGreater(row["added"] + row["removed"], 0)


@unittest.skipUnless(shutil.which("git"), "git 不可用")
class TestGitDirCheckpointPaths(_Base):
    """git 仓库内的文件级 checkpoint（edit / file 工具走的 checkpoint_paths）"""

    def _git(self, *args):
        subprocess.run(["git", "-C", self.work, *args], check=True,
                       capture_output=True, text=True)

    def _init_repo(self):
        self._git("init")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "tester")

    def test_edit_single_file_restore(self):
        """git 目录里 edit 单文件：回滚恢复原内容"""
        self._init_repo()
        path = self._write("code.py", "v1\n")
        self._git("add", "code.py")
        self._git("commit", "-m", "init")

        self.store.begin_turn("s_g1", "t1")
        self.assertFalse(self.store.has_tracking("s_g1", "t1"))
        self.store.checkpoint_paths("s_g1", "t1", [path])
        self.assertTrue(self.store.has_tracking("s_g1", "t1"))

        self._write("code.py", "v2\n")
        result = self.store.rollback("s_g1", [])
        self.assertEqual(self._read(path), "v1\n")
        self.assertEqual(result["restored"], 1)
        self.assertFalse(self.store.has_tracking("s_g1", "t1"))

    def test_new_file_deleted_after_rollback(self):
        """git 目录里新增文件：checkpoint 时不存在，回滚后删除"""
        self._init_repo()
        self._write("base.txt", "x")
        self._git("add", "base.txt")
        self._git("commit", "-m", "init")

        created = os.path.join(self.work, "new.txt")
        self.store.begin_turn("s_g2", "t1")
        self.store.checkpoint_paths("s_g2", "t1", [created])
        self._write("new.txt", "created")

        self.store.rollback("s_g2", [])
        self.assertFalse(os.path.exists(created))

    def test_multi_file_checkpoint_restores_all(self):
        """file fs create 多文件：全部恢复"""
        self._init_repo()
        a = self._write("a.py", "a1")
        b = self._write("b.py", "b1")
        self._git("add", ".")
        self._git("commit", "-m", "init")

        self.store.begin_turn("s_g3", "t1")
        self.store.checkpoint_paths("s_g3", "t1", [a, b])

        self._write("a.py", "a2")
        self._write("b.py", "b2")

        result = self.store.rollback("s_g3", [])
        self.assertEqual(self._read(a), "a1")
        self.assertEqual(self._read(b), "b1")
        self.assertEqual(result["restored"], 2)

    def test_duplicate_checkpoint_dedup(self):
        """同一 turn 对同一文件重复 checkpoint 只记一次"""
        self._init_repo()
        path = self._write("d.txt", "d1")
        self._git("add", "d.txt")
        self._git("commit", "-m", "init")

        self.store.begin_turn("s_g4", "t1")
        self.store.checkpoint_paths("s_g4", "t1", [path])
        self.store.checkpoint_paths("s_g4", "t1", [path])

        manifest = self.store._load(os.path.join(self.store._root, "s_g4", "t1"))
        self.assertEqual(len(manifest["entries"]), 1)

    def test_keep_turn_not_rolled_back(self):
        """多轮：keep 的 turn 不回滚，其余回滚"""
        self._init_repo()
        f1 = self._write("f1.txt", "1")
        f2 = self._write("f2.txt", "2")
        self._git("add", ".")
        self._git("commit", "-m", "init")

        self.store.begin_turn("s_g5", "t1")
        self.store.checkpoint_paths("s_g5", "t1", [f1])
        self.store.begin_turn("s_g5", "t2")
        self.store.checkpoint_paths("s_g5", "t2", [f2])

        self._write("f1.txt", "1x")
        self._write("f2.txt", "2x")

        self.store.rollback("s_g5", ["t1"])
        self.assertEqual(self._read(f1), "1x")
        self.assertEqual(self._read(f2), "2")
        self.assertTrue(self.store.has_tracking("s_g5", "t1"))
        self.assertFalse(self.store.has_tracking("s_g5", "t2"))

    def test_git_shell_and_paths_combined(self):
        """git 基线 + 文件级快照并存：一并回滚"""
        self._init_repo()
        tracked = self._write("tracked.txt", "base\n")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "init")

        self.store.checkpoint_shell("s_g6", "t1", self.work)
        self.store.checkpoint_paths("s_g6", "t1", [tracked])

        self._write("tracked.txt", "changed\n")
        self._write("extra.txt", "extra")

        result = self.store.rollback("s_g6", [])
        self.assertEqual(self._read(tracked), "base\n")
        self.assertFalse(os.path.exists(os.path.join(self.work, "extra.txt")))
        self.assertGreaterEqual(result["restored"], 1)


@unittest.skipUnless(shutil.which("git"), "git 不可用")
class TestNonGitCheckpointPaths(_Base):
    """非 git 目录的文件级 checkpoint（对比组）"""

    def test_edit_file_restore_without_git(self):
        path = self._write("n.txt", "orig")
        with patch.object(us.UndoStore, "_git_toplevel", staticmethod(lambda cwd: None)):
            self.store.begin_turn("s_n1", "t1")
            self.store.checkpoint_paths("s_n1", "t1", [path])
        self._write("n.txt", "mod")
        self.store.rollback("s_n1", [])
        self.assertEqual(self._read(path), "orig")


if __name__ == "__main__":
    unittest.main()

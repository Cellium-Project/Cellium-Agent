# -*- coding: utf-8 -*-
import filecmp
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time

from app.core.util.runtime_paths import resolve_dir_writable

logger = logging.getLogger(__name__)

MAX_TURNS = 7
COPY_MAX_BYTES = 100 * 1024 * 1024
COPY_MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
# 行数差异计算的规模上限，超过后退化估算，避免 SequenceMatcher 卡住
DIFF_MAX_CELLS = 4_000_000
EXCLUDE_DIRS = {
    ".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".cache",
}


class UndoStore:

    def __init__(self):
        self._lock = threading.RLock()
        self._root = os.path.join(resolve_dir_writable("workspace"), ".cache", "undo")
        try:
            os.makedirs(self._root, exist_ok=True)
        except Exception as e:
            logger.warning("[UndoStore] 创建存储根失败: %s", e)

    @staticmethod
    def _safe_name(value) -> str:
        raw = str(value or "")
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", raw).strip("._")
        if not safe:
            return "default"
        if safe != raw:
            digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
            return f"{safe}_{digest}"
        return safe

    def _session_dir(self, session_id) -> str:
        path = os.path.join(self._root, self._safe_name(session_id))
        os.makedirs(path, exist_ok=True)
        return path

    def _turn_dir(self, session_id, turn_id) -> str:
        path = os.path.join(self._session_dir(session_id), self._safe_name(turn_id))
        os.makedirs(os.path.join(path, "files"), exist_ok=True)
        return path

    @staticmethod
    def _manifest_path(turn_dir: str) -> str:
        return os.path.join(turn_dir, "manifest.json")

    def _load(self, turn_dir: str):
        try:
            with open(self._manifest_path(turn_dir), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return None

    def _save(self, turn_dir: str, manifest: dict) -> None:
        path = self._manifest_path(turn_dir)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _ensure(self, session_id, turn_id):
        turn_dir = self._turn_dir(session_id, turn_id)
        manifest = self._load(turn_dir)
        if manifest is None:
            shutil.rmtree(os.path.join(turn_dir, "files"), ignore_errors=True)
            os.makedirs(os.path.join(turn_dir, "files"), exist_ok=True)
            manifest = {
                "turn_id": str(turn_id),
                "created_at": time.time(),
                "entries": [],
                "git": None,
                "copy_root": None,
                "skipped_reason": None,
            }
            self._save(turn_dir, manifest)
        manifest.setdefault("entries", [])
        return turn_dir, manifest

    def begin_turn(self, session_id, turn_id) -> None:
        try:
            with self._lock:
                turn_dir, manifest = self._ensure(session_id, turn_id)
                self._save(turn_dir, manifest)
                self._prune(self._session_dir(session_id))
        except Exception as e:
            logger.warning("[UndoStore] begin_turn 失败: %s", e)

    def _prune(self, session_dir: str) -> None:
        items = []
        for name in os.listdir(session_dir):
            turn_dir = os.path.join(session_dir, name)
            if not os.path.isdir(turn_dir):
                continue
            manifest = self._load(turn_dir) or {}
            created = manifest.get("created_at")
            if not isinstance(created, (int, float)):
                try:
                    created = os.path.getmtime(turn_dir)
                except Exception:
                    created = 0.0
            items.append((float(created), turn_dir, manifest))
        items.sort(key=lambda x: x[0], reverse=True)
        if not items:
            return
        # 最新轮次可能还在写快照，先为它预留一个名额
        newest_tracked = self._has_snapshot(items[0][2])
        limit = MAX_TURNS if newest_tracked else MAX_TURNS - 1
        kept = 0
        for idx, (_, turn_dir, manifest) in enumerate(items):
            tracked = self._has_snapshot(manifest)
            if tracked:
                kept += 1
                if kept <= limit:
                    continue
            elif idx == 0:
                continue
            shutil.rmtree(turn_dir, ignore_errors=True)

    @staticmethod
    def _has_snapshot(manifest) -> bool:
        manifest = manifest or {}
        return bool(
            manifest.get("entries")
            or manifest.get("git")
            or manifest.get("copy_root")
        )

    def has_tracking(self, session_id, turn_id) -> bool:
        try:
            with self._lock:
                turn_dir = os.path.join(
                    self._root, self._safe_name(session_id), self._safe_name(turn_id)
                )
                return self._has_snapshot(self._load(turn_dir) or {})
        except Exception:
            return False

    def tracked_turn_ids(self, session_id) -> set:
        ids = set()
        try:
            with self._lock:
                session_dir = os.path.join(self._root, self._safe_name(session_id))
                if not os.path.isdir(session_dir):
                    return ids
                for name in os.listdir(session_dir):
                    turn_dir = os.path.join(session_dir, name)
                    if not os.path.isdir(turn_dir):
                        continue
                    manifest = self._load(turn_dir) or {}
                    if self._has_snapshot(manifest):
                        ids.add(str(manifest.get("turn_id") or name))
        except Exception as e:
            logger.warning("[UndoStore] 读取追踪失败: %s", e)
        return ids

    @staticmethod
    def _ignore(_src, names):
        return {n for n in names if n in EXCLUDE_DIRS}

    def _measure(self, base: str):
        count = 0
        total = 0
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                full = os.path.join(root, name)
                try:
                    total += os.path.getsize(full)
                except Exception:
                    continue
                count += 1
                if count > COPY_MAX_FILES or total > COPY_MAX_BYTES:
                    return count, total
        return count, total

    @staticmethod
    def _norm(path: str):
        if not path:
            return None
        try:
            return os.path.abspath(os.path.expanduser(str(path)))
        except Exception:
            return None

    def checkpoint_paths(self, session_id, turn_id, paths) -> None:
        try:
            with self._lock:
                turn_dir, manifest = self._ensure(session_id, turn_id)
                entries = manifest["entries"]
                known = {e.get("path") for e in entries if isinstance(e, dict)}
                index = len(entries)
                for raw in paths or []:
                    full = self._norm(raw)
                    if not full or full in known:
                        continue
                    known.add(full)
                    entry = {"path": full}
                    try:
                        if os.path.isfile(full):
                            size = os.path.getsize(full)
                            if size > MAX_FILE_BYTES:
                                entry.update({"existed": True, "kind": "file", "backup": None, "skipped": True})
                            else:
                                rel = f"files/{index}_{os.path.basename(full) or 'f'}"
                                dst = os.path.join(turn_dir, *rel.split("/"))
                                shutil.copy2(full, dst)
                                entry.update({"existed": True, "kind": "file", "backup": rel})
                                index += 1
                        elif os.path.isdir(full):
                            count, total = self._measure(full)
                            if count > COPY_MAX_FILES or total > COPY_MAX_BYTES:
                                entry.update({"existed": True, "kind": "dir", "backup": None, "skipped": True})
                            else:
                                name = os.path.basename(os.path.normpath(full)) or "d"
                                rel = f"files/{index}_{name}"
                                dst = os.path.join(turn_dir, *rel.split("/"))
                                shutil.copytree(full, dst, ignore=self._ignore, dirs_exist_ok=True)
                                entry.update({"existed": True, "kind": "dir", "backup": rel})
                                index += 1
                        else:
                            entry.update({"existed": False})
                    except Exception as e:
                        logger.warning("[UndoStore] 备份失败 path=%s error=%s", full, e)
                        entry.update({"existed": True, "backup": None, "skipped": True})
                    entries.append(entry)
                self._save(turn_dir, manifest)
        except Exception as e:
            logger.warning("[UndoStore] checkpoint_paths 失败: %s", e)

    @staticmethod
    def _git_toplevel(cwd: str):
        try:
            proc = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                out = (proc.stdout or "").strip()
                if out and os.path.isdir(out):
                    return out
        except Exception:
            pass
        return None

    def checkpoint_shell(self, session_id, turn_id, cwd: str) -> None:
        try:
            with self._lock:
                turn_dir, manifest = self._ensure(session_id, turn_id)
                if manifest.get("git") or manifest.get("copy_root") or manifest.get("skipped_reason"):
                    return
                base = self._norm(cwd or os.getcwd())
                if not base or not os.path.isdir(base):
                    return
                repo = self._git_toplevel(base)
                if repo:
                    sha = ""
                    try:
                        proc = subprocess.run(
                            ["git", "-C", repo, "stash", "create"],
                            capture_output=True, text=True, timeout=60,
                        )
                        if proc.returncode == 0:
                            sha = (proc.stdout or "").strip()
                    except Exception as e:
                        logger.warning("[UndoStore] stash create 失败: %s", e)
                    baseline = []
                    try:
                        proc = subprocess.run(
                            ["git", "-C", repo, "-c", "core.quotepath=false",
                             "status", "--porcelain", "-uall"],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60,
                        )
                        if proc.returncode == 0:
                            baseline = (proc.stdout or "").splitlines()
                    except Exception as e:
                        logger.warning("[UndoStore] git status 失败: %s", e)
                    manifest["git"] = {"repo": repo, "sha": sha, "baseline": baseline}
                else:
                    count, total = self._measure(base)
                    if count > COPY_MAX_FILES or total > COPY_MAX_BYTES:
                        manifest["skipped_reason"] = "too_large"
                    else:
                        root = os.path.join(turn_dir, "root")
                        shutil.copytree(base, root, ignore=self._ignore, dirs_exist_ok=True)
                        manifest["copy_root"] = "root"
                        manifest["copy_cwd"] = base
                self._save(turn_dir, manifest)
        except Exception as e:
            logger.warning("[UndoStore] checkpoint_shell 失败: %s", e)

    @staticmethod
    def _unquote(text: str) -> str:
        text = text.strip()
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            try:
                return json.loads(text)
            except Exception:
                return text[1:-1]
        return text

    @classmethod
    def _porcelain_paths(cls, line: str):
        if not line or len(line) < 4:
            return []
        text = line[3:]
        if " -> " in text:
            old, new = text.split(" -> ", 1)
            return [cls._unquote(old), cls._unquote(new)]
        return [cls._unquote(text)]

    @staticmethod
    def _read_text(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

    @staticmethod
    def _count_lines(text):
        if not text:
            return 0
        return text.count("\n") + (0 if text.endswith("\n") else 1)

    @staticmethod
    def _diff_stats(old_text, new_text):
        import difflib
        if old_text is None or new_text is None:
            return None
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        # 先剪掉公共前后缀，大文件通常只改一小段
        start = 0
        limit = min(len(old_lines), len(new_lines))
        while start < limit and old_lines[start] == new_lines[start]:
            start += 1
        end_old, end_new = len(old_lines), len(new_lines)
        while (
            end_old > start and end_new > start
            and old_lines[end_old - 1] == new_lines[end_new - 1]
        ):
            end_old -= 1
            end_new -= 1
        mid_old = old_lines[start:end_old]
        mid_new = new_lines[start:end_new]
        if len(mid_old) * len(mid_new) > DIFF_MAX_CELLS:
            return len(mid_new), len(mid_old)
        added = removed = 0
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mid_old, mid_new).get_opcodes():
            if tag in ("replace", "delete"):
                removed += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
        return added, removed

    @staticmethod
    def _record_file(result, path, added=0, removed=0, kind="modified", binary=False) -> None:
        result.setdefault("files", []).append({
            "path": path,
            "added": int(added or 0),
            "removed": int(removed or 0),
            "kind": kind,
            "binary": bool(binary),
        })

    def _restore_entries(self, turn_dir, manifest, result, apply=True) -> None:
        for entry in reversed(manifest.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not path:
                continue
            try:
                if entry.get("existed"):
                    backup = entry.get("backup")
                    if not backup:
                        result["skipped"].append(path)
                        continue
                    src = os.path.join(turn_dir, *str(backup).split("/"))
                    if not os.path.exists(src):
                        result["skipped"].append(path)
                        continue
                    if os.path.isdir(src):
                        if apply:
                            parent = os.path.dirname(path)
                            if parent:
                                os.makedirs(parent, exist_ok=True)
                            if os.path.isfile(path):
                                os.remove(path)
                            shutil.copytree(src, path, dirs_exist_ok=True)
                    else:
                        current = self._read_text(path) if os.path.isfile(path) else None
                        backup_text = self._read_text(src)
                        stats = self._diff_stats(current, backup_text)
                        if stats is None and backup_text is not None:
                            stats = (self._count_lines(backup_text), 0)
                        if apply:
                            parent = os.path.dirname(path)
                            if parent:
                                os.makedirs(parent, exist_ok=True)
                            if os.path.isdir(path):
                                shutil.rmtree(path, ignore_errors=True)
                            shutil.copy2(src, path)
                        result["restored"] += 1
                        kind = "modified" if current is not None else "restored"
                        self._record_file(result, path, *(stats or (0, 0)), kind=kind)
                else:
                    if os.path.exists(path):
                        if os.path.isdir(path):
                            if apply:
                                shutil.rmtree(path, ignore_errors=True)
                        else:
                            removed = self._count_lines(self._read_text(path))
                            if apply:
                                os.remove(path)
                            self._record_file(result, path, 0, removed, kind="deleted")
                        result["restored"] += 1
            except Exception as e:
                logger.warning("[UndoStore] 还原失败 path=%s error=%s", path, e)
                result["skipped"].append(path)

    @staticmethod
    def _git_numstat(repo, rev):
        """一次 git diff 拿到全部文件的增删行数（已按“回滚视角”换算）"""
        stats = {}
        try:
            proc = subprocess.run(
                ["git", "-C", repo, "-c", "core.quotepath=false",
                 "diff", "--numstat", "--no-renames", rev, "--"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60,
            )
            if proc.returncode != 0:
                return stats
            for line in (proc.stdout or "").splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                added, removed, rel = parts[0], parts[1], "\t".join(parts[2:])
                is_bin = not added.isdigit() or not removed.isdigit()
                # numstat 是“快照 → 工作区”，回滚要反过来看
                stats[os.path.normpath(rel)] = (
                    int(removed) if removed.isdigit() else 0,
                    int(added) if added.isdigit() else 0,
                    is_bin,
                )
        except Exception:
            pass
        return stats

    @staticmethod
    def _git_tree_paths(repo, rev):
        """快照内所有文件路径（一次 ls-tree），失败返回 None"""
        try:
            proc = subprocess.run(
                ["git", "-C", repo, "-c", "core.quotepath=false",
                 "ls-tree", "-r", "--name-only", rev],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60,
            )
            if proc.returncode != 0:
                return None
            return {
                os.path.normpath(p.strip())
                for p in (proc.stdout or "").splitlines() if p.strip()
            }
        except Exception:
            return None

    def _remove_path(self, full, result, apply=True):
        try:
            if os.path.isdir(full):
                if apply:
                    shutil.rmtree(full, ignore_errors=True)
                result["restored"] += 1
            elif os.path.exists(full):
                removed = self._count_lines(self._read_text(full))
                if apply:
                    os.remove(full)
                self._record_file(result, full, 0, removed, kind="deleted")
                result["restored"] += 1
        except Exception as e:
            logger.warning("[UndoStore] 删除多余文件失败 path=%s error=%s", full, e)
            result["skipped"].append(full)

    @staticmethod
    def _git_restore_worktree(repo, rev, rel) -> bool:
        """只恢复工作区，不动 index，避免撤回后改动被误标为已暂存"""
        commands = (
            ["restore", f"--source={rev}", "--worktree", "--", rel],
            ["checkout", rev, "--", rel],
        )
        for args in commands:
            try:
                proc = subprocess.run(
                    ["git", "-C", repo, "-c", "core.quotepath=false", *args],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=60,
                )
            except Exception:
                continue
            if proc.returncode == 0:
                return True
        return False

    def _git_restore_path(self, repo, rev, rel, result, apply, numstat, tree, baseline):
        full = os.path.join(repo, rel)
        key = os.path.normpath(rel)
        if tree is not None and key not in tree:
            # 快照里没有：本轮回合新增，回滚即删除
            if key in baseline:
                result["skipped"].append(full)
                return
            self._remove_path(full, result, apply=apply)
            return
        existed = os.path.isfile(full)
        if apply and not self._git_restore_worktree(repo, rev, rel):
            logger.warning("[UndoStore] git 恢复失败 path=%s", rel)
            result["skipped"].append(full)
            return
        result["restored"] += 1
        stats = numstat.get(key) or (0, 0, False)
        kind = "modified" if existed else "restored"
        self._record_file(result, full, stats[0], stats[1], kind=kind, binary=stats[2])

    def _restore_git(self, git, result, apply=True, cache=None, stats_rev=None) -> None:
        cache = cache if cache is not None else {}
        repo = git.get("repo")
        sha = (git.get("sha") or "").strip()
        if not repo or not os.path.isdir(repo):
            result["skipped"].append(repo or "")
            return
        cached = cache.get(("status", repo))
        if cached is None:
            try:
                proc = subprocess.run(
                    ["git", "-C", repo, "-c", "core.quotepath=false",
                     "status", "--porcelain", "-uall"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=60,
                )
                cached = (proc.returncode == 0, proc.stdout or "")
            except Exception as e:
                logger.warning("[UndoStore] git status 失败: %s", e)
                cached = (False, "")
            cache[("status", repo)] = cached
        ok, status_out = cached
        if not ok:
            result["skipped"].append(repo)
            return
        baseline = set()
        for line in git.get("baseline") or []:
            for p in self._porcelain_paths(line):
                baseline.add(os.path.normpath(p))
        rev = sha or "HEAD"
        oldest = (stats_rev or {}).get(repo) or rev
        numstat = cache.get(("numstat", repo))
        if numstat is None:
            # 多轮撤回时最终态由最老快照决定，统一用最老的 rev 算一次
            numstat = self._git_numstat(repo, oldest)
            cache[("numstat", repo)] = numstat
        tree = cache.get(("tree", repo))
        if tree is None:
            tree = self._git_tree_paths(repo, oldest)
            cache[("tree", repo)] = tree

        for line in status_out.splitlines():
            if not line:
                continue
            status = line[:2]
            paths = [p for p in self._porcelain_paths(line) if p]
            if status == "??":
                # 快照前就是未跟踪的文件无法用 rev 还原，跳过
                for rel in paths:
                    if os.path.normpath(rel) in baseline:
                        continue
                    self._remove_path(os.path.join(repo, rel), result, apply=apply)
                continue
            if status[:1] in ("R", "C") and len(paths) >= 2:
                # 重命名：恢复旧路径，删掉新路径
                self._git_restore_path(
                    repo, rev, paths[0], result, apply, numstat, tree, baseline
                )
                self._remove_path(
                    os.path.join(repo, paths[1]), result, apply=apply
                )
                continue
            for rel in paths:
                self._git_restore_path(
                    repo, rev, rel, result, apply, numstat, tree, baseline
                )

    @staticmethod
    def _rel_files(base: str):
        rels = set()
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                full = os.path.join(root, name)
                rels.add(os.path.relpath(full, base).replace(os.sep, "/"))
        return rels

    @staticmethod
    def _rel_dirs(base: str):
        dirs_out = set()
        for root, subdirs, _files in os.walk(base):
            subdirs[:] = [d for d in subdirs if d not in EXCLUDE_DIRS]
            for name in subdirs:
                full = os.path.join(root, name)
                dirs_out.add(os.path.relpath(full, base).replace(os.sep, "/"))
        return dirs_out

    @staticmethod
    def _prune_empty_dirs(cwd: str, rels):
        """自底向上删除回滚后变空的目录"""
        for rel in sorted(rels, key=lambda r: r.count("/"), reverse=True):
            full = os.path.join(cwd, *rel.split("/"))
            try:
                if os.path.isdir(full) and not os.listdir(full):
                    os.rmdir(full)
            except OSError:
                continue

    def _restore_copy(self, turn_dir, manifest, result, apply=True) -> None:
        copy_root = manifest.get("copy_root")
        cwd = manifest.get("copy_cwd")
        if not copy_root or not cwd:
            return
        root = os.path.join(turn_dir, *str(copy_root).split("/"))
        if not os.path.isdir(root):
            result["skipped"].append(root)
            return
        if not os.path.isdir(cwd):
            result["skipped"].append(cwd)
            return
        snap = self._rel_files(root)
        current = self._rel_files(cwd)
        for rel in current - snap:
            full = os.path.join(cwd, *rel.split("/"))
            try:
                removed = self._count_lines(self._read_text(full))
                if apply:
                    os.remove(full)
                self._record_file(result, full, 0, removed, kind="deleted")
                result["restored"] += 1
            except Exception as e:
                logger.warning("[UndoStore] 删除多出文件失败 path=%s error=%s", full, e)
                result["skipped"].append(full)
        for rel in snap:
            src = os.path.join(root, *rel.split("/"))
            dst = os.path.join(cwd, *rel.split("/"))
            try:
                same = False
                if os.path.exists(dst) and os.path.isfile(dst):
                    # 只有大小相同才做内容比较；相同大小 + 回填 mtime 的情况也不能漏
                    try:
                        same = (os.path.getsize(src) == os.path.getsize(dst)
                                and filecmp.cmp(src, dst, shallow=False))
                    except OSError:
                        same = False
                if not same:
                    cur_text = self._read_text(dst) if os.path.isfile(dst) else None
                    src_text = self._read_text(src)
                    stats = self._diff_stats(cur_text, src_text)
                    if stats is None and src_text is not None:
                        stats = (self._count_lines(src_text), 0)
                    if apply:
                        parent = os.path.dirname(dst)
                        if parent:
                            os.makedirs(parent, exist_ok=True)
                        if os.path.isdir(dst):
                            shutil.rmtree(dst, ignore_errors=True)
                        shutil.copy2(src, dst)
                    result["restored"] += 1
                    kind = "modified" if cur_text is not None else "restored"
                    self._record_file(result, dst, *(stats or (0, 0)), kind=kind)
            except Exception as e:
                logger.warning("[UndoStore] 复制快照失败 path=%s error=%s", dst, e)
                result["skipped"].append(dst)
        if apply:
            # 删除快照后新建的空目录
            self._prune_empty_dirs(cwd, self._rel_dirs(cwd) - self._rel_dirs(root))

    def drop_session(self, session_id) -> None:
        try:
            with self._lock:
                path = os.path.join(self._root, self._safe_name(session_id))
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.warning("[UndoStore] 清理会话快照失败: %s", e)

    def _pending_turns(self, session_id, keep_turn_ids):
        session_dir = os.path.join(self._root, self._safe_name(session_id))
        if not os.path.isdir(session_dir):
            return []
        keep = {self._safe_name(t) for t in (keep_turn_ids or [])}
        turns = []
        for name in os.listdir(session_dir):
            turn_dir = os.path.join(session_dir, name)
            if not os.path.isdir(turn_dir) or name in keep:
                continue
            manifest = self._load(turn_dir) or {}
            created = manifest.get("created_at")
            if not isinstance(created, (int, float)):
                try:
                    created = os.path.getmtime(turn_dir)
                except Exception:
                    created = 0.0
            turns.append((float(created), turn_dir, manifest))
        turns.sort(key=lambda x: x[0], reverse=True)
        return turns

    def _run_turns(self, turns, apply: bool) -> dict:
        result = {"restored": 0, "skipped": [], "files": [], "warning": None}
        warned = False
        cache = {}
        stats_rev = {}
        # turns 新的在前，最后留下的即最老快照（最终还原态）
        for _, _, manifest in turns:
            git = manifest.get("git")
            if isinstance(git, dict) and git.get("repo"):
                stats_rev[git["repo"]] = (git.get("sha") or "").strip() or "HEAD"
        for _, turn_dir, manifest in turns:
            skipped_before = len(result["skipped"])
            try:
                self._restore_entries(turn_dir, manifest, result, apply=apply)
                if manifest.get("git"):
                    self._restore_git(
                        manifest["git"], result, apply=apply,
                        cache=cache, stats_rev=stats_rev,
                    )
                elif manifest.get("copy_root"):
                    self._restore_copy(turn_dir, manifest, result, apply=apply)
                if manifest.get("skipped_reason"):
                    warned = True
                    logger.warning(
                        "[UndoStore] turn 快照跳过原因=%s | turn=%s",
                        manifest.get("skipped_reason"), manifest.get("turn_id"),
                    )
            except Exception as e:
                logger.warning("[UndoStore] turn 回滚失败 turn=%s error=%s", turn_dir, e)
                result["skipped"].append(turn_dir)
            if apply and len(result["skipped"]) == skipped_before:
                # 有文件没回滚就保留快照，方便重试
                shutil.rmtree(turn_dir, ignore_errors=True)
        if result["skipped"] or warned:
            result["warning"] = "部分文件未回滚"
        if not apply:
            # 多轮会重复列出同一文件，按路径去重（保留最老快照的统计）
            merged = {}
            for row in result["files"]:
                merged[os.path.normpath(str(row.get("path") or ""))] = row
            result["files"] = list(merged.values())
        return result

    def preview(self, session_id, keep_turn_ids) -> dict:
        try:
            with self._lock:
                return self._run_turns(self._pending_turns(session_id, keep_turn_ids), apply=False)
        except Exception as e:
            logger.warning("[UndoStore] preview 失败: %s", e)
            return {"restored": 0, "skipped": [], "files": [], "warning": "部分文件未回滚"}

    def rollback(self, session_id, keep_turn_ids) -> dict:
        try:
            with self._lock:
                return self._run_turns(self._pending_turns(session_id, keep_turn_ids), apply=True)
        except Exception as e:
            logger.warning("[UndoStore] rollback 失败: %s", e)
            return {"restored": 0, "skipped": [], "files": [], "warning": "部分文件未回滚"}


_global_store = None
_singleton_lock = threading.Lock()


def get_undo_store() -> UndoStore:
    global _global_store
    if _global_store is None:
        with _singleton_lock:
            if _global_store is None:
                _global_store = UndoStore()
    return _global_store

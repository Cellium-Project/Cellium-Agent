# -*- coding: utf-8 -*-
import filecmp
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

MAX_TURNS = 5
COPY_MAX_BYTES = 100 * 1024 * 1024
COPY_MAX_FILES = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
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
        safe = re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "")).strip("._")
        return safe or "default"

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
            items.append((float(created), turn_dir))
        items.sort(key=lambda x: x[0], reverse=True)
        for _, turn_dir in items[MAX_TURNS:]:
            shutil.rmtree(turn_dir, ignore_errors=True)

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
                            ["git", "-C", repo, "status", "--porcelain", "-uall"],
                            capture_output=True, text=True, timeout=60,
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
        added = removed = 0
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
            if tag in ("replace", "delete"):
                removed += i2 - i1
            if tag in ("replace", "insert"):
                added += j2 - j1
        return added, removed

    @staticmethod
    def _record_file(result, path, added=0, removed=0, kind="modified") -> None:
        result.setdefault("files", []).append({
            "path": path,
            "added": int(added or 0),
            "removed": int(removed or 0),
            "kind": kind,
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
    def _git_show(repo, rev, rel):
        try:
            proc = subprocess.run(
                ["git", "-C", repo, "show", f"{rev}:{rel}"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                return proc.stdout
        except Exception:
            pass
        return None

    def _restore_git(self, git, result, apply=True) -> None:
        repo = git.get("repo")
        sha = (git.get("sha") or "").strip()
        if not repo or not os.path.isdir(repo):
            result["skipped"].append(repo or "")
            return
        try:
            proc = subprocess.run(
                ["git", "-C", repo, "status", "--porcelain", "-uall"],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            logger.warning("[UndoStore] git status 失败: %s", e)
            result["skipped"].append(repo)
            return
        if proc.returncode != 0:
            result["skipped"].append(repo)
            return
        baseline = set()
        for line in git.get("baseline") or []:
            for p in self._porcelain_paths(line):
                baseline.add(os.path.normpath(p))
        rev = sha or "HEAD"
        for line in (proc.stdout or "").splitlines():
            if not line:
                continue
            status = line[:2]
            for rel in self._porcelain_paths(line):
                if not rel:
                    continue
                full = os.path.join(repo, rel)
                if status == "??":
                    if os.path.normpath(rel) not in baseline:
                        try:
                            if os.path.isdir(full):
                                if apply:
                                    shutil.rmtree(full, ignore_errors=True)
                            elif os.path.exists(full):
                                removed = self._count_lines(self._read_text(full))
                                if apply:
                                    os.remove(full)
                                self._record_file(result, full, 0, removed, kind="deleted")
                            result["restored"] += 1
                        except Exception as e:
                            logger.warning("[UndoStore] 删除未跟踪文件失败 path=%s error=%s", full, e)
                            result["skipped"].append(full)
                    continue
                current = self._read_text(full) if os.path.isfile(full) else None
                old_text = self._git_show(repo, rev, rel)
                if apply:
                    try:
                        proc2 = subprocess.run(
                            ["git", "-C", repo, "checkout", rev, "--", rel],
                            capture_output=True, text=True, timeout=60,
                        )
                    except Exception as e:
                        logger.warning("[UndoStore] git checkout 失败 path=%s error=%s", rel, e)
                        result["skipped"].append(full)
                        continue
                    if proc2.returncode != 0:
                        result["skipped"].append(full)
                        continue
                else:
                    if old_text is None:
                        result["skipped"].append(full)
                        continue
                result["restored"] += 1
                stats = self._diff_stats(current, old_text)
                if stats is None and old_text is not None:
                    stats = (self._count_lines(old_text), 0)
                kind = "modified" if current is not None else "restored"
                self._record_file(result, full, *(stats or (0, 0)), kind=kind)

    @staticmethod
    def _rel_files(base: str):
        rels = set()
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                full = os.path.join(root, name)
                rels.add(os.path.relpath(full, base).replace(os.sep, "/"))
        return rels

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
                    same = filecmp.cmp(src, dst, shallow=False)
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

    def drop_session(self, session_id) -> None:
        try:
            with self._lock:
                path = os.path.join(self._root, self._safe_name(session_id))
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.warning("[UndoStore] 清理会话快照失败: %s", e)

    def _pending_turns(self, session_id, keep_turn_ids):
        session_dir = self._session_dir(session_id)
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
        for _, turn_dir, manifest in turns:
            try:
                self._restore_entries(turn_dir, manifest, result, apply=apply)
                if manifest.get("git"):
                    self._restore_git(manifest["git"], result, apply=apply)
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
            if apply:
                shutil.rmtree(turn_dir, ignore_errors=True)
        if result["skipped"] or warned:
            result["warning"] = "部分文件未回滚"
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

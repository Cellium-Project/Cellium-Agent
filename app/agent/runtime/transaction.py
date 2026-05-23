# -*- coding: utf-8 -*-
import os
import json
import uuid
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from .diagnostics import DiagnosticLoop, DiagnosticEngine, Diagnostic
from .patch import Patch, PatchEngine
from .core import CodeRuntime
from .patch_applier import PatchApplier

logger = logging.getLogger(__name__)


@dataclass
class EditStep:
    step_id: str
    file: str
    patch: Dict[str, Any]
    snapshot_id: str
    status: str = "pending"
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    diff: str = ""
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditStep":
        return cls(**data)


@dataclass
class TransactionState:
    transaction_id: str
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    steps: List[EditStep] = field(default_factory=list)
    committed_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    rollback_history: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)  


class EditTransaction:

    JOURNAL_DIR = ".edit_journal"

    def __init__(self, workspace: str = None, auto_validate: bool = True):
        self.workspace = workspace or os.getcwd()
        self.auto_validate = auto_validate
        self.state: Optional[TransactionState] = None
        self._runtime = CodeRuntime()
        self._diagnostic_loop = DiagnosticLoop()
        self._file_cache: Dict[str, str] = {}

    def begin(self, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        if self.state and self.state.status == "active":
            return {
                "success": False,
                "error": "已有活跃事务，请先 commit 或 rollback_all"
            }

        tx_id = self._generate_id()
        self.state = TransactionState(transaction_id=tx_id)

        if metadata:
            self.state.metadata = metadata

        journal_dir = self._get_journal_dir()
        os.makedirs(journal_dir, exist_ok=True)

        self._persist_state()

        logger.info(f"开始事务 {tx_id}")

        return {
            "success": True,
            "transaction_id": tx_id,
            "journal_dir": journal_dir,
        }

    def commit(self) -> Dict[str, Any]:
        if not self.state:
            return {"success": False, "error": "没有活跃事务"}

        committed = len(self.state.committed_steps)
        failed = len(self.state.failed_steps)

        self.state.status = "committed" if failed == 0 else "failed"
        self._persist_state()

        logger.info(f"提交事务 {self.state.transaction_id}: {committed} 成功, {failed} 失败")

        self._cleanup_current_journal()

        return {
            "success": True,
            "transaction_id": self.state.transaction_id,
            "status": self.state.status,
            "steps_committed": committed,
            "steps_failed": failed,
            "steps_rolled_back": len(self.state.rollback_history),
        }

    def create_step(
        self,
        file: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        metadata: Dict[str, Any] = None
    ) -> EditStep:
        if not self.state or self.state.status != "active":
            raise RuntimeError("没有活跃事务，请先调用 begin()")

        abs_path = self._resolve_path(file)
        snapshot_id = self._runtime.snapshot(abs_path)

        patch = {
            "mode": "replace",
            "old_text": old_text,
            "new_text": new_text,
            "replace_all": replace_all,
        }

        step = EditStep(
            step_id=self._generate_step_id(),
            file=abs_path,
            patch=patch,
            snapshot_id=snapshot_id,
            metadata=metadata or {},
        )
        self.state.steps.append(step)
        return step

    def create_step_for_insert(
        self,
        file: str,
        line: int,
        content: str,
        metadata: Dict[str, Any] = None
    ) -> EditStep:
        if not self.state or self.state.status != "active":
            raise RuntimeError("没有活跃事务，请先调用 begin()")

        abs_path = self._resolve_path(file)
        snapshot_id = self._runtime.snapshot(abs_path)

        patch = {
            "mode": "insert",
            "line": line,
            "content": content,
        }

        step = EditStep(
            step_id=self._generate_step_id(),
            file=abs_path,
            patch=patch,
            snapshot_id=snapshot_id,
            metadata=metadata or {},
        )
        self.state.steps.append(step)
        return step

    def create_step_for_append(
        self,
        file: str,
        content: str,
        metadata: Dict[str, Any] = None
    ) -> EditStep:
        if not self.state or self.state.status != "active":
            raise RuntimeError("没有活跃事务，请先调用 begin()")

        abs_path = self._resolve_path(file)
        snapshot_id = self._runtime.snapshot(abs_path)

        patch = {
            "mode": "append",
            "content": content,
        }

        step = EditStep(
            step_id=self._generate_step_id(),
            file=abs_path,
            patch=patch,
            snapshot_id=snapshot_id,
            metadata=metadata or {},
        )
        self.state.steps.append(step)
        return step

    def create_step_for_regex(
        self,
        file: str,
        pattern: str,
        replacement: str,
        metadata: Dict[str, Any] = None
    ) -> EditStep:
        if not self.state or self.state.status != "active":
            raise RuntimeError("没有活跃事务，请先调用 begin()")

        abs_path = self._resolve_path(file)
        snapshot_id = self._runtime.snapshot(abs_path)

        patch = {
            "mode": "regex",
            "pattern": pattern,
            "replacement": replacement,
        }

        step = EditStep(
            step_id=self._generate_step_id(),
            file=abs_path,
            patch=patch,
            snapshot_id=snapshot_id,
            metadata=metadata or {},
        )
        self.state.steps.append(step)
        return step

    def create_step_by_range(
        self,
        file: str,
        start_line: int,
        end_line: int,
        new_text: str,
        metadata: Dict[str, Any] = None
    ) -> EditStep:
        if not self.state or self.state.status != "active":
            raise RuntimeError("没有活跃事务，请先调用 begin()")

        abs_path = self._resolve_path(file)
        snapshot_id = self._runtime.snapshot(abs_path)

        patch = {
            "mode": "range",
            "start_line": start_line,
            "end_line": end_line,
            "new_text": new_text,
        }

        step = EditStep(
            step_id=self._generate_step_id(),
            file=abs_path,
            patch=patch,
            snapshot_id=snapshot_id,
            metadata=metadata or {},
        )
        self.state.steps.append(step)
        return step

    def commit_step(self, step: EditStep, validate: bool = True) -> Dict[str, Any]:
        if not self.state or self.state.status != "active":
            return {"success": False, "error": "没有活跃事务"}

        if step.status in ("committed", "rolled_back"):
            return {"success": False, "error": f"步骤已 {step.status}"}

        abs_path = step.file

        try:
            content = self._read_file(abs_path)

            new_content, apply_info = PatchApplier.apply(content, step.patch)

            if apply_info.get("error"):
                step.status = "failed"
                step.error = apply_info["error"]
                self.state.failed_steps.append(step.step_id)
                return {
                    "success": False,
                    "error": step.error,
                    "step_id": step.step_id,
                }

            if new_content == content:
                step.status = "failed"
                step.error = "内容无变化"
                self.state.failed_steps.append(step.step_id)
                return {
                    "success": False,
                    "error": step.error,
                    "step_id": step.step_id,
                }

            self._write_file(abs_path, new_content)
            step.status = "applied"

            diff = PatchApplier._generate_diff(content, new_content)

            if validate and self.auto_validate:
                diagnostics = self._diagnostic_loop.engine.check(abs_path, new_content)
                step.diagnostics = [
                    {"line": d.line, "message": d.message, "severity": d.severity}
                    for d in diagnostics[:10]
                ]

                if self._diagnostic_loop.engine.has_errors(diagnostics):
                    self.rollback_step(step)
                    error_details = "; ".join(
                        f"行{d.line}: {d.message}" for d in diagnostics if d.severity == "error"
                    )
                    return {
                        "success": False,
                        "error": f"诊断失败，已自动回滚: {error_details}",
                        "rolled_back": True,
                        "diagnostics": step.diagnostics,
                        "diff": diff,
                        "step_id": step.step_id,
                    }

            step.status = "committed"
            self.state.committed_steps.append(step.step_id)
            self._persist_state()

            logger.info(f"步骤 {step.step_id} 提交成功: {abs_path}")

            return {
                "success": True,
                "step_id": step.step_id,
                "file": abs_path,
                "count": apply_info.get("count", 0),
                "diff": diff,
                "diagnostics": step.diagnostics,
            }

        except Exception as e:
            step.status = "failed"
            step.error = str(e)
            self.state.failed_steps.append(step.step_id)

            logger.error(f"步骤 {step.step_id} 失败: {e}")

            return {
                "success": False,
                "error": str(e),
                "step_id": step.step_id,
            }

    def rollback_step(self, step: EditStep) -> Dict[str, Any]:
        if step.status not in ("applied", "committed", "failed"):
            return {"success": False, "error": f"步骤状态 {step.status} 不可回滚"}

        abs_path = step.file

        success = self._runtime.rollback(step.snapshot_id, abs_path)

        if success:
            step.status = "rolled_back"
            self.state.rollback_history.append(step.step_id)

            if step.step_id in self.state.committed_steps:
                self.state.committed_steps.remove(step.step_id)

            self._persist_state()

            logger.info(f"步骤 {step.step_id} 已回滚: {abs_path}")

            return {"success": True, "step_id": step.step_id}
        else:
            return {"success": False, "error": "回滚失败（快照不存在）"}

    def rollback_to_step(self, step_id: str, include_target: bool = False) -> Dict[str, Any]:
        if not self.state:
            return {"success": False, "error": "没有活跃事务"}

        target_idx = None
        for i, step in enumerate(self.state.steps):
            if step.step_id == step_id:
                target_idx = i
                break

        if target_idx is None:
            return {"success": False, "error": f"未找到步骤 {step_id}"}

        rolled_back = []
        for step in reversed(self.state.steps[target_idx + 1:]):
            if step.status in ("committed", "applied"):
                result = self.rollback_step(step)
                if result["success"]:
                    rolled_back.append(step.step_id)

        if include_target:
            target_step = self.state.steps[target_idx]
            if target_step.status in ("committed", "applied"):
                result = self.rollback_step(target_step)
                if result["success"]:
                    rolled_back.append(target_step.step_id)

        return {
            "success": True,
            "rolled_back": rolled_back,
            "target_step": step_id,
        }

    def rollback_all(self) -> Dict[str, Any]:
        if not self.state:
            return {"success": False, "error": "没有活跃事务"}

        rolled_back = []

        for step in reversed(self.state.steps):
            if step.status in ("committed", "applied"):
                result = self.rollback_step(step)
                if result["success"]:
                    rolled_back.append(step.step_id)

        self.state.status = "rolled_back"
        self._persist_state()

        logger.info(f"事务 {self.state.transaction_id} 已完全回滚")

        return {
            "success": True,
            "rolled_back": rolled_back,
            "transaction_id": self.state.transaction_id,
        }

    def retry_failed(self, max_retries: int = 3) -> Dict[str, Any]:
        if not self.state:
            return {"success": False, "error": "没有活跃事务"}

        retried = []
        succeeded = []

        for step in self.state.steps:
            if step.status == "failed" and step.retry_count < max_retries:
                step.retry_count += 1
                step.status = "pending"
                step.error = None
                step.diagnostics = []

                if step.step_id in self.state.failed_steps:
                    self.state.failed_steps.remove(step.step_id)

                result = self.commit_step(step)
                retried.append(step.step_id)

                if result["success"]:
                    succeeded.append(step.step_id)

        self._persist_state()

        return {
            "success": len(succeeded) > 0,
            "retried": retried,
            "succeeded": succeeded,
            "max_retries": max_retries,
        }

    def get_step(self, step_id: str) -> Optional[EditStep]:
        if not self.state:
            return None
        for step in self.state.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_all_steps(self) -> List[EditStep]:
        return self.state.steps if self.state else []

    def get_committed_steps(self) -> List[EditStep]:
        if not self.state:
            return []
        return [s for s in self.state.steps if s.status == "committed"]

    def get_failed_steps(self) -> List[EditStep]:
        if not self.state:
            return []
        return [s for s in self.state.steps if s.status == "failed"]

    def get_summary(self) -> Dict[str, Any]:
        if not self.state:
            return {"error": "没有活跃事务"}

        return {
            "transaction_id": self.state.transaction_id,
            "status": self.state.status,
            "total_steps": len(self.state.steps),
            "committed": len(self.state.committed_steps),
            "failed": len(self.state.failed_steps),
            "rolled_back": len(self.state.rollback_history),
            "files_modified": list(set(s.file for s in self.state.steps if s.status == "committed")),
        }

    def _persist_state(self):
        if not self.state:
            return

        journal_file = self._get_journal_file()

        state_dict = {
            "transaction_id": self.state.transaction_id,
            "status": self.state.status,
            "created_at": self.state.created_at,
            "steps": [s.to_dict() for s in self.state.steps],
            "committed_steps": self.state.committed_steps,
            "failed_steps": self.state.failed_steps,
            "rollback_history": self.state.rollback_history,
            "metadata": self.state.metadata,
        }

        import tempfile
        temp_file = journal_file + ".tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(state_dict, f, ensure_ascii=False, indent=2)

        os.replace(temp_file, journal_file)

    def recover(self, transaction_id: str = None) -> Dict[str, Any]:
        journal_dir = self._get_journal_dir()

        if not os.path.exists(journal_dir):
            return {"success": False, "error": "没有找到日志目录"}

        if transaction_id:
            journal_file = os.path.join(journal_dir, f"{transaction_id}.json")
            if not os.path.exists(journal_file):
                return {"success": False, "error": f"未找到事务 {transaction_id}"}
        else:
            journals = sorted(
                [f for f in os.listdir(journal_dir) if f.endswith('.json')],
                key=lambda x: os.path.getmtime(os.path.join(journal_dir, x)),
                reverse=True
            )
            if not journals:
                return {"success": False, "error": "没有找到事务日志"}
            journal_file = os.path.join(journal_dir, journals[0])

        try:
            with open(journal_file, 'r', encoding='utf-8') as f:
                state_dict = json.load(f)

            self.state = TransactionState(
                transaction_id=state_dict["transaction_id"],
                status=state_dict["status"],
                created_at=state_dict["created_at"],
                steps=[EditStep.from_dict(s) for s in state_dict["steps"]],
                committed_steps=state_dict["committed_steps"],
                failed_steps=state_dict["failed_steps"],
                rollback_history=state_dict["rollback_history"],
                metadata=state_dict.get("metadata", {}),
            )

            logger.info(f"已恢复事务 {self.state.transaction_id}")

            return {
                "success": True,
                "transaction_id": self.state.transaction_id,
                "status": self.state.status,
                "steps_count": len(self.state.steps),
                "summary": self.get_summary(),
            }
        except Exception as e:
            return {"success": False, "error": f"恢复失败: {e}"}

    def _generate_id(self) -> str:
        return f"tx_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"

    def _generate_step_id(self) -> str:
        return f"step_{uuid.uuid4().hex[:8]}"

    def _get_journal_dir(self) -> str:
        return os.path.join(self.workspace, self.JOURNAL_DIR)

    def _get_journal_file(self) -> str:
        if not self.state:
            return None
        return os.path.join(self._get_journal_dir(), f"{self.state.transaction_id}.json")

    def _resolve_path(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace, path)

    def _read_file(self, path: str) -> str:
        if path in self._file_cache:
            mtime = os.path.getmtime(path)
            cached = self._file_cache.get(f"{path}:mtime")
            if cached and cached >= mtime:
                return self._file_cache[path]

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        self._file_cache[path] = content
        self._file_cache[f"{path}:mtime"] = os.path.getmtime(path)

        return content

    def _write_file(self, path: str, content: str):
        import tempfile
        dir_path = os.path.dirname(path) or '.'
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')

        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                if hasattr(os, 'fsync'):
                    os.fsync(f.fileno())

            os.replace(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

        self._file_cache[path] = content
        self._file_cache[f"{path}:mtime"] = os.path.getmtime(path)

    def _cleanup_current_journal(self):
        if not self.state:
            return
        journal_file = self._get_journal_file()
        if journal_file and os.path.exists(journal_file):
            try:
                os.unlink(journal_file)
                logger.debug(f"清理事务日志: {journal_file}")
            except Exception as e:
                logger.warning(f"清理事务日志失败: {e}")

    def _cleanup_journal(self, keep_days: int = 7):
        journal_dir = self._get_journal_dir()
        if not os.path.exists(journal_dir):
            return

        cutoff_time = time.time() - keep_days * 86400

        for filename in os.listdir(journal_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(journal_dir, filename)
                if os.path.getmtime(filepath) < cutoff_time:
                    os.unlink(filepath)
                    logger.debug(f"清理日志: {filename}")

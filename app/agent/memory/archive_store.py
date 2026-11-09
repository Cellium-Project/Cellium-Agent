import json
import hashlib
import logging
import os
from datetime import datetime
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class ArchiveStore:
    def __init__(self, base_dir: str = "memory/archive"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self._session_keys: Dict[str, set] = {}

    def append(self, user_input: str, response: str, session_id: str = "default") -> str:
        return self.append_messages(
            session_id=session_id,
            messages=[
                {"role": "user", "content": user_input},
                {"role": "assistant", "content": response},
            ],
        )

    def append_messages(
        self,
        session_id: str = "default",
        messages: list = None,
        snapshot_hash: Optional[str] = None,
    ) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(self.base_dir, f"{date_str}.jsonl")

        normalized_messages = messages or []

        session_keys = self._get_session_keys(session_id)
        is_snapshot = any(
            isinstance(m, dict) and m.get("_is_compacted_notes") for m in normalized_messages
        )
        fresh_messages = []
        for m in normalized_messages:
            if isinstance(m, dict):
                key = self._message_key(m)
                if key in session_keys and not is_snapshot:
                    continue
                session_keys.add(key)
                fresh_messages.append(m)
            else:
                fresh_messages.append(m)

        if not fresh_messages:
            latest = self.get_latest_by_session(session_id)
            return latest.get("id", "") if latest else ""

        now = datetime.now().isoformat()

        record = {
            "id": self._gen_id(fresh_messages, timestamp=now),
            "time": now,
            "session_id": session_id,
            "messages": fresh_messages,
            "snapshot_hash": self._hash_messages(fresh_messages),
        }

        with open(file_path, "a", encoding="utf-8-sig") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record["id"]

    def get_by_id(self, record_id: str) -> Optional[Dict]:
        """根据 ID 回溯原始对话"""
        for root, _dirs, files in os.walk(self.base_dir):
            for file in files:
                if not file.endswith(".jsonl"):
                    continue
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8-sig") as f:
                        lines = f.readlines()
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning("[ArchiveStore] 归档文件读取失败，跳过: %s (%s)", file_path, e)
                    continue
                for line in lines:
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                        continue
                    if record.get("id") == record_id:
                        return record
        return None

    def get_by_session(self, session_id: str, limit: int = 20) -> List[Dict]:
        records: List[Dict] = []
        all_files = sorted(
            [f for f in os.listdir(self.base_dir) if f.endswith(".jsonl")],
            reverse=True,
        )

        for fname in all_files:
            file_path = os.path.join(self.base_dir, fname)
            try:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    lines = f.readlines()
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("[ArchiveStore] 归档文件读取失败，跳过: %s (%s)", fname, e)
                continue
            for line in reversed(lines):
                try:
                    record = json.loads(line)
                    if record.get("session_id") == session_id:
                        records.insert(0, record)
                        if len(records) >= limit:
                            return records
                except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
                    continue
        return records

    def get_latest_by_session(self, session_id: str) -> Optional[Dict]:
        records = self.get_by_session(session_id, limit=1)
        return records[-1] if records else None

    def get_by_date(self, date: str) -> list:
        """获取指定日期的所有对话"""
        file_path = os.path.join(self.base_dir, f"{date}.jsonl")
        if not os.path.exists(file_path):
            return []

        records = []
        with open(file_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                records.append(json.loads(line))
        return records

    def delete_after(self, session_id: str, message_id: str) -> int:
        files = sorted(f for f in os.listdir(self.base_dir) if f.endswith(".jsonl"))
        parsed: Dict[str, List[Dict]] = {}
        target_file = None
        target_record_index = -1
        target_message_index = -1

        for fname in files:
            file_path = os.path.join(self.base_dir, fname)
            records = []
            try:
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("[ArchiveStore] 归档文件读取失败，跳过: %s (%s)", fname, e)
                continue
            parsed[fname] = records

            if target_file is not None:
                continue
            for record_index, rec in enumerate(records):
                if rec.get("session_id") != session_id:
                    continue
                msgs = rec.get("messages")
                if not isinstance(msgs, list):
                    continue
                for message_index, msg in enumerate(msgs):
                    if isinstance(msg, dict) and msg.get("id") == message_id:
                        target_file = fname
                        target_record_index = record_index
                        target_message_index = message_index
                        break
                if target_file is not None:
                    break

        if target_file is None:
            return 0

        removed_count = 0
        started = False
        affected = set()

        for fname in files:
            records = parsed.get(fname)
            if records is None:
                continue
            new_records = []
            changed = False
            for record_index, rec in enumerate(records):
                if rec.get("session_id") != session_id:
                    new_records.append(rec)
                    continue
                if fname == target_file and record_index == target_record_index:
                    msgs = rec.get("messages") or []
                    kept = msgs[:target_message_index]
                    removed_count += len(msgs) - target_message_index
                    started = True
                    changed = True
                    if kept:
                        updated = dict(rec)
                        updated["messages"] = kept
                        new_records.append(updated)
                    continue
                if started:
                    removed_count += len(rec.get("messages") or [])
                    changed = True
                    continue
                new_records.append(rec)
            if changed:
                affected.add(fname)
                parsed[fname] = new_records

        for fname in affected:
            file_path = os.path.join(self.base_dir, fname)
            tmp_path = f"{file_path}.{os.getpid()}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                for rec in parsed[fname]:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp_path, file_path)

        self._session_keys.pop(session_id, None)
        return removed_count

    @staticmethod
    def _message_key(msg: Dict) -> tuple:
        """消息级去重键：role + content + timestamp + tool_call_id + tool_calls"""
        tool_calls = msg.get("tool_calls")
        return (
            msg.get("role", ""),
            msg.get("content"),
            msg.get("timestamp", ""),
            msg.get("tool_call_id", ""),
            json.dumps(tool_calls, ensure_ascii=False, sort_keys=True) if tool_calls else None,
        )

    def _get_session_keys(self, session_id: str) -> set:
        """获取该 session 已归档消息 key 集合"""
        keys = self._session_keys.get(session_id)
        if keys is not None:
            return keys

        keys = set()
        for root, _dirs, files in os.walk(self.base_dir):
            for fname in files:
                if not fname.endswith(".jsonl"):
                    continue
                file_path = os.path.join(root, fname)
                try:
                    with open(file_path, "r", encoding="utf-8-sig") as f:
                        for line in f:
                            try:
                                rec = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if rec.get("session_id") != session_id:
                                continue
                            for m in rec.get("messages", []):
                                if isinstance(m, dict):
                                    keys.add(self._message_key(m))
                except Exception:
                    continue
        self._session_keys[session_id] = keys
        return keys

    def _gen_id(self, messages: Optional[list] = None, timestamp: Optional[str] = None) -> str:
        payload = json.dumps(messages or [], ensure_ascii=False, sort_keys=True)
        ts = (timestamp or datetime.now().isoformat()).encode("utf-8")
        return hashlib.md5(payload.encode("utf-8") + ts).hexdigest()

    @staticmethod
    def _hash_messages(messages: Optional[list]) -> str:
        payload = json.dumps(messages or [], ensure_ascii=False, sort_keys=True)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

import json
import hashlib
import os
from datetime import datetime
from typing import Optional, Dict, List


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
        fresh_messages = []
        for m in normalized_messages:
            if not isinstance(m, dict):
                fresh_messages.append(m)
                continue
            key = self._message_key(m)
            if key not in session_keys:
                session_keys.add(key)
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
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        record = json.loads(line)
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
            with open(file_path, "r", encoding="utf-8-sig") as f:
                for line in reversed(f.readlines()):
                    try:
                        record = json.loads(line)
                        if record.get("session_id") == session_id:
                            records.insert(0, record)
                            if len(records) >= limit:
                                return records
                    except (json.JSONDecodeError, KeyError):
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

    @staticmethod
    def _message_key(msg: Dict) -> tuple:
        """消息级去重键：role + content + tool_call_id + tool_calls"""
        tool_calls = msg.get("tool_calls")
        return (
            msg.get("role", ""),
            msg.get("content"),
            msg.get("tool_call_id", ""),
            json.dumps(tool_calls, ensure_ascii=False, sort_keys=True) if tool_calls else None,
        )

    def _get_session_keys(self, session_id: str) -> set:
        """获取该 session 已归档消息 key 集合（进程内缓存，首次全量扫描）"""
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

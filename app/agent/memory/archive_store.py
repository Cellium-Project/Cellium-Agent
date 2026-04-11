import json
import hashlib
import os
from datetime import datetime
from typing import Optional, Dict, List


class ArchiveStore:
    """原始对话归档 - JSONL 格式，支持按 session_id 检索恢复"""

    def __init__(self, base_dir: str = "memory/archive"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def append(self, user_input: str, response: str, session_id: str = "default") -> str:
        """追加对话记录到 JSONL 文件（纯文本模式，向后兼容）"""
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
        """追加完整消息列表到 JSONL 文件（含 tool_call / tool_result）"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(self.base_dir, f"{date_str}.jsonl")

        user_text = ""
        assistant_text = ""
        normalized_messages = messages or []
        for message in normalized_messages:
            if message.get("role") == "user":
                user_text = message.get("content", "") or ""
            elif message.get("role") == "assistant" and not message.get("tool_calls"):
                assistant_text = (message.get("content") or "") or ""

        record = {
            "id": self._gen_id(user_text, assistant_text, normalized_messages),
            "time": datetime.now().isoformat(),
            "session_id": session_id,
            "user": user_text,
            "assistant": assistant_text,
            "messages": normalized_messages,
            "snapshot_hash": snapshot_hash or self._hash_messages(normalized_messages),
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
        """按 session_id 恢复历史对话。"""
        records: List[Dict] = []
        all_files = []
        for fname in os.listdir(self.base_dir):
            if fname.endswith(".jsonl"):
                all_files.append(os.path.join(self.base_dir, fname))
        all_files.sort()

        for file_path in all_files:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        if record.get("session_id") == session_id:
                            records.append(record)
                    except (json.JSONDecodeError, KeyError):
                        continue

        return records[-limit:] if len(records) > limit else records

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

    def _gen_id(self, user_input: str, response: str, messages: Optional[list] = None) -> str:
        """生成唯一 ID（优先基于完整消息快照）"""
        if messages:
            payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        else:
            payload = user_input + response
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_messages(messages: Optional[list]) -> str:
        payload = json.dumps(messages or [], ensure_ascii=False, sort_keys=True)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

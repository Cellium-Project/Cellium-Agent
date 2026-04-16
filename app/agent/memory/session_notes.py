# -*- coding: utf-8 -*-
"""
SessionNotes — 会话笔记管理器

功能：
  - 管理会话的 Markdown 笔记文件
  - 支持增量更新和读取
  - 渲染为提示词格式
"""

import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionNotes:
    """
    会话笔记管理器

    笔记格式：
    ```markdown
    # 会话笔记 (Session: xxx)

    ## 用户目标
    ...

    ## 已完成操作
    1. ...

    ## 关键发现
    - ...

    ## 待处理
    - ...

    ## 错误历史
    - ...
    ```
    """

    DEFAULT_NOTES_DIR = os.path.join("memory", "notes")
    
    # 最大历史目标数量
    MAX_GOAL_HISTORY = 10

    # 笔记段落模板
    SECTIONS = {
        "goal": "## 当前目标\n",
        "goal_history": "## 历史目标\n",
        "completed": "## 已完成操作\n",
        "findings": "## 关键发现\n",
        "pending": "## 待处理\n",
        "errors": "## 错误历史\n",
    }

    def __init__(self, session_id: str, notes_dir: str = None):
        self.session_id = session_id
        self.notes_dir = notes_dir or self.DEFAULT_NOTES_DIR
        safe_id = session_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        self.notes_path = os.path.join(self.notes_dir, f"{safe_id}.md")
        self._content: Dict[str, List[str]] = {}
        self._loaded = False

        os.makedirs(self.notes_dir, exist_ok=True)

    def load(self) -> str:
        """加载现有笔记"""
        if not os.path.exists(self.notes_path):
            return ""

        try:
            with open(self.notes_path, "r", encoding="utf-8") as f:
                content = f.read()
            self._parse(content)
            self._loaded = True
            return content
        except Exception as e:
            logger.warning("[SessionNotes] 加载失败: %s", e)
            return ""

    def _parse(self, content: str):
        """解析笔记内容到结构化数据"""
        current_section = None

        for line in content.split("\n"):
            for key, header in self.SECTIONS.items():
                if line.startswith(header.rstrip("\n")):
                    current_section = key
                    if key not in self._content:
                        self._content[key] = []
                    break
            else:
                if current_section and line.strip():
                    if current_section not in self._content:
                        self._content[current_section] = []
                    if not self._is_duplicate(line, self._content[current_section]):
                        self._content[current_section].append(line)

    def save(self) -> bool:
        """保存笔记到文件"""
        try:
            content = self.render()
            with open(self.notes_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.debug("[SessionNotes] 保存成功: %s", self.notes_path)
            return True
        except Exception as e:
            logger.error("[SessionNotes] 保存失败: %s", e)
            return False

    def _normalize_content(self, content: str) -> str:
        normalized = " ".join(content.split()).lower()
        suffixes = ["文件", "操作", "目录", "配置", "功能"]
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        return normalized

    def _is_duplicate(self, new_content: str, existing: List[str]) -> bool:
        """检查内容是否与现有内容重复"""
        new_normalized = self._normalize_content(new_content)

        for item in existing:
            existing_normalized = self._normalize_content(item)

            if new_normalized == existing_normalized:
                return True

            # 短内容：检查是否一个包含另一个
            if len(new_normalized) <= 20 or len(existing_normalized) <= 20:
                shorter = min(new_normalized, existing_normalized, key=len)
                longer = max(new_normalized, existing_normalized, key=len)
                if longer.startswith(shorter.rstrip()) or shorter in longer:
                    return True
            else:
                # 长内容：检查相似度
                shorter = min(len(new_normalized), len(existing_normalized))
                longer = max(len(new_normalized), len(existing_normalized))
                if shorter / longer >= 0.7:
                    overlap = 0
                    for i in range(len(new_normalized) - 10):
                        if new_normalized[i:i+10] in existing_normalized:
                            overlap += 1
                    if overlap > len(new_normalized) // 20:
                        return True

        return False

    def append(self, section: str, content: str):
        """
        追加笔记段落

        Args:
            section: 段落名称 (goal/completed/findings/pending/errors)
            content: 内容文本
        """
        if section not in self.SECTIONS:
            logger.warning("[SessionNotes] 未知段落: %s", section)
            return

        if section not in self._content:
            self._content[section] = []

        if not self._is_duplicate(content, self._content[section]):
            self._content[section].append(content)

    def set_goal(self, goal: str, force: bool = False):
        """
        设置用户目标

        Args:
            goal: 目标内容
            force: 是否强制覆盖（默认 False，只在目标为空时设置）
        """
        current_goal = self.get_goal()

        if force:
            if current_goal and current_goal != goal:
                if "goal_history" not in self._content:
                    self._content["goal_history"] = []
                if not self._is_duplicate(current_goal, self._content.get("goal_history", [])):
                    self._content["goal_history"].append(f"- {current_goal}")
                # 限制历史数量，保留最近 N 条
                if len(self._content["goal_history"]) > self.MAX_GOAL_HISTORY:
                    self._content["goal_history"] = self._content["goal_history"][-self.MAX_GOAL_HISTORY:]
            self._content["goal"] = [goal]
        elif not current_goal:
            self._content["goal"] = [goal]

    def update_goal_from_summary(self, new_goal: str):
        current_goal = self.get_goal()
        
        if current_goal and current_goal != new_goal:
            if "goal_history" not in self._content:
                self._content["goal_history"] = []
            if not self._is_duplicate(current_goal, self._content.get("goal_history", [])):
                self._content["goal_history"].append(f"- {current_goal}")
            # 限制历史数量
            if len(self._content["goal_history"]) > self.MAX_GOAL_HISTORY:
                self._content["goal_history"] = self._content["goal_history"][-self.MAX_GOAL_HISTORY:]
        
        self._content["goal"] = [new_goal]
        logger.info("[SessionNotes] 目标已更新 | 新目标: %s", new_goal[:50] if new_goal else "(无)")

    def get_goal(self) -> Optional[str]:
        """获取当前用户目标"""
        goals = self._content.get("goal", [])
        return goals[0] if goals else None

    def get_goal_history(self) -> List[str]:
        """获取历史目标列表"""
        return self._content.get("goal_history", [])

    def get_completed(self) -> List[str]:
        """获取已完成操作列表"""
        return self._content.get("completed", [])

    def get_findings(self) -> List[str]:
        """获取关键发现列表"""
        return self._content.get("findings", [])

    def get_errors(self) -> List[Dict]:
        """获取错误历史列表"""
        return self._content.get("errors", [])

    def get_pending(self) -> List[str]:
        """获取待处理列表"""
        return self._content.get("pending", [])

    def add_completed(self, action: str, tool: str = None):
        """添加已完成操作"""
        item = f"- {action}"
        if tool:
            item += f" ({tool})"
        self.append("completed", item)

    def set_completed(self, actions: List[str]):
        """用新列表替换已完成操作"""
        self._content["completed"] = [f"- {a}" for a in actions]

    def add_finding(self, finding: str):
        """添加关键发现"""
        self.append("findings", f"- {finding}")

    def add_pending(self, task: str):
        """添加待处理项"""
        self.append("pending", f"- {task}")

    def add_error(self, error: str, resolution: str = None):
        """添加错误记录"""
        item = f"- {error}"
        if resolution:
            item += f" → {resolution}"
        self.append("errors", item)

    def render(self) -> str:
        """渲染为完整的 Markdown 文本"""
        lines = [
            f"# 会话笔记 (Session: {self.session_id})",
            f"*最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
            "",
        ]

        for section, header in self.SECTIONS.items():
            if section in self._content and self._content[section]:
                lines.append(header)
                lines.extend(self._content[section])
                lines.append("")

        return "\n".join(lines)

    def render_for_prompt(self, max_length: int = 2000) -> str:
        """
        渲染为提示词格式

        Args:
            max_length: 最大长度限制

        Returns:
            格式化的笔记摘要
        """
        content = self.render()

        if len(content) > max_length:
            content = content[:max_length] + "\n...[笔记已截断]"

        return f"""## 会话笔记摘要

以下是之前对话的压缩摘要：

{content}
"""

    def exists(self) -> bool:
        """检查笔记是否存在"""
        return os.path.exists(self.notes_path) or bool(self._content)

    def clear(self):
        """清空笔记"""
        self._content = {}
        if os.path.exists(self.notes_path):
            os.remove(self.notes_path)

    def get_stats(self) -> Dict:
        """获取笔记统计"""
        return {
            "session_id": self.session_id,
            "exists": self.exists(),
            "sections": {k: len(v) for k, v in self._content.items()},
            "total_items": sum(len(v) for v in self._content.values()),
        }

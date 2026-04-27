# -*- coding: utf-8 -*-
"""
TaskSignalMatcher - 任务信号匹配器

职责：
  1. 关键词匹配（O(1) 快速匹配）
  2. 语义向量匹配（兜底策略）
  3. 内置 Gene 模板管理
  4. 仓库缓存管理
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskSignalMatcher:
    """任务信号匹配器 - 匹配用户输入到任务类型"""

    _repository = None
    _cache: Dict[str, Any] = {}
    _cache_loaded = False
    _semantic_match_enabled = True
    _semantic_threshold = 0.65  # 语义匹配阈值

    TASK_PATTERNS = {
        "code_debug": {
            "signals": ["debug", "fix", "error", "bug", "exception", "traceback", "报错", "错误", "调试"],
            "gene_template": """[HARD CONSTRAINTS]
Debug task detected. Follow strict protocol.
[CONTROL ACTION]
MUST: 1) Read error location 2) Analyze root cause 3) Fix minimal code
MUST NOT: Guess without reading code, change unrelated files
[AVOID]
- Don't ignore error line numbers
- Don't fix symptoms without understanding cause""",
            "forbidden_tools": ["web_search"],
            "preferred_tools": ["file", "shell"],
        },
        "file_operation": {
            "signals": ["read", "write", "edit", "create", "delete", "file", "文件", "写入", "读取"],
            "gene_template": """[HARD CONSTRAINTS]
File operation task. Safety first.
[CONTROL ACTION]
MUST: 1) Check file exists 2) Backup before write 3) Verify after change
MUST NOT: Overwrite without reading, delete recursively
[AVOID]
- Don't assume file structure
- Don't write without verification""",
            "forbidden_tools": [],
            "preferred_tools": ["file"],
        },
        "web_search": {
            "signals": ["search", "find", "google", "lookup", "query", "搜索", "查找"],
            "gene_template": """[HARD CONSTRAINTS]
Search task. Precision required.
[CONTROL ACTION]
MUST: 1) Use specific keywords 2) Verify source reliability 3) Synthesize answer
MUST NOT: Rely on single source, copy without understanding
[AVOID]
- Don't use vague search terms
- Don't trust unverified sources""",
            "forbidden_tools": ["shell"],
            "preferred_tools": ["web_search"],
        },
    }

    @classmethod
    def set_repository(cls, repository):
        cls._repository = repository
        cls._cache_loaded = False
        cls._cache.clear()

    @classmethod
    def _load_from_repository(cls):
        if not cls._repository or cls._cache_loaded:
            return
        try:
            results = cls._repository.search_memories(
                query="control_gene strategy",
                schema_type="control_gene",
                top_k=10
            )
            for item in results:
                metadata = item.get("metadata", {})
                task_type = metadata.get("task_type")
                if task_type:
                    cls._cache[task_type] = {
                        "task_type": task_type,
                        "gene_template": item.get("content", ""),
                        "forbidden_tools": metadata.get("forbidden_tools", []),
                        "preferred_tools": metadata.get("preferred_tools", []),
                        "signals": metadata.get("signals", []),
                    }
            cls._cache_loaded = True
        except Exception:
            pass

    @classmethod
    def _save_to_repository(cls, task_type: str, config: Dict[str, Any]):
        if not cls._repository:
            return
        try:
            cls._repository.upsert_memory(
                title=f"Gene: {task_type}",
                content=config["gene_template"],
                schema_type="control_gene",
                category="task_strategy",
                memory_key=f"gene:{task_type}",
                metadata={
                    "task_type": task_type,
                    "signals": config.get("signals", []),
                    "forbidden_tools": config.get("forbidden_tools", []),
                    "preferred_tools": config.get("preferred_tools", []),
                }
            )
        except Exception:
            pass

    @classmethod
    def _init_builtin_genes(cls):
        if not cls._repository:
            return
        for task_type, config in cls.TASK_PATTERNS.items():
            cls._save_to_repository(task_type, config)

    @classmethod
    def match(cls, user_input: str) -> Optional[Dict[str, Any]]:
        if not user_input:
            return None

        cls._load_from_repository()

        user_lower = user_input.lower()

        # 1. 快速关键词匹配（O(1) 级别）
        for task_type, config in cls._cache.items():
            signals = config.get("signals", [])
            if any(signal in user_lower for signal in signals):
                return {
                    "task_type": task_type,
                    "gene_template": config["gene_template"],
                    "forbidden_tools": config["forbidden_tools"],
                    "preferred_tools": config["preferred_tools"],
                }

        for task_type, config in cls.TASK_PATTERNS.items():
            if any(signal in user_lower for signal in config["signals"]):
                if cls._repository:
                    cls._save_to_repository(task_type, config)
                return {
                    "task_type": task_type,
                    "gene_template": config["gene_template"],
                    "forbidden_tools": config["forbidden_tools"],
                    "preferred_tools": config["preferred_tools"],
                }

        # 2. 语义向量匹配（兜底策略）
        if cls._semantic_match_enabled and cls._repository:
            semantic_match = cls._semantic_match(user_input)
            if semantic_match:
                return semantic_match

        return None

    @classmethod
    def _semantic_match(cls, user_input: str) -> Optional[Dict[str, Any]]:
        """基于向量相似度的语义匹配"""
        try:
            results = cls._repository.search_memories(
                query=user_input,
                top_k=3,
                schema_type="control_gene",
            )

            if not results:
                return None

            best_match = None
            best_score = 0.0

            for item in results:
                metadata = item.get("metadata", {})
                base_score = item.get("embedding_score", 0.0)
                usage_count = metadata.get("usage_count", 0)
                success_rate = metadata.get("success_rate", 0.5)

                experience_bonus = min(usage_count * 0.01, 0.1)
                success_bonus = (success_rate - 0.5) * 0.1

                final_score = base_score + experience_bonus + success_bonus

                if final_score > best_score and base_score >= cls._semantic_threshold:
                    best_score = final_score
                    best_match = item

            if best_match:
                metadata = best_match.get("metadata", {})
                task_type = metadata.get("task_type", "unknown")
                return {
                    "task_type": task_type,
                    "gene_template": best_match.get("content", ""),
                    "forbidden_tools": metadata.get("forbidden_tools", []),
                    "preferred_tools": metadata.get("preferred_tools", []),
                    "semantic_match": True,
                    "match_score": best_score,
                }

        except Exception:
            pass

        return None

    @classmethod
    def initialize(cls, repository=None):
        if repository:
            cls.set_repository(repository)
            cls._init_builtin_genes()
            cls._load_from_repository()
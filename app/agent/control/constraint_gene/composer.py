# -*- coding: utf-8 -*-
"""
GeneComposer - Gene 组合器

职责：
  1. 多任务匹配
  2. Gene 组合与合并
"""

import logging
from typing import Any, Dict, List, Optional

from .matcher import TaskSignalMatcher

logger = logging.getLogger(__name__)


class GeneComposer:
    """Gene 组合器 - 处理多任务匹配和组合"""

    TASK_PRIORITY = {
        "code_debug": 100,
        "file_operation": 90,
        "web_search": 80,
    }

    @classmethod
    def match_multiple(cls, user_input: str) -> List[Dict[str, Any]]:
        if not user_input:
            return []

        user_lower = user_input.lower()
        matches = []

        for task_type, config in TaskSignalMatcher._cache.items():
            signals = config.get("signals", [])
            if any(signal in user_lower for signal in signals):
                matches.append({
                    "task_type": task_type,
                    "gene_template": config["gene_template"],
                    "forbidden_tools": config["forbidden_tools"],
                    "preferred_tools": config["preferred_tools"],
                    "priority": cls.TASK_PRIORITY.get(task_type, 50),
                })

        for task_type, config in TaskSignalMatcher.TASK_PATTERNS.items():
            if any(signal in user_lower for signal in config["signals"]):
                if not any(m["task_type"] == task_type for m in matches):
                    matches.append({
                        "task_type": task_type,
                        "gene_template": config["gene_template"],
                        "forbidden_tools": config["forbidden_tools"],
                        "preferred_tools": config["preferred_tools"],
                        "priority": cls.TASK_PRIORITY.get(task_type, 50),
                    })

        matches.sort(key=lambda x: x["priority"], reverse=True)
        return matches

    @classmethod
    def compose(cls, matches: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not matches:
            return None

        if len(matches) == 1:
            return matches[0]

        task_types = [m["task_type"] for m in matches]
        combined_forbidden = list(set(
            tool for m in matches for tool in m["forbidden_tools"]
        ))
        combined_preferred = list(set(
            tool for m in matches for tool in m["preferred_tools"]
        ))

        hard_constraints = cls._build_combined_constraints(matches)

        return {
            "task_type": f"combined:{','.join(task_types)}",
            "gene_template": hard_constraints,
            "forbidden_tools": combined_forbidden,
            "preferred_tools": combined_preferred,
            "component_tasks": task_types,
        }

    @classmethod
    def _build_combined_constraints(cls, matches: List[Dict[str, Any]]) -> str:
        task_types = [m["task_type"] for m in matches]

        lines = [
            "[HARD CONSTRAINTS]",
            f"Multi-task: {' + '.join(task_types)}",
            "",
            "[CONTROL ACTION]",
        ]

        for i, match in enumerate(matches, 1):
            task_type = match["task_type"]
            template = match["gene_template"]

            must_section = cls._extract_section(template, "MUST:")
            must_not_section = cls._extract_section(template, "MUST NOT:")

            lines.append(f"STEP {i} [{task_type}]:")
            if must_section:
                lines.append(f"  MUST: {must_section}")
            if must_not_section:
                lines.append(f"  MUST NOT: {must_not_section}")
            lines.append("")

        avoid_items = set()
        for match in matches:
            template = match["gene_template"]
            items = cls._extract_avoid_items(template)
            avoid_items.update(items)

        if avoid_items:
            lines.append("[AVOID]")
            for item in sorted(avoid_items):
                lines.append(f"- {item}")

        return "\n".join(lines)

    @classmethod
    def _extract_section(cls, template: str, marker: str) -> str:
        lines = template.split("\n")
        result = []
        capturing = False

        for line in lines:
            if marker in line:
                capturing = True
                result.append(line.split(marker, 1)[-1].strip())
            elif capturing:
                if line.strip().startswith("MUST") or line.strip().startswith("["):
                    break
                if line.strip():
                    result.append(line.strip())

        return " ".join(result) if result else ""

    @classmethod
    def _extract_avoid_items(cls, template: str) -> List[str]:
        lines = template.split("\n")
        items = []
        in_avoid = False

        for line in lines:
            if "[AVOID]" in line:
                in_avoid = True
                continue
            if in_avoid:
                if line.strip().startswith("["):
                    break
                if line.strip().startswith("-"):
                    items.append(line.strip()[1:].strip())

        return items

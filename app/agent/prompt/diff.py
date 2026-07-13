# -*- coding: utf-8 -*-
"""
PromptDiffTracker — API 请求差异追踪层
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DiffReport:
    total_messages: int = 0
    stable_until: int = 0
    total_chars: int = 0
    stable_chars: int = 0
    divergence_reason: str = ""
    elapsed_seconds: float = 0.0

    @property
    def char_stable_ratio(self) -> float:
        """按字符量计算的稳定比例（≈ token 缓存覆盖率）"""
        return self.stable_chars / max(self.total_chars, 1)


@dataclass
class CacheStats:
    """缓存命中统计"""
    total_calls: int = 0
    last_report: Optional[DiffReport] = None
    history: List[DiffReport] = field(default_factory=list)

    @property
    def avg_char_stable_ratio(self) -> float:
        if len(self.history) <= 0:
            return 0.0
        return sum(r.char_stable_ratio for r in self.history) / len(self.history)


class PromptDiffTracker:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._last_messages: Optional[List[Dict]] = None
        self._stats = CacheStats()

    async def chat(self, llm_engine: Any, messages: List[Dict],
                   tools: Optional[List[Dict]] = None,
                   max_tokens: Optional[int] = None,
                   **kwargs) -> Any:
        start = time.perf_counter()
        self._stats.total_calls += 1

        if self._enabled and self._last_messages is not None and messages is not None:
            try:
                report = self._compute_diff(self._last_messages, messages, start)
                self._stats.last_report = report
                self._stats.history.append(report)

                if report.divergence_reason:
                    logger.info(
                        "[PromptDiff] 前缀变化 | stable_until=%d/%d条 | chars=%d/%d (%d%%) | reason=%s",
                        report.stable_until, report.total_messages,
                        report.stable_chars, report.total_chars,
                        round(report.char_stable_ratio * 100),
                        report.divergence_reason[:120],
                    )
            except Exception as e:
                logger.warning("[PromptDiff] 差异计算失败: %s", e)
        else:
            logger.info("[PromptDiff] 首次调用，跳过对比")

        response = await llm_engine.chat(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            **kwargs,
        )

        self._last_messages = messages

        logger.info(
            "[PromptDiff] 调用完成 | %s",
            self._format_stats(),
        )

        return response

    # ---- 内部 ----

    @staticmethod
    def _compute_diff(last: List[Dict], current: List[Dict],
                      start: float) -> DiffReport:
        """计算两次请求的差异"""
        total_chars = sum(len(str(m.get("content", ""))) for m in current)
        report = DiffReport(
            total_messages=len(current),
            total_chars=total_chars,
            elapsed_seconds=time.perf_counter() - start,
        )

        divergence = 0
        stable_chars = 0
        while (divergence < len(last) and divergence < len(current)):
            if not PromptDiffTracker._message_equal(last[divergence], current[divergence]):
                break
            stable_chars += len(str(current[divergence].get("content", "")))
            divergence += 1

        report.stable_until = divergence
        report.stable_chars = stable_chars

        if divergence < min(len(last), len(current)):
            changed = current[divergence]
            old_content = last[divergence].get("content", "")
            new_content = changed.get("content", "")
            report.divergence_reason = (
                f"[{divergence}] role={changed.get('role')} 变化: "
                f"旧={_short_str(old_content)} → 新={_short_str(new_content)}"
            )
        elif len(current) > len(last):
            report.divergence_reason = (
                f"消息数增加（{len(last)}→{len(current)}），新增 {len(current) - len(last)} 条"
            )

        if divergence < min(len(last), len(current)):
            logger.debug(
                "[PromptDiff] 分歧点: 索引 %d | last=%d条 | current=%d条",
                divergence, len(last), len(current),
            )

        return report

    @staticmethod
    def _message_equal(a: Dict, b: Dict) -> bool:
        return (
            a.get("role") == b.get("role")
            and a.get("content") == b.get("content")
            and a.get("tool_call_id") == b.get("tool_call_id")
            and a.get("tool_calls") == b.get("tool_calls")
        )

    def _format_stats(self) -> str:
        stats = self._stats
        if stats.total_calls <= 1:
            return "首次调用"

        report = stats.last_report
        if not report:
            return "无报告"

        ratio = report.char_stable_ratio
        avg = stats.avg_char_stable_ratio

        parts = [
            f"总调用={stats.total_calls}",
            f"前缀稳定={report.stable_until}/{report.total_messages}条",
            f"chars={report.stable_chars}/{report.total_chars} ({ratio:.0%})",
        ]
        if len(stats.history) > 1:
            parts.append(f"历史均值={avg:.0%}")

        return " | ".join(parts)

    def get_stats(self) -> CacheStats:
        return self._stats

    def get_cache_summary(self) -> str:
        stats = self._stats
        if stats.total_calls <= 1:
            return "仅 1 次调用，无对比数据"

        avg = stats.avg_char_stable_ratio
        report = stats.last_report

        summary = (
            f"[PromptDiff] 总调用={stats.total_calls} | "
            f"历史平均缓存覆盖率={avg:.1%}"
        )
        if report:
            summary += (
                f" | 最近: {report.stable_until}/{report.total_messages}条 "
                f"({report.stable_chars}/{report.total_chars} chars, {report.char_stable_ratio:.0%})"
            )

        return summary


def _short_str(s: str, max_len: int = 60) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"

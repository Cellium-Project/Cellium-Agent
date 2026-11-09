# -*- coding: utf-8 -*-
"""
循环控制器 - 迭代计数和循环检测

职责：
  - 迭代计数管理
  - 停止请求处理
  - 输出循环检测
"""

import logging
from typing import Deque, Optional
from collections import deque

logger = logging.getLogger(__name__)


class LoopController:
    """
    循环控制器

    管理迭代计数、停止信号和输出循环检测。
    """

    def __init__(
        self,
        max_iterations: int = 10,
        loop_detection_threshold: int = 3,
    ):
        """
        初始化循环控制器

        Args:
            max_iterations: 最大迭代次数
            loop_detection_threshold: 循环检测阈值（连续相同输出的次数）
        """
        self.max_iterations = max_iterations
        self.loop_detection_threshold = loop_detection_threshold

        # 迭代状态
        self._iteration: int = 0
        self._stop_requested: bool = False

        # 输出循环检测
        self._recent_outputs: Deque[str] = deque(maxlen=5)

    @property
    def iteration(self) -> int:
        """当前迭代次数"""
        return self._iteration

    @property
    def is_stop_requested(self) -> bool:
        """是否请求停止"""
        return self._stop_requested

    @property
    def remaining_iterations(self) -> int:
        """剩余迭代次数"""
        return max(0, self.max_iterations - self._iteration)

    def start(self):
        """开始新的循环（重置状态）"""
        self._iteration = 0
        self._stop_requested = False
        self._recent_outputs.clear()
        logger.debug("[LoopController] 循环开始 | max_iterations=%d", self.max_iterations)

    def advance(self) -> int:
        """
        推进迭代

        Returns:
            新的迭代次数
        """
        self._iteration += 1
        logger.debug("[LoopController] 迭代 %d/%d", self._iteration, self.max_iterations)
        return self._iteration

    def request_stop(self):
        """请求停止循环"""
        self._stop_requested = True
        logger.info("[LoopController] 收到停止请求")

    def is_exceeded(self) -> bool:
        """是否超过最大迭代次数"""
        return self._iteration >= self.max_iterations

    def check_output_loop(self, content: str) -> tuple:
        """
        检测输出是否循环

        Args:
            content: 当前输出内容

        Returns:
            (is_looping: bool, repeated_content: Optional[str])
        """
        if not content or not content.strip():
            return False, None

        normalized = content.strip()[:200] 

        repeat_count = sum(1 for out in self._recent_outputs if out == normalized)

        self._recent_outputs.append(normalized)

        if repeat_count >= self.loop_detection_threshold - 1:
            logger.warning(
                "[LoopController] 检测到输出循环 | 重复次数=%d | 阈值=%d",
                repeat_count + 1,
                self.loop_detection_threshold,
            )
            return True, normalized

        return False, None

    def reset(self):
        """重置控制器状态"""
        self._iteration = 0
        self._stop_requested = False
        self._recent_outputs.clear()


# 单次回复内的退化重复检测（模型刷屏时提前中断，避免白烧 token）
_STREAM_WINDOW = 4000
_STREAM_MIN_COUNT = 12
_STREAM_MIN_REPEAT_CHARS = 2000
_STREAM_MIN_PERIOD = 8
_STREAM_MAX_PERIOD = 240
_STREAM_MIN_ALNUM = 6


def detect_stream_loop(text: str) -> Optional[str]:
    """检测单条回复末尾是否在连续重复同一片段，返回重复片段"""
    if not text:
        return None
    tail = text[-_STREAM_WINDOW:]

    # 连续重复的长行（要求累计重复长度够大，避免误伤数据表/代码块）
    prev, run = None, 0
    for raw in reversed(tail.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line == prev:
            run += 1
        else:
            prev, run = line, 1
        if (
            run >= _STREAM_MIN_COUNT
            and len(line) * run >= _STREAM_MIN_REPEAT_CHARS
            and sum(ch.isalnum() for ch in line) >= _STREAM_MIN_ALNUM
        ):
            return line

    # 末尾必须是同一片段的连续整段重复，分散出现的相同句子不算
    for period in range(_STREAM_MIN_PERIOD, _STREAM_MAX_PERIOD + 1):
        repeat = max(_STREAM_MIN_COUNT, -(-600 // period))
        need = period * repeat
        if len(tail) < need:
            continue
        unit = tail[-period:]
        if sum(ch.isalnum() for ch in unit) < _STREAM_MIN_ALNUM:
            continue
        if tail[-need:] == unit * repeat:
            return unit
    return None

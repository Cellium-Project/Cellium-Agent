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

        normalized = content.strip()[:200]  # 只比较前200字符

        # 检查是否与最近的输出重复
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

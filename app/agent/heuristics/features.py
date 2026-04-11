# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Tuple

from app.agent.heuristics.types import EvaluationContext, DerivedFeatures

logger = logging.getLogger(__name__)


class FeatureExtractor:

    EMA_ALPHA = 0.3

    _progress_history: List[float] = []
    _ema_trend: float = 0.0
    _last_progress: float = 0.0

    _stuck_counter: int = 0
    _last_progress_score: float = 0.0

    def __init__(self, ema_alpha: float = None):
        if ema_alpha is not None:
            self.EMA_ALPHA = ema_alpha

        self._progress_history: List[float] = []
        self._ema_trend: float = 0.0
        self._last_progress: float = 0.0
        self._stuck_counter: int = 0
        self._last_progress_score: float = 0.0

    def extract(self, context: EvaluationContext) -> DerivedFeatures:
        """
        从上下文提取派生特征

        Args:
            context: 评估上下文

        Returns:
            DerivedFeatures: 派生特征
        """
        features = DerivedFeatures()
        calls = context.tool_call_history

        if calls:
            tool_names = [c.get("tool_name", c.get("tool", "unknown")) for c in calls]
            features.unique_tools_used = len(set(tool_names))
            features.tool_diversity_score = features.unique_tools_used / max(len(tool_names), 1)

            if tool_names:
                from collections import Counter
                counts = Counter(tool_names)
                dominant = counts.most_common(1)[0][1] if counts else 0
                features.dominant_tool_ratio = dominant / len(tool_names)

        features.repetition_score = self._calc_repetition_score(calls)
        features.pattern_detected, features.pattern_cycle_length = self._detect_pattern(calls)

        if calls:
            features.error_rate = self._calc_error_rate(calls)
            features.empty_result_rate = self._calc_empty_rate(calls)
            features.result_quality_score = self._calc_result_quality(calls)
            features.avg_result_size = self._calc_avg_result_size(calls)

        current_progress = self._estimate_progress(calls, context)
        features.stuck_iterations = self._calc_stuck_iterations(calls, context, current_progress)

        (
            features.progress_trend_raw,
            features.progress_trend,
            features.trend_confidence,
        ) = self._calc_progress_trend_robust(current_progress, context.iteration)

        features.convergence_rate = max(0.0, features.progress_trend) * max(features.trend_confidence, 0.3)
        features.progress_score = current_progress
        features.is_plateau = self._detect_plateau(features)
        features.is_making_progress = features.progress_trend > 0.03 or features.progress_score >= 0.7 or features.is_plateau


        # ===== 上下文特征 =====
        if context.token_budget > 0:
            saturation = context.total_tokens_used / context.token_budget
            features.context_saturation = saturation

            if saturation >= 0.95:
                features.context_saturation_level = "stop"
            elif saturation >= 0.85:
                features.context_saturation_level = "redirect"
            elif saturation >= 0.70:
                features.context_saturation_level = "warn"
            elif saturation >= 0.50:
                features.context_saturation_level = "normal"
            else:
                features.context_saturation_level = "idle"

        if context.iteration > 0:
            features.message_turn_ratio = len(calls) / context.iteration

        # ===== 时序特征 =====
        if context.iteration > 0 and context.elapsed_ms > 0:
            features.time_per_iteration = context.elapsed_ms / context.iteration

        # ===== LLM 输出重复检测 =====
        features.exact_repetition_count = self._detect_exact_repetition(
            context.recent_llm_outputs
        )
        features.is_output_loop = features.exact_repetition_count >= 5

        return features

    def reset(self):
        self._progress_history = []
        self._ema_trend = 0.0
        self._last_progress = 0.0
        self._stuck_counter = 0
        self._last_progress_score = 0.0

    # ============================================================
    #  循环检测方法
    # ============================================================

    def _calc_repetition_score(self, calls: List[Dict]) -> float:
        if not calls:
            return 0.0

        recent = calls[-10:]
        if len(recent) < 2:
            return 0.0

        consecutive_same = 1
        max_consecutive = 1
        last_tool = None

        for call in recent:
            tool_name = call.get("tool_name", call.get("tool", "unknown"))
            if tool_name == last_tool:
                consecutive_same += 1
                max_consecutive = max(max_consecutive, consecutive_same)
            else:
                consecutive_same = 1
            last_tool = tool_name

        return min(max_consecutive / len(recent), 1.0)

    def _detect_pattern(self, calls: List[Dict]) -> Tuple[str, int]:
        """
        检测调用模式

        Returns:
            (pattern_type, cycle_length)
            pattern_type: "cycle" / "repetition" / ""
        """
        if len(calls) < 4:
            return "", 0

        tool_names = [c.get("tool_name", c.get("tool", "unknown")) for c in calls[-12:]]

        if len(tool_names) >= 4:
            if tool_names[-1] == tool_names[-3] and tool_names[-2] == tool_names[-4]:
                if tool_names[-1] != tool_names[-2]:
                    return "cycle", 2

        if len(tool_names) >= 6:
            if (tool_names[-1] == tool_names[-4] and
                tool_names[-2] == tool_names[-5] and
                tool_names[-3] == tool_names[-6]):
                return "cycle", 3

        if len(tool_names) >= 3:
            if tool_names[-1] == tool_names[-2] == tool_names[-3]:
                return "repetition", 1

        return "", 0

    # ============================================================
    #  结果质量计算
    # ============================================================

    def _calc_error_rate(self, calls: List[Dict]) -> float:
        """计算错误率"""
        if not calls:
            return 0.0

        errors = 0
        for c in calls:
            result = c.get("result", {})
            if isinstance(result, dict):
                if result.get("error") or result.get("status") == "error":
                    errors += 1
            elif result is None:
                errors += 1

        return errors / len(calls)

    def _calc_empty_rate(self, calls: List[Dict]) -> float:
        """计算空结果率"""
        if not calls:
            return 0.0

        empties = sum(1 for c in calls if self._is_empty_result(c.get("result")))
        return empties / len(calls)

    def _is_empty_result(self, result: any) -> bool:
        """判断结果是否为空"""
        if result is None:
            return True
        if isinstance(result, dict):
            if result.get("error"):
                return False  
            if result.get("output") and str(result.get("output")).strip():
                return False
            if result.get("content") and str(result.get("content")).strip():
                return False
            meaningful_keys = {"output", "content", "result", "data", "text"}
            if not any(k in result for k in meaningful_keys):
                return True
            return all(not v for k, v in result.items() if k in meaningful_keys)
        if isinstance(result, str):
            return not result.strip()
        return False

    def _calc_result_quality(self, calls: List[Dict]) -> float:
        if not calls:
            return 0.5

        error_rate = self._calc_error_rate(calls)
        empty_rate = self._calc_empty_rate(calls)

        sizes = [len(str(c.get("result", ""))) for c in calls[-5:]]
        if len(sizes) >= 2:
            growth = (sizes[-1] - sizes[0]) / max(sizes[0], 1)
            growth_score = min(max(growth, -1), 1) * 0.5 + 0.5  # 归一化到 0-1
        else:
            growth_score = 0.5

        quality = (1 - error_rate) * 0.4 + (1 - empty_rate) * 0.3 + growth_score * 0.3
        return quality

    def _calc_avg_result_size(self, calls: List[Dict]) -> float:
        """计算平均结果大小"""
        if not calls:
            return 0.0

        sizes = [len(str(c.get("result", ""))) for c in calls]
        return sum(sizes) / len(sizes)

    def _has_meaningful_result(self, result: any) -> bool:
        if result is None:
            return False
        if isinstance(result, dict):
            if result.get("error") or result.get("status") == "error" or result.get("success") is False:
                return False
            return not self._is_empty_result(result)
        if isinstance(result, str):
            return bool(result.strip())
        return True

    def _count_consecutive_failures(self, calls: List[Dict]) -> int:
        failures = 0
        for call in reversed(calls):
            result = call.get("result")
            if self._has_meaningful_result(result):
                break
            if self._is_empty_result(result) or (isinstance(result, dict) and result.get("error")):
                failures += 1
        return failures

    def _is_strategy_repeating(self, calls: List[Dict]) -> bool:
        if len(calls) < 2:
            return False

        signatures = []
        for call in calls:
            tool_name = call.get("tool_name", call.get("tool", ""))
            args = call.get("arguments", call.get("args", {}))
            if tool_name == "file":
                signature = args.get("path", args.get("file", ""))
            elif tool_name == "shell":
                signature = args.get("command", "")[:80]
            else:
                signature = str(sorted(args.items()))
            signatures.append(f"{tool_name}:{signature}")

        return len(set(signatures)) <= 1

    # ============================================================
    #  进度估计
    # ============================================================


    def _estimate_progress(self, calls: List[Dict], context: EvaluationContext) -> float:
        if not calls:
            return 0.0

        recent_calls = calls[-4:]
        quality = self._calc_result_quality(recent_calls)
        recent_non_empty = 1.0 - self._calc_empty_rate(recent_calls)
        meaningful_ratio = sum(
            1 for call in recent_calls
            if self._has_meaningful_result(call.get("result"))
        ) / max(len(recent_calls), 1)

        tool_names = [c.get("tool_name", c.get("tool", "unknown")) for c in recent_calls]
        unique_ratio = len(set(tool_names)) / max(len(tool_names), 1)
        repetition_penalty = self._calc_repetition_score(recent_calls)
        exploration_score = max(unique_ratio - repetition_penalty * 0.5, 0.0)
        last_result_bonus = 0.1 if self._has_meaningful_result(recent_calls[-1].get("result")) else 0.0

        progress = (
            quality * 0.3
            + meaningful_ratio * 0.3
            + recent_non_empty * 0.15
            + exploration_score * 0.15
            + last_result_bonus
            - repetition_penalty * 0.1
        )


        return min(max(progress, 0.0), 1.0)

    def _calc_stuck_iterations(
        self,
        calls: List[Dict],
        context: EvaluationContext,
        current_progress: float,
    ) -> int:
        recent_calls = calls[-3:]
        progress_delta = current_progress - self._last_progress_score
        recent_quality = self._calc_result_quality(recent_calls) if recent_calls else 0.0
        repeated_strategy = self._is_strategy_repeating(recent_calls)
        recent_repetition = self._calc_repetition_score(recent_calls) if recent_calls else 0.0
        consecutive_failures = self._count_consecutive_failures(recent_calls)
        last_result_meaningful = (
            self._has_meaningful_result(recent_calls[-1].get("result"))
            if recent_calls else False
        )

        is_regressing = progress_delta < -0.03
        lacks_meaningful_gain = progress_delta < 0.05 and not last_result_meaningful and recent_quality < 0.6
        is_looping = (
            repeated_strategy
            or recent_repetition > 0.66
            or self._detect_pattern(recent_calls)[0] != ""
        )

        if recent_calls and (is_regressing or consecutive_failures >= 2 or (lacks_meaningful_gain and is_looping)):
            self._stuck_counter += 1
        elif progress_delta > 0.08 or (last_result_meaningful and recent_quality >= 0.6):
            self._stuck_counter = 0
        elif self._stuck_counter > 0 and progress_delta > 0.0:
            self._stuck_counter -= 1

        self._last_progress_score = current_progress
        return self._stuck_counter

    def _calc_progress_trend_robust(
        self,
        current_progress: float,
        iteration: int,
    ) -> Tuple[float, float, float]:
        """
        计算抗噪声的进度趋势

        方法：EMA 平滑 + 线性回归置信度
        - EMA 平滑：消除单次波动
        - 回归 R²：判断趋势是否可信

        Returns:
            (原始趋势, 平滑后的趋势, 趋势置信度)
        """
        self._progress_history.append(current_progress)

        window = 10
        if len(self._progress_history) > window:
            self._progress_history = self._progress_history[-window:]

        if iteration == 1:
            self._ema_trend = 0.0

        raw_trend = 0.0
        if len(self._progress_history) >= 2:
            raw_trend = current_progress - self._progress_history[-2]

        self._ema_trend = self.EMA_ALPHA * raw_trend + (1 - self.EMA_ALPHA) * self._ema_trend
        confidence = self._calc_trend_confidence()

        if len(self._progress_history) < 3:
            return raw_trend, self._ema_trend, 0.0

        return raw_trend, self._ema_trend, confidence


    def _calc_trend_confidence(self) -> float:
        """
        线性回归 R² 计算趋势置信度

        R² 接近 1：趋势明显且可信
        R² 接近 0：数据噪声大，趋势不可信
        """
        history = self._progress_history
        n = len(history)
        if n < 3:
            return 0.0

        x = list(range(n))
        y = history

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        cov = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        var_x = sum((x[i] - x_mean) ** 2 for i in range(n))
        var_y = sum((y[i] - y_mean) ** 2 for i in range(n))

        if var_x == 0 or var_y == 0:
            return 0.0

        # R² = (cov / (std_x * std_y))²
        r = cov / (var_x ** 0.5 * var_y ** 0.5)
        r_squared = r ** 2

        return max(0.0, min(1.0, r_squared))

    def _detect_plateau(self, features: DerivedFeatures) -> bool:
        """
        检测是否处于高原期（plateau）

        Plateau 特征：
        1. progress_trend ≈ 0（不升不降）
        2. 但 stuck_iterations 较短（刚进入停滞）
        3. 结果质量没有明显下降

        区分：
        - Plateau：正常的中期调整，应继续观察
        - 真停滞：长时间无进展 + 结果质量差
        """
        # 条件1：趋势接近水平
        is_flat = abs(features.progress_trend) < 0.03 and abs(features.progress_trend_raw) < 0.05

        # 条件2：停滞时间不太长，但至少已经出现轻微停滞
        is_short_stuck = 0 < features.stuck_iterations < 5

        # 条件3：结果质量尚可，且不是高错误率
        has_ok_quality = features.result_quality_score >= 0.55 and features.error_rate < 0.5

        # 条件4：趋势不够明确，不应贸然判为失败
        trend_is_uncertain = features.trend_confidence < 0.6

        return is_flat and is_short_stuck and has_ok_quality and trend_is_uncertain


    def _detect_exact_repetition(self, outputs: List[str]) -> int:
        """
        检测 LLM 输出的一字不差重复

        用户核心需求：
          - 只有模型连续输出 5 次以上完全相同的内容，才认为陷入循环
          - 这是 terminate 的唯一触发条件

        Args:
            outputs: 最近的 LLM 输出列表

        Returns:
            连续完全相同的输出次数（0 表示无重复或数据不足）
        """
        if not outputs or len(outputs) < 2:
            return 0

        # 从最新开始向前检测
        latest = outputs[-1]
        count = 1

        for i in range(len(outputs) - 2, -1, -1):
            if outputs[i] == latest:
                count += 1
            else:
                break

        return count

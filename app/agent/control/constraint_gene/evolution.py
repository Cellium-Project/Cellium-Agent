# -*- coding: utf-8 -*-
"""
GeneEvolution - Gene 进化系统

职责：
  1. 从失败中提取避免项
  2. Gene 存在检查
  3. Gene 更新/进化
  4. LLM Gene 创建
  5. 成功记录
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .matcher import TaskSignalMatcher

if TYPE_CHECKING:
    from app.agent.control.loop_state import LoopState

logger = logging.getLogger(__name__)


class GeneEvolution:
    """Gene 进化系统"""

    MAX_RECENT_RESULTS = 20

    @classmethod
    def extract_avoid_cue(cls, state: "LoopState", reward: float) -> Optional[str]:
        if reward >= 0.5:
            return None

        avoid_cue = None

        if state.last_error:
            avoid_cue = f"DON'T: Cause error '{state.last_error[:50]}'"
        elif state.features and state.features.stuck_iterations > 2:
            avoid_cue = f"DON'T: Get stuck for {state.features.stuck_iterations} iterations"
        elif state.features and state.features.repetition_score > 0.5:
            avoid_cue = "DON'T: Repeat the same tool call"
        elif state.decision_trace:
            last_decision = state.decision_trace[-1]
            if last_decision.action_type == "redirect":
                avoid_cue = "DON'T: Ignore redirect guidance"

        return avoid_cue

    @classmethod
    def _extract_avoid_cue_from_gene_content(cls, content: str) -> Optional[str]:
        avoid_match = re.search(r'\[AVOID\]\s*\n((?:\s*-\s*.+\n?)+)', content)
        if avoid_match:
            items = [line.strip().lstrip('- ').strip() for line in avoid_match.group(1).strip().split('\n') if line.strip()]
            if items:
                return " | ".join(items)

        must_not_match = re.search(r'MUST NOT:\s*(.+?)(?:\n|$)', content)
        if must_not_match:
            return must_not_match.group(1).strip()

        return None

    @classmethod
    def _gene_exists(cls, task_type: str) -> bool:
        """检查指定 task_type 的 Gene 是否存在"""
        if not TaskSignalMatcher._repository or not task_type:
            return False
        try:
            results = TaskSignalMatcher._repository.search_memories(
                query=f"gene:{task_type}",
                schema_type="control_gene",
                top_k=1,
            )
            for r in results:
                if r.get("memory_key") == f"gene:{task_type}":
                    return True
            return False
        except Exception:
            return False

    @classmethod
    def _update_gene(
        cls,
        gene: Dict[str, Any],
        task_type: str,
        new_content: str,
        change_description: str,
        is_failure: bool = False,
        reward: float = 0.0,
        elapsed_ms: int = 0,
    ) -> bool:
        if not TaskSignalMatcher._repository:
            return False

        try:
            metadata = gene.get("metadata", {})

            current_version = metadata.get("version", 1)
            new_version = current_version + 1

            evolution_history = metadata.get("evolution_history", [])
            evolution_history.append({
                "version": new_version,
                "change": change_description[:40],
                "at": datetime.now().isoformat(),
            })

            usage_count = metadata.get("usage_count", 0) + 1
            success_count = metadata.get("success_count", 0)

            if is_failure:
                failure_count = metadata.get("failure_count", 0) + 1
            else:
                failure_count = metadata.get("failure_count", 0)

            success_rate = success_count / max(usage_count, 1)

            metadata_updates = {
                "version": new_version,
                "evolution_history": evolution_history,
                "usage_count": usage_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "success_rate": success_rate,
                "evolved": True,
            }

            if is_failure and elapsed_ms > 0:
                result_updates = cls._update_recent_results(metadata, False, reward, elapsed_ms)
                metadata_updates.update(result_updates)
                metadata_updates["last_failure_at"] = datetime.now().isoformat()
            else:
                metadata_updates["last_evolved_at"] = datetime.now().isoformat()

            TaskSignalMatcher._repository.upsert_memory(
                title=gene.get("title", f"Gene: {task_type}"),
                content=new_content,
                schema_type="control_gene",
                category="task_strategy",
                memory_key=gene.get("memory_key", f"gene:{task_type}"),
                metadata={
                    **metadata,
                    **metadata_updates,
                }
            )

            TaskSignalMatcher._cache_loaded = False

            return True
        except Exception as e:
            logger.error("[GeneEvolution] 更新 Gene 失败: %s", e)
            return False

    @classmethod
    def _evolve_existing_gene(cls, task_type: str, avoid_cue: str, state: "LoopState") -> bool:
        if not TaskSignalMatcher._repository or not avoid_cue:
            return False

        try:
            results = TaskSignalMatcher._repository.search_memories(
                query=f"gene:{task_type}",
                schema_type="control_gene",
                top_k=1
            )

            if not results:
                logger.info("[GeneEvolution] 未找到目标 Gene，将创建新 Gene")
                return False

            gene = results[0]
            content = gene.get("content", "")

            if avoid_cue in content:
                logger.info("[GeneEvolution] 避免项已存在于 Gene 中，跳过更新")
                return True

            avoid_section = "[AVOID]"
            if avoid_section in content:
                new_content = content.replace(
                    avoid_section,
                    f"{avoid_section}\n- {avoid_cue[:80]}"
                )
            else:
                new_content = f"{content}\n\n[AVOID]\n- {avoid_cue[:80]}"

            success = cls._update_gene(
                gene=gene,
                task_type=task_type,
                new_content=new_content,
                change_description=f"evolve: {avoid_cue}",
                is_failure=False,
            )

            if success:
                try:
                    from app.server.routes.ws_event_manager import ws_publish_event
                    metadata = gene.get("metadata", {})
                    new_version = metadata.get("version", 1) + 1
                    ws_publish_event(
                        "gene_evolved",
                        {
                            "task_type": task_type,
                            "version": new_version,
                            "title": f"Gene: {task_type}",
                            "change": avoid_cue[:50],
                        }
                    )
                except Exception as e:
                    logger.debug("[GeneEvolution] 发送 WebSocket 事件失败: %s", e)

            return success
        except Exception as e:
            logger.error("[GeneEvolution] 进化 Gene 失败: %s", e)
            return False

    @classmethod
    def _update_recent_results(cls, metadata: Dict, success: bool, reward: float, elapsed_ms: int):
        recent_results = metadata.get("recent_results", [])
        recent_results.append({
            "success": success,
            "reward": reward,
            "duration_ms": elapsed_ms,
            "at": datetime.now().isoformat(),
        })
        if len(recent_results) > cls.MAX_RECENT_RESULTS:
            recent_results = recent_results[-cls.MAX_RECENT_RESULTS:]

        avg_reward = sum(r["reward"] for r in recent_results) / len(recent_results)
        avg_duration = sum(r["duration_ms"] for r in recent_results) / len(recent_results)

        consecutive_success = 0
        consecutive_failure = 0
        for r in reversed(recent_results):
            if r["success"]:
                consecutive_success += 1
                consecutive_failure = 0
            else:
                consecutive_failure += 1
                consecutive_success = 0

        return {
            "recent_results": recent_results,
            "avg_reward": avg_reward,
            "avg_duration_ms": avg_duration,
            "consecutive_success": consecutive_success,
            "consecutive_failure": consecutive_failure,
        }

    @classmethod
    def update_gene_from_failure(cls, task_type: str, avoid_cue: str, state: "LoopState", reward: float = 0.0):
        """失败后自动更新 Gene（添加避免项）"""
        if not TaskSignalMatcher._repository or not avoid_cue:
            return

        try:
            results = TaskSignalMatcher._repository.search_memories(
                query=f"gene:{task_type}",
                schema_type="control_gene",
                top_k=1
            )

            if not results:
                return

            gene = results[0]
            content = gene.get("content", "")

            if avoid_cue in content:
                return

            # 更新内容：添加避免项（使用完整 avoid_cue）
            avoid_section = "[AVOID]"
            if avoid_section in content:
                new_content = content.replace(
                    avoid_section,
                    f"{avoid_section}\n- {avoid_cue}"
                )
            else:
                new_content = f"{content}\n\n[AVOID]\n- {avoid_cue}"

            success = cls._update_gene(
                gene=gene,
                task_type=task_type,
                new_content=new_content,
                change_description=f"add: {avoid_cue}",
                is_failure=True,
                reward=reward,
                elapsed_ms=state.elapsed_ms,
            )

            # 发送 WebSocket 事件通知前端
            if success:
                try:
                    from app.server.routes.ws_event_manager import ws_publish_event
                    metadata = gene.get("metadata", {})
                    new_version = metadata.get("version", 1) + 1
                    ws_publish_event(
                        "gene_evolved",
                        {
                            "task_type": task_type,
                            "version": new_version,
                            "title": f"Gene: {task_type}",
                            "change": avoid_cue[:50],
                        }
                    )
                except Exception as e:
                    logger.debug("[GeneEvolution] 发送 WebSocket 事件失败: %s", e)

        except Exception:
            pass

    @classmethod
    def should_prompt_agent_for_gene(cls, state: "LoopState", task_type: str, avoid_cue: Optional[str]) -> bool:
        """判断是否满足 Gene 创建/进化条件

        条件：只要有 task_type 和 avoid_cue 就触发（放宽条件）
        """
        if not task_type:
            return False

        if not avoid_cue:
            return False

        # 放宽条件：只要有失败历史就触发，不强制要求 features.consecutive_failures
        return True

    @classmethod
    def _get_existing_gene(cls, user_input: str) -> Optional[Dict[str, Any]]:
        if not TaskSignalMatcher._repository or not user_input:
            return None

        try:
            results = TaskSignalMatcher._repository.search_memories(
                query=user_input,
                top_k=1,
                schema_type="control_gene",
            )

            if results:
                item = results[0]
                score = item.get("embedding_score", 0.0)
                if score >= 0.7:
                    return {
                        "task_type": item.get("metadata", {}).get("task_type", ""),
                        "content": item.get("content", ""),
                        "score": score,
                    }
        except Exception:
            pass

        return None

    @classmethod
    def _get_all_genes(cls) -> List[Dict[str, Any]]:
        if not TaskSignalMatcher._repository:
            return []

        try:
            results = TaskSignalMatcher._repository.search_memories(
                query="gene:",
                schema_type="control_gene",
                top_k=50
            )

            genes = []
            for item in results:
                meta = item.get("metadata", {})
                genes.append({
                    "task_type": meta.get("task_type", ""),
                    "version": meta.get("version", 1),
                    "title": item.get("title", ""),
                    "memory_key": item.get("memory_key", ""),
                })
            return genes
        except Exception:
            return []

    @classmethod
    def build_gene_creation_prompt(cls, state: "LoopState", user_input: str) -> str:
        context_lines = []

        if state.last_error:
            context_lines.append(f"错误信息: {state.last_error[:200]}")

        if state.features:
            if state.features.stuck_iterations > 0:
                context_lines.append(f"停滞轮数: {state.features.stuck_iterations}")
            if state.features.repetition_score > 0.3:
                context_lines.append(f"重复程度: {state.features.repetition_score:.2f}")

        if state.decision_trace:
            recent_decisions = state.decision_trace[-3:]
            context_lines.append("最近决策:")
            for d in recent_decisions:
                context_lines.append(f"  - {d.action_type}: {d.stop_reason or 'no reason'}")

        context_str = "\n".join(context_lines) if context_lines else "无具体错误信息"

        existing_gene = cls._get_existing_gene(user_input)

        if existing_gene:
            existing_section = f"""
现有 Gene:
```
{existing_gene['content'][:500]}
```"""
        else:
            existing_section = ""

        prompt = f"""[系统提示]
任务执行失败，请按以下步骤处理：

步骤1: 使用 memory list_genes 查看所有 Gene 列表
步骤2: 如有相关 Gene，使用 memory get_gene task_type=<类型> 查看详情
步骤3: 判断是否需要创建新 Gene 或更新现有 Gene

用户输入: {user_input[:200]}

失败上下文:
{context_str}
{existing_section}

如果决定创建/更新 Gene，请按以下格式返回:
```gene
[HARD CONSTRAINTS]
[任务类型]: <简短描述任务类型>
[CONTROL ACTION]
MUST: <应该采取的正确做法>
MUST NOT: <应该避免的错误做法>
[AVOID]
- <具体的避免事项1>
- <具体的避免事项2>
```

要求:
1. 先查看已有 Gene 再决定
2. 任务类型要具体且可识别
3. MUST/MUST NOT 要 actionable
4. AVOID 要基于具体失败经验
5. 总长度控制在 200 tokens 以内"""

        return prompt

    @classmethod
    def parse_agent_gene_response(cls, response: str) -> Optional[Dict[str, Any]]:
        if not response or not response.strip():
            return None

        gene_match = re.search(r'```gene\s*\n(.*?)\n```', response, re.DOTALL)
        if not gene_match:
            gene_match = re.search(r'\[HARD CONSTRAINTS\](.*)', response, re.DOTALL)
            if not gene_match:
                return None
            content = "[HARD CONSTRAINTS]" + gene_match.group(1).strip()
        else:
            content = gene_match.group(1).strip()

        if not content or len(content) < 30:
            logger.warning(f"[GeneEvolution] Gene 内容太短: {len(content)} 字符")
            return None

        mode_match = re.search(r'\[MODE\]:\s*(EVOLVE|CREATE)', content, re.IGNORECASE)
        mode = "create"
        target_task_type = None
        if mode_match:
            mode = mode_match.group(1).lower()

        target_match = re.search(r'\[TARGET\]:\s*(.+?)(?:\n|$)', content)
        if target_match:
            target_val = target_match.group(1).strip()
            if target_val and target_val.lower() != "new":
                target_task_type = target_val

        task_type_match = re.search(r'\[任务类型\]:\s*(.+?)(?:\n|$)', content)
        task_type = task_type_match.group(1).strip() if task_type_match else None

        if not task_type and target_task_type:
            task_type = target_task_type

        has_must = re.search(r'MUST:', content) is not None
        has_must_not = re.search(r'MUST NOT:', content) is not None

        if not task_type:
            logger.warning(f"[GeneEvolution] 无法解析任务类型")
            return None

        if not has_must and not has_must_not:
            logger.warning(f"[GeneEvolution] 缺少 MUST 或 MUST NOT 约束")
            return None

        task_type_normalized = re.sub(r'[^\w\u4e00-\u9fff]+', '_', task_type).lower().strip('_')
        if not task_type_normalized or task_type_normalized == 'unknown':
            return None

        signals = [task_type_normalized]

        return {
            "task_type": task_type_normalized,
            "content": content,
            "signals": signals,
            "forbidden_tools": [],
            "preferred_tools": [],
            "source": "agent_created",
            "mode": mode,
            "target_task_type": target_task_type,
        }

    @classmethod
    def save_agent_created_gene(cls, gene_data: Dict[str, Any]) -> bool:
        if not TaskSignalMatcher._repository:
            return False

        try:
            task_type = gene_data["task_type"]
            content = gene_data["content"]
            signals = gene_data.get("signals", [task_type])
            memory_key = f"gene:{task_type}"

            existing_history = []
            existing_version = 0
            existing_usage = 0
            existing_success = 0
            existing_failure = 0

            try:
                results = TaskSignalMatcher._repository.search_memories(
                    query=memory_key,
                    schema_type="control_gene",
                    top_k=1,
                )
                for r in results:
                    if r.get("memory_key") == memory_key:
                        existing_meta = r.get("metadata", {})
                        existing_history = existing_meta.get("evolution_history", [])
                        existing_version = existing_meta.get("version", 0)
                        existing_usage = existing_meta.get("usage_count", 0)
                        existing_success = existing_meta.get("success_count", 0)
                        existing_failure = existing_meta.get("failure_count", 0)
                        break
            except Exception:
                pass

            base_version = max(existing_version, len(existing_history))
            new_version = base_version + 1
            is_update = new_version > 1

            evolution_history = existing_history + [{
                "version": new_version,
                "change": "agent_updated" if is_update else "agent_created",
                "at": datetime.now().isoformat(),
            }]

            if existing_usage > 0:
                success_rate = existing_success / existing_usage
            else:
                success_rate = 0.5

            TaskSignalMatcher._repository.upsert_memory(
                title=f"Gene: {task_type}",
                content=content,
                schema_type="control_gene",
                category="task_strategy",
                memory_key=memory_key,
                metadata={
                    "task_type": task_type,
                    "signals": signals,
                    "forbidden_tools": gene_data.get("forbidden_tools", []),
                    "preferred_tools": gene_data.get("preferred_tools", []),
                    "version": new_version,
                    "evolution_history": evolution_history,
                    "usage_count": existing_usage,
                    "success_count": existing_success,
                    "failure_count": existing_failure,
                    "success_rate": success_rate,
                    "source": "agent_created",
                },
                merge_strategy="replace",
            )

            TaskSignalMatcher._cache_loaded = False

            try:
                from app.server.routes.ws_event_manager import ws_publish_event
                ws_publish_event(
                    "gene_created",
                    {
                        "task_type": task_type,
                        "version": new_version,
                        "is_update": is_update,
                        "title": f"Gene: {task_type}",
                        "content_preview": content[:200] + "..." if len(content) > 200 else content,
                    }
                )
            except Exception as e:
                logger.debug("[GeneEvolution] 发送 WebSocket 事件失败: %s", e)

            return True

        except Exception:
            return False

    @classmethod
    async def create_gene_with_llm(cls, user_input: str, state: "LoopState", llm_engine) -> bool:
        """
        独立调用 LLM 创建 Gene

        Args:
            user_input: 用户输入
            state: 当前循环状态
            llm_engine: LLM 引擎实例

        Returns:
            是否成功创建 Gene
        """
        if not TaskSignalMatcher._repository:
            logger.warning("[GeneEvolution] 记忆系统未初始化，无法创建 Gene")
            return False

        try:
            # 1. 获取现有 Gene 信息
            existing_gene = cls._get_existing_gene(user_input)

            if existing_gene:
                created_at = existing_gene.get('metadata', {}).get('created_at', '')
                if created_at:
                    from datetime import datetime, timedelta
                    try:
                        created_time = datetime.fromisoformat(created_at)
                        if datetime.now() - created_time < timedelta(minutes=5):
                            logger.info("[GeneEvolution] 最近已创建过 Gene，跳过")
                            return True
                    except:
                        pass

            # 2. 构建完整的运行上下文
            context_lines = []

            # 2.1 错误信息
            if state.last_error:
                context_lines.append(f"【最后错误】{state.last_error[:300]}")

            # 2.2 工具执行历史（最近3个）
            if state.tool_traces:
                context_lines.append("\n【工具执行历史】")
                for i, trace in enumerate(state.tool_traces[-3:], 1):
                    tool_name = trace.get("tool", "unknown")
                    success = trace.get("success", False)
                    result = trace.get("result", {})
                    error = result.get("error", "") if isinstance(result, dict) else ""
                    context_lines.append(f"  {i}. {tool_name}: {'成功' if success else '失败'}")
                    if error:
                        context_lines.append(f"     错误: {error[:150]}")

            # 2.3 决策轨迹（最近3个）
            if state.decision_trace:
                context_lines.append("\n【决策轨迹】")
                for i, decision in enumerate(state.decision_trace[-3:], 1):
                    action = decision.action_type
                    reason = decision.stop_reason or "无"
                    context_lines.append(f"  {i}. 动作: {action}")
                    if decision.stop_reason:
                        context_lines.append(f"     原因: {reason[:150]}")

            # 2.4 特征状态
            if state.features:
                context_lines.append("\n【执行特征】")
                if state.features.stuck_iterations > 0:
                    context_lines.append(f"  - 停滞轮数: {state.features.stuck_iterations}")
                if state.features.repetition_score > 0.3:
                    context_lines.append(f"  - 重复程度: {state.features.repetition_score:.2f}")
                if hasattr(state.features, 'consecutive_failures') and state.features.consecutive_failures > 0:
                    context_lines.append(f"  - 连续失败: {state.features.consecutive_failures}")

            context_str = "\n".join(context_lines) if context_lines else "无具体错误信息"

            all_genes = cls._get_all_genes()

            genes_list_section = ""
            if all_genes:
                genes_list_section = "\n【现有 Gene 列表】\n"
                for g in all_genes:
                    genes_list_section += f"  - [{g['task_type']}] v{g['version']} ({g['title']})\n"
                genes_list_section += "\n请判断当前任务与哪个 Gene 最相关：\n"
                genes_list_section += "  - 如果高度相关（同一类任务），选择 EVOLVE 模式进化该 Gene\n"
                genes_list_section += "  - 如果不相关或需要全新规则，选择 CREATE 模式新建 Gene\n"

            existing_content = ""
            if existing_gene:
                existing_content = f"""

【最匹配的现有 Gene 内容】
```
{existing_gene['content'][:500]}
```"""

            prompt = f"""你是 Gene 进化系统。分析以下失败信息，判断是进化现有 Gene 还是创建新 Gene。

【用户输入】
{user_input[:200]}

【运行失败分析】
{context_str}
{genes_list_section}
{existing_content}

请按以下格式输出（只输出代码块，不要其他内容）：

```gene
[MODE]: <EVOLVE 或 CREATE>
[TARGET]: <如果是 EVOLVE，填写要进化的 task_type；如果是 CREATE，填 new>
[HARD CONSTRAINTS]
[任务类型]: <简短描述任务类型>
[CONTROL ACTION]
MUST: <应该采取的正确做法>
MUST NOT: <应该避免的错误做法>
[AVOID]
- <具体的避免事项1>
- <具体的避免事项2>
```"""

            # 3. 调用 LLM（带重试）
            logger.info("[GeneEvolution] 调用 LLM 创建 Gene")
            messages = [{"role": "user", "content": prompt}]

            response = None
            for attempt in range(2):
                try:
                    response = await llm_engine.chat(
                        messages=messages,
                        max_tokens=3000,
                        temperature=0.3
                    )
                    raw_content = getattr(response, 'content', None) or ''
                    logger.info(f"[GeneEvolution] LLM 原始返回 (尝试 {attempt + 1}/2): 长度={len(raw_content)} | 内容={repr(raw_content[:800])}")
                    if response and raw_content and raw_content.strip() and len(raw_content) > 200:
                        break
                    logger.warning(f"[GeneEvolution] LLM 返回太短或为空，尝试 {attempt + 1}/2 | response={type(response).__name__} | content={repr(raw_content[:200]) if raw_content else 'None'}")
                except Exception as e:
                    logger.warning(f"[GeneEvolution] LLM 调用失败，尝试 {attempt + 1}/2: {e}")

            final_content = getattr(response, 'content', None) or ''
            if not response or not final_content or not final_content.strip():
                logger.warning(f"[GeneEvolution] LLM 返回为空，放弃创建 Gene | response={response is not None} | content={repr(final_content[:200]) if final_content else 'None'}")
                return False

            logger.info(f"[GeneEvolution] LLM 返回成功 | 长度={len(final_content)} | 内容预览: {final_content[:300]}...")

            # 4. 解析并保存 Gene
            gene_data = cls.parse_agent_gene_response(response.content)
            if gene_data:
                mode = gene_data.get("mode", "create")
                target = gene_data.get("target_task_type")

                if mode == "evolve" and target:
                    logger.info("[GeneEvolution] LLM 选择进化模式 | target=%s", target)

                    target_exists = cls._gene_exists(target)
                    if not target_exists:
                        logger.warning("[GeneEvolution] LLM 要求进化不存在的 Gene=%s，改为创建新 Gene", target)
                        gene_data["task_type"] = target
                        success = cls.save_agent_created_gene(gene_data)
                    else:
                        avoid_cue = cls._extract_avoid_cue_from_gene_content(gene_data.get("content", ""))
                        if avoid_cue:
                            success = cls._evolve_existing_gene(target, avoid_cue, state)
                            if success:
                                logger.info("[GeneEvolution] 进化 Gene 成功 | target=%s", target)
                            else:
                                logger.warning("[GeneEvolution] 进化 Gene 失败，尝试创建新 Gene")
                                gene_data["task_type"] = target
                                success = cls.save_agent_created_gene(gene_data)
                        else:
                            logger.warning("[GeneEvolution] 无法提取进化内容，创建新 Gene")
                            success = cls.save_agent_created_gene(gene_data)
                else:
                    logger.info("[GeneEvolution] LLM 选择新建模式 | task_type=%s", gene_data.get("task_type"))
                    success = cls.save_agent_created_gene(gene_data)

                if success:
                    logger.info("[GeneEvolution] Gene 操作成功 | mode=%s | task_type=%s", mode, gene_data.get("task_type"))
                else:
                    logger.warning("[GeneEvolution] Gene 操作失败")
                return success
            else:
                logger.warning("[GeneEvolution] 无法从 LLM 响应中解析 Gene")
                return False

        except Exception as e:
            logger.error("[GeneEvolution] 创建 Gene 时出错: %s", e)
            return False

    @classmethod
    def record_success(cls, task_type: str, reward: float = 1.0, elapsed_ms: int = 0):
        if not TaskSignalMatcher._repository or not task_type:
            return

        try:
            results = TaskSignalMatcher._repository.search_memories(
                query=f"gene:{task_type}",
                schema_type="control_gene",
                top_k=1
            )

            if not results:
                return

            gene = results[0]
            metadata = gene.get("metadata", {})

            usage_count = metadata.get("usage_count", 0) + 1
            success_count = metadata.get("success_count", 0) + 1

            result_updates = cls._update_recent_results(metadata, True, reward, elapsed_ms)

            TaskSignalMatcher._repository.upsert_memory(
                title=gene.get("title", f"Gene: {task_type}"),
                content=gene.get("content", ""),
                schema_type="control_gene",
                category="task_strategy",
                memory_key=gene.get("memory_key", f"gene:{task_type}"),
                metadata={
                    **metadata,
                    "usage_count": usage_count,
                    "success_count": success_count,
                    "success_rate": success_count / usage_count,
                    "last_success_at": datetime.now().isoformat(),
                    **result_updates,
                }
            )
        except Exception:
            pass

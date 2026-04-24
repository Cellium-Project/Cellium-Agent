# -*- coding: utf-8 -*-
"""
HardConstraintRenderer - 强约束渲染器 (PromptBuilder v3)

核心：
  1. 三段结构：HARD CONSTRAINTS + CONTROL ACTION + CONTEXT HINT
  2. 自然语言强约束（不是 DSL 标签）
  3. FAILURE CONDITIONS 判定标准
  4. 具体行为规则（MUST DO / MUST NOT DO）
  5. token ≤ 80
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .loop_state import LoopState

from .loop_state import ControlDecision
from app.agent.heuristics.features import get_call_signature


@dataclass
class HardConstraint:
    """渲染后的强约束"""
    hard_constraints: str      # 三段结构文本 (≤80 tokens)
    failure_conditions: str    # 失败判定标准
    trigger_reason: str        # 触发原因
    forbidden: List[str]       # 禁止项
    preferred: List[str]       # 推荐项
    max_tokens: int            # 输出 token 限制
    force_stop: bool = False   # 是否强制终止


class FailureConditionBuilder:
    """Failure Conditions 构建器"""

    @staticmethod
    def build(features: Optional[Any] = None) -> str:
        """
        构建 failure condition 文本

        格式：
            [FAILURE CONDITIONS]
            Your response will be considered FAILED if:
            - condition 1
            - condition 2
        """
        conditions = []

        if features:
            # 工具重复
            if hasattr(features, 'repetition_score') and features.repetition_score > 0.5:
                conditions.append("You repeat the same tool call")

            # 停滞
            if hasattr(features, 'stuck_iterations') and features.stuck_iterations > 2:
                conditions.append(f"You make no progress after {features.stuck_iterations} attempts")

            # 输出循环
            if hasattr(features, 'is_output_loop') and features.is_output_loop:
                conditions.append("You produce identical repeated output")

            # 上下文压力
            if hasattr(features, 'context_saturation') and features.context_saturation > 0.75:
                conditions.append("You ignore context compression request")

        if not conditions:
            return ""

        lines = ["[FAILURE CONDITIONS]", "Your response will be considered FAILED if:"]
        for cond in conditions[:3]:  # 限制数量
            lines.append(f"- {cond}")

        return "\n".join(lines)


class TaskSignalMatcher:
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
            results = cls._repository.search(
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


class HardConstraintTemplates:
    REDIRECT_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Change strategy, use different tool.
MUST NOT: Repeat same tool, continue same path.
[CONTEXT HINT]
Current approach may be stuck."""

    REDIRECT_FORBIDDEN_TOOLS = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Use {forbidden_tools}, switch approach.
MUST NOT: Repeat {last_tool}.
[CONTEXT HINT]
Try {suggested_tools}."""

    COMPRESS_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS
MUST: Summarize in ≤{max_tokens} tokens, bullet points.
MUST NOT: Repeat details, expand new ideas.
[CONTEXT HINT]
Context saturated. Prioritize key facts."""

    TERMINATE_HARD = """[HARD CONSTRAINTS]
You MUST follow termination signal. This is FINAL.
[CONTROL ACTION]
Action: TERMINATE
MUST: Stop immediately, provide summary + next steps.
MUST NOT: Continue exploration, request more input.
[FAILURE CONDITIONS]
- If you continue after this signal = FAILED
- If you ignore summary request = FAILED"""

    COMPRESS_REDIRECT_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS + REDIRECT
MUST: Summarize first (≤{max_tokens} tokens), then change tool.
MUST NOT: Repeat details, use same tool.
[CONTEXT HINT]
Stuck + context saturated."""

    RETRY_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: RETRY
MUST: Keep direction, adjust prompt/params.
MUST NOT: Repeat exact same approach.
[CONTEXT HINT]
Minor stuck. Try refinement."""

    COMPRESS_RETRY_HARD = """[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: COMPRESS + RETRY
MUST: Summarize first (≤{max_tokens} tokens), then refine approach.
MUST NOT: Repeat details, use exact same params.
[CONTEXT HINT]
Stuck + need refinement."""

    CONTINUE_HARD = ""

    @classmethod
    def get_template(cls, action: str) -> str:
        return getattr(cls, action.upper() + "_HARD", cls.CONTINUE_HARD)

    @classmethod
    def render_for_task(cls, user_input: str, action: str) -> HardConstraint:
        matches = GeneComposer.match_multiple(user_input)
        composed = GeneComposer.compose(matches)
        
        if composed and action in ["redirect", "retry"]:
            component_tasks = composed.get("component_tasks", [])
            if len(component_tasks) > 1:
                return HardConstraint(
                    hard_constraints=composed["gene_template"],
                    failure_conditions="[FAILURE CONDITIONS]\n- Ignoring multi-task protocol = FAILED",
                    trigger_reason=f"Combined tasks: {','.join(component_tasks)}",
                    forbidden=composed["forbidden_tools"],
                    preferred=composed["preferred_tools"],
                    max_tokens=500,
                )
            else:
                return HardConstraint(
                    hard_constraints=composed["gene_template"],
                    failure_conditions="[FAILURE CONDITIONS]\n- Ignoring task-specific protocol = FAILED",
                    trigger_reason=f"Task signal matched: {composed['task_type']}",
                    forbidden=composed["forbidden_tools"],
                    preferred=composed["preferred_tools"],
                    max_tokens=300,
                )
        
        template = cls.get_template(action)
        return HardConstraint(
            hard_constraints=template,
            failure_conditions="",
            trigger_reason="Generic rule triggered",
            forbidden=[],
            preferred=[],
            max_tokens=80,
        )


class GeneEvolution:
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
        import re
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
            metadata = gene.get("metadata", {})
            
            if avoid_cue in content:
                logger.info("[GeneEvolution] 避免项已存在于 Gene 中，跳过更新")
                return True
            
            avoid_section = "[AVOID]"
            if avoid_section in content:
                content = content.replace(
                    avoid_section,
                    f"{avoid_section}\n- {avoid_cue[:80]}"
                )
            else:
                content += f"\n\n[AVOID]\n- {avoid_cue[:80]}"
            
            current_version = metadata.get("version", 1)
            new_version = current_version + 1
            
            evolution_history = metadata.get("evolution_history", [])
            evolution_history.append({
                "version": new_version,
                "change": f"evolve: {avoid_cue[:40]}",
                "at": datetime.now().isoformat(),
            })
            
            usage_count = metadata.get("usage_count", 0) + 1
            failure_count = metadata.get("failure_count", 0)
            
            TaskSignalMatcher._repository.upsert_memory(
                title=gene.get("title", f"Gene: {task_type}"),
                content=content,
                schema_type="control_gene",
                category="task_strategy",
                memory_key=gene.get("memory_key", f"gene:{task_type}"),
                metadata={
                    **metadata,
                    "version": new_version,
                    "evolution_history": evolution_history,
                    "usage_count": usage_count,
                    "failure_count": failure_count,
                    "last_evolved_at": datetime.now().isoformat(),
                    "evolved": True,
                }
            )
            
            TaskSignalMatcher._cache_loaded = False
            
            try:
                from app.server.routes.ws_event_manager import ws_publish_event
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
            
            return True
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
            metadata = gene.get("metadata", {})
            
            if avoid_cue in content:
                return
            
            avoid_section = "[AVOID]"
            if avoid_section in content:
                content = content.replace(
                    avoid_section,
                    f"{avoid_section}\n- {avoid_cue}"
                )
            else:
                content += f"\n\n[AVOID]\n- {avoid_cue}"
            
            current_version = metadata.get("version", 1)
            new_version = current_version + 1
            
            evolution_history = metadata.get("evolution_history", [])
            evolution_history.append({
                "version": new_version,
                "change": f"add: {avoid_cue[:40]}",
                "at": datetime.now().isoformat(),
            })
            
            usage_count = metadata.get("usage_count", 0) + 1
            failure_count = metadata.get("failure_count", 0) + 1
            
            result_updates = cls._update_recent_results(metadata, False, reward, state.elapsed_ms)
            
            TaskSignalMatcher._repository.upsert_memory(
                title=gene.get("title", f"Gene: {task_type}"),
                content=content,
                schema_type="control_gene",
                category="task_strategy",
                memory_key=gene.get("memory_key", f"gene:{task_type}"),
                metadata={
                    **metadata,
                    "version": new_version,
                    "evolution_history": evolution_history,
                    "usage_count": usage_count,
                    "failure_count": failure_count,
                    "last_failure_at": datetime.now().isoformat(),
                    "evolved": True,
                    **result_updates,
                }
            )
            
            TaskSignalMatcher._cache_loaded = False
            
        except Exception:
            pass
    
    @classmethod
    def should_prompt_agent_for_gene(cls, state: "LoopState", task_type: str, avoid_cue: Optional[str]) -> bool:
        """判断是否需要提示 Agent 创建 Gene
        
        返回 True 的情况：
        1. 没有识别到任务类型（task_type 为空）
        2. 自动提取失败（avoid_cue 为空）
        3. 连续多次失败且 Gene 没有改善
        """
        if not task_type:
            return True
        
        if not avoid_cue:
            return True
        
        # 检查是否连续失败且没有改善
        if state.features and state.features.consecutive_failures >= 3:
            return True
        
        return False
    
    @classmethod
    def _get_existing_gene(cls, user_input: str) -> Optional[Dict[str, Any]]:
        if not TaskSignalMatcher._repository or not user_input:
            return None
        
        try:
            results = TaskSignalMatcher._repository.search(
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
        import re
        
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
        
        if not content or len(content) < 50:
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
        
        has_must = re.search(r'MUST:', content) is not None
        has_must_not = re.search(r'MUST NOT:', content) is not None
        
        if not task_type or (not has_must and not has_must_not):
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
                existing = TaskSignalMatcher._repository.get_by_memory_key(memory_key)
                if existing:
                    existing_meta = existing.get("metadata", {})
                    existing_history = existing_meta.get("evolution_history", [])
                    existing_version = existing_meta.get("version", 0)
                    existing_usage = existing_meta.get("usage_count", 0)
                    existing_success = existing_meta.get("success_count", 0)
                    existing_failure = existing_meta.get("failure_count", 0)
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
                    "success_rate": existing_success / max(existing_usage, 1),
                    "source": "agent_created",
                }
            )
            
            TaskSignalMatcher._cache_loaded = False
            
            # 发送 WebSocket 事件通知前端 Gene 已创建/更新
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
        独立调用 LLM 创建 Gene，不占用主 Agent 轮次

        Args:
            user_input: 用户输入
            state: 当前循环状态
            llm_engine: LLM 引擎实例

        Returns:
            是否成功创建 Gene
        """
        import logging
        logger = logging.getLogger(__name__)

        if not TaskSignalMatcher._repository:
            logger.warning("[GeneEvolution] 记忆系统未初始化，无法创建 Gene")
            return False

        try:
            # 1. 获取现有 Gene 信息
            existing_gene = cls._get_existing_gene(user_input)
            
            # 检查是否最近刚创建过 Gene（避免重复创建）
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
                        max_tokens=800,
                        temperature=0.3
                    )
                    raw_content = getattr(response, 'content', None) or ''
                    logger.info(f"[GeneEvolution] LLM 原始返回 (尝试 {attempt + 1}/2): {repr(raw_content[:500])}")
                    if response and raw_content and raw_content.strip():
                        break
                    logger.warning(f"[GeneEvolution] LLM 返回为空，尝试 {attempt + 1}/2 | response={type(response).__name__} | content={repr(raw_content[:200]) if raw_content else 'None'}")
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


class GeneComposer:
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
            tool for m in matches for m in matches for tool in m["preferred_tools"]
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


class ActionFusion:
    PRIORITY = {
        "terminate": 100,
        "compress": 50,
        "retry": 40,
        "redirect": 30,
        "continue": 0,
    }

    FUSABLE = {
        ("compress", "redirect"): "compress_redirect",
        ("compress", "retry"): "compress_retry",
    }

    @classmethod
    def fuse(cls, actions: List[str]) -> str:
        if not actions:
            return "continue"
        if "terminate" in actions:
            return "terminate"
        action_set = tuple(sorted(set(actions)))
        if action_set in cls.FUSABLE:
            return cls.FUSABLE[action_set]
        return max(actions, key=lambda a: cls.PRIORITY.get(a, 0))


class HardConstraintRenderer:
    """
    强约束渲染器 - PromptBuilder v3

    三段结构：
      1. HARD CONSTRAINTS：不可违反的判定标准
      2. CONTROL ACTION：具体行为规则
      3. CONTEXT HINT：可选的上下文提示

    特点：
      - 自然语言强约束（非 DSL 标签）
      - MUST DO / MUST NOT DO 格式
      - FAILURE CONDITIONS 判定标准
      - token ≤ 80
    """

    def __init__(self, max_output_tokens: int = 100):
        self.max_output_tokens = max_output_tokens

    def render(
        self,
        decision: ControlDecision,
        features: Optional[Any] = None,
        state: Optional["LoopState"] = None,
    ) -> HardConstraint:
        """
        渲染强约束

        Args:
            decision: 控制决策
            features: 派生特征
            state: 状态

        Returns:
            HardConstraint（三段结构）
        """
        action = decision.action_type

        # 构建 failure condition
        failure_conditions = FailureConditionBuilder.build(features)

        # 构建禁止/推荐项
        forbidden, preferred = self._build_constraints(state, features)

        if action == "terminate":
            return self._render_terminate(failure_conditions)
        elif action == "compress":
            return self._render_compress(failure_conditions)
        elif action == "redirect":
            return self._render_redirect(failure_conditions, forbidden, preferred, state, decision)
        elif action == "compress_redirect":
            return self._render_compress_redirect(failure_conditions, forbidden, preferred)
        elif action == "retry":
            return self._render_retry(failure_conditions)
        elif action == "compress_retry":
            return self._render_compress_retry(failure_conditions)
        else:
            return HardConstraint(
                hard_constraints="",
                failure_conditions="",
                trigger_reason="",
                forbidden=[],
                preferred=[],
                max_tokens=self.max_output_tokens,
            )

    def _render_terminate(self, failure_conditions: str) -> HardConstraint:
        """渲染 terminate 约束"""
        return HardConstraint(
            hard_constraints=HardConstraintTemplates.TERMINATE_HARD,
            failure_conditions=failure_conditions or "Continuing after this signal = FAILED",
            trigger_reason="terminate",
            forbidden=["continue", "explore"],
            preferred=["summary", "next_steps"],
            max_tokens=200,
            force_stop=True,
        )

    def _render_compress(self, failure_conditions: str) -> HardConstraint:
        """渲染 compress 约束"""
        template = HardConstraintTemplates.COMPRESS_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Ignoring compression request = FAILED",
            trigger_reason="compress",
            forbidden=["repeat", "verbose", "expand"],
            preferred=["bullet_points", "key_facts", "concise"],
            max_tokens=self.max_output_tokens,
        )

    def _render_redirect(
        self,
        failure_conditions: str,
        forbidden: List[str],
        preferred: List[str],
        state: Optional["LoopState"],
        decision: ControlDecision,
    ) -> HardConstraint:
        """渲染 redirect 约束"""
        # 如果有具体的禁止/推荐工具，使用详细模板
        if forbidden and state:
            last_tool = forbidden[-1] if forbidden else "last_tool"
            suggested = decision.suggested_tools[:3] if decision.suggested_tools else preferred
            suggested_str = ", ".join(suggested) if suggested else "different tool"

            forbidden_str = ", ".join(forbidden[:2]) if forbidden else last_tool

            hard_constraints = f"""[HARD CONSTRAINTS]
You MUST obey control instruction. Violations = incorrect.
[CONTROL ACTION]
Action: REDIRECT
MUST: Use {suggested_str}, change strategy.
MUST NOT: Use {forbidden_str}.
[CONTEXT HINT]
Current approach may be stuck."""
        else:
            hard_constraints = HardConstraintTemplates.REDIRECT_HARD

        return HardConstraint(
            hard_constraints=hard_constraints,
            failure_conditions=failure_conditions or "Repeating same action = FAILED",
            trigger_reason="redirect",
            forbidden=forbidden,
            preferred=preferred,
            max_tokens=self.max_output_tokens,
        )

    def _render_compress_redirect(
        self,
        failure_conditions: str,
        forbidden: List[str],
        preferred: List[str],
    ) -> HardConstraint:
        """渲染 compress+redirect 融合约束"""
        template = HardConstraintTemplates.COMPRESS_REDIRECT_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Repeating + ignoring compress = FAILED",
            trigger_reason="compress_redirect",
            forbidden=forbidden + ["repeat", "verbose"],
            preferred=preferred + ["bullet_points", "different_tool"],
            max_tokens=self.max_output_tokens,
        )

    def _render_retry(self, failure_conditions: str) -> HardConstraint:
        """渲染 retry 约束"""
        return HardConstraint(
            hard_constraints=HardConstraintTemplates.RETRY_HARD,
            failure_conditions=failure_conditions or "Repeating exact same approach = FAILED",
            trigger_reason="retry",
            forbidden=["repeat_exact", "same_prompt"],
            preferred=["adjust_params", "refine_approach"],
            max_tokens=self.max_output_tokens,
        )

    def _render_compress_retry(
        self,
        failure_conditions: str,
    ) -> HardConstraint:
        """渲染 compress+retry 融合约束"""
        template = HardConstraintTemplates.COMPRESS_RETRY_HARD.format(
            max_tokens=self.max_output_tokens
        )
        return HardConstraint(
            hard_constraints=template,
            failure_conditions=failure_conditions or "Repeating + ignoring compress = FAILED",
            trigger_reason="compress_retry",
            forbidden=["repeat", "verbose", "same_params"],
            preferred=["bullet_points", "adjust_params"],
            max_tokens=self.max_output_tokens,
        )

    def _build_constraints(
        self,
        state: Optional["LoopState"],
        features: Optional[Any],
    ) -> tuple:
        """构建禁止/推荐项（使用签名而非工具名）"""
        forbidden = []
        preferred = []

        if state and state.tool_traces:
            # 禁止特定签名（工具+命令），而非整个工具
            recent_signatures = [
                get_call_signature(t)
                for t in state.tool_traces[-3:]
            ]
            forbidden = [s for s in recent_signatures if s]

            # 推荐未使用的工具（保持原有逻辑）
            if state.available_tools:
                used = set(
                    t.get("tool_name") or t.get("tool")
                    for t in state.tool_traces
                )
                preferred = [t for t in state.available_tools if t not in used][:3]

        return forbidden, preferred

    def render_multi_action(
        self,
        decisions: List[ControlDecision],
        features: Optional[Any] = None,
        state: Optional["LoopState"] = None,
    ) -> HardConstraint:
        """多 Action 融合渲染"""
        if not decisions:
            return HardConstraint(
                hard_constraints="",
                failure_conditions="",
                trigger_reason="none",
                forbidden=[],
                preferred=[],
                max_tokens=self.max_output_tokens,
            )

        actions = [d.action_type for d in decisions]
        fused_action = ActionFusion.fuse(actions)

        fused_decision = ControlDecision(action_type=fused_action)
        if decisions:
            fused_decision.suggested_tools = decisions[0].suggested_tools
            fused_decision.guidance_message = decisions[0].guidance_message

        return self.render(fused_decision, features, state)

    def render_combined(self, constraint: HardConstraint) -> str:
        """
        渲染组合的三段结构文本

        用于注入到 system prompt

        Args:
            constraint: HardConstraint

        Returns:
            组合后的文本
        """
        parts = []

        if constraint.hard_constraints:
            parts.append(constraint.hard_constraints)

        if constraint.failure_conditions:
            parts.append(constraint.failure_conditions)

        return "\n\n".join(parts) if parts else ""

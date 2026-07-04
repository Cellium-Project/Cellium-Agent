# -*- coding: utf-8 -*-
"""
PromptBuilder - 提示词构建器
"""

import logging
from typing import Dict, List, Optional, Set

from app.agent.prompt.piece import PromptPiece, Stability

logger = logging.getLogger(__name__)


class PromptBuilder:

    def __init__(self):
        self._pieces: Dict[str, PromptPiece] = {}
        self._dynamic_counter = 0

    def register(self, piece: PromptPiece) -> None:
        self._pieces[piece.name] = piece
        logger.debug("[PromptBuilder] 注册: %s (stability=%s)", piece.name, piece.stability)

    def unregister(self, name: str) -> bool:
        if name in self._pieces:
            del self._pieces[name]
            logger.debug("[PromptBuilder] 注销: %s", name)
            return True
        return False

    def enable(self, name: str, enabled: bool = True) -> bool:
        if name in self._pieces:
            self._pieces[name].enabled = enabled
            logger.debug("[PromptBuilder] %s: %s", "启用" if enabled else "禁用", name)
            return True
        return False

    def get_piece(self, name: str) -> Optional[PromptPiece]:
        return self._pieces.get(name)

    def inject(self, content: str, name: Optional[str] = None,
               stability: Stability = "dynamic", priority: int = 200) -> str:
        self._dynamic_counter += 1
        piece_name = name or f"_dynamic_{self._dynamic_counter}"
        self._pieces[piece_name] = PromptPiece(
            name=piece_name,
            content=content,
            stability=stability,
            priority=priority,
            enabled=True,
        )
        logger.debug("[PromptBuilder] 动态注入: %s (stability=%s, priority=%d)",
                     piece_name, stability, priority)
        return piece_name

    def clear_dynamic(self) -> int:
        dynamic_names = [
            name for name in self._pieces
            if name.startswith("_dynamic_") or name.startswith("_control_")
        ]
        for name in dynamic_names:
            del self._pieces[name]
        count = len(dynamic_names)
        if count:
            logger.debug("[PromptBuilder] 清除动态注入: %d 个", count)
        return count

    # ---- listing ----

    def list_pieces(self, enabled_only: bool = False) -> List[PromptPiece]:
        pieces = list(self._pieces.values())
        if enabled_only:
            pieces = [p for p in pieces if p.enabled]
        return sorted(pieces, key=lambda p: p.priority)

    def _get_enabled_by_stability(self, stability: Stability) -> List[PromptPiece]:
        return sorted(
            [p for p in self._pieces.values() if p.enabled and p.stability == stability],
            key=lambda p: p.priority,
        )

    # ---- build ----

    def build(self, context: dict = None) -> List[Dict]:
        context = context or {}
        messages: List[Dict] = []

        static_parts = self._render_group("static", context)
        if static_parts:
            messages.append({
                "role": "system",
                "content": "\n\n".join(static_parts),
            })

        daily_parts = self._render_group("daily", context)
        if daily_parts:
            messages.append({
                "role": "user",
                "content": "\n".join(daily_parts),
            })

        session_messages = context.get("session_messages", [])
        messages.extend(session_messages)

        for piece in self._get_enabled_by_stability("session"):
            try:
                content = piece.render(context)
                if content and content.strip():
                    messages.append({
                        "role": piece.effective_role,
                        "content": content.strip(),
                    })
            except Exception as e:
                logger.warning("[PromptBuilder] 渲染 session piece %s 失败: %s", piece.name, e)

        for piece in self._get_enabled_by_stability("dynamic"):
            try:
                content = piece.render(context)
                if content and content.strip():
                    messages.append({
                        "role": piece.effective_role,
                        "content": content.strip(),
                    })
            except Exception as e:
                logger.warning("[PromptBuilder] 渲染 dynamic piece %s 失败: %s", piece.name, e)

        if messages:
            prefix_info = "; ".join(
                f"{i}:<{m.get('role', '?')}>" + (
                    f" ({m.get('content', '')[:50]}...)" if i < 3 else ""
                )
                for i, m in enumerate(messages[:3])
            )
            logger.debug("[PromptBuilder] 构建完成 | total=%d | prefix=[%s]",
                         len(messages), prefix_info)

        return messages

    def _render_group(self, stability: Stability, context: dict) -> List[str]:
        rendered = []
        for piece in self._get_enabled_by_stability(stability):
            try:
                content = piece.render(context)
                if content and content.strip():
                    if stability == "static":
                        rendered.append(content)
                    else:
                        rendered.append(content.strip())
            except Exception as e:
                logger.warning("[PromptBuilder] render %s 失败: %s", piece.name, e)
        return rendered

    def reset(self) -> None:
        self.clear_dynamic()
        for piece in self._pieces.values():
            piece.enabled = True

    def __repr__(self) -> str:
        counts = {}
        for p in self._pieces.values():
            counts[p.stability] = counts.get(p.stability, 0) + 1
        parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return f"PromptBuilder({parts})"

# -*- coding: utf-8 -*-
"""流式 thinking/正文 分离状态机"""

import json
import re
from typing import Dict, List, Optional

_THINK_PREFIXES = {
    "<", "<t", "<th", "<thi", "<thin", "<think",
    "</", "</t", "</th", "</thi", "</thin", "</think",
}

_JSON_FENCE = "```json"
_INLINE_OPEN = '{"reasoning'
# JSON 缓冲上限，防未闭合思考块导致内存暴涨
_JSON_BUFFER_MAX = 100000


def _think_fragment_tail(rest: str) -> str:
    for k in range(7, 0, -1):
        tail = rest[-k:]
        if tail in _THINK_PREFIXES:
            return tail
    return ""


def _fence_frag_tail(rest: str) -> str:
    """rest 尾部为 ```json 前缀（不含完整 ```json）的残片，取最长匹配。

    从长到短匹配：`前文 `` ` 应切出 frag="``" 而非 "`"，否则拼接后
    无法识别 ```json 围栏，导致 ```json 残片泄漏进正文。
    """
    for k in range(len(_JSON_FENCE) - 1, 0, -1):
        tail = rest[-k:]
        if tail.lower() == _JSON_FENCE[:k]:
            return tail
    return ""


def _inline_frag_tail(rest: str) -> str:
    """rest 尾部为 {"reasoning 前缀（不含完整）的残片，取最长匹配"""
    for k in range(len(_INLINE_OPEN) - 1, 0, -1):
        tail = rest[-k:]
        norm = re.sub(r"\s", "", tail)
        if tail[0] == "{" and norm == _INLINE_OPEN[:k]:
            return tail
    return ""


class StreamNormalizer:
    """将 run_stream 原始事件流规范化为 thinking/正文分离的统一事件流"""

    def __init__(self):
        self._thought_mode = False      # 是否在 <think> 块内
        self._think_buffer = ""         # <think> 内容累积
        self._think_frag = ""           # <think>/</think> 标签残片跨 chunk 缓存
        self._json_mode = False         # 是否在 ```json/内联 JSON 思考块内
        self._json_buffer = ""          # JSON 思考块原文累积
        self._json_fenced = False       # 是否 ```json 围栏包裹
        self._json_fence_opener = ""    # 围栏开头原文（如 ```json）
        self._fence_frag = ""           # ```json 开头残片跨 chunk 缓存
        self._inline_frag = ""          # {"reasoning 开头残片缓存
        self._last_early_reasoning: Optional[str] = None
        self._thinking_started: bool = False  # 是否已发出 thinking_start 启动信号

    def feed(self, raw_event: dict) -> List[dict]:
        """输入 run_stream 原始事件 dict，产出规范事件列表。

        - type=content_chunk / content: 状态机剥离 <think> 与 ```json 思考块，
          产出 thinking/content 事件
        - 其他类型: 原样透传（作为单个元素返回）
        """
        if raw_event.get("type") in ("content_chunk", "content"):
            content = raw_event.get("content", "")
            if isinstance(content, str):
                if not content:
                    return []
                return self._process_content(content)
            return [raw_event]
        return [raw_event]

    def finish(self) -> List[dict]:
        """流结束时冲刷残留状态。

        - <think> 残片 + 未闭合思考内容：收尾输出
        - ```json/内联 JSON 未闭合块：合法思考 → thinking，否则原样 content
        """
        results = []
        if self._think_frag:
            if self._thought_mode:
                self._think_buffer += self._think_frag
            self._think_frag = ""
        if self._thought_mode and self._think_buffer.strip():
            results.append({"type": "thinking", "content": self._think_buffer.strip()})
        self._think_buffer = ""
        self._thought_mode = False

        if self._fence_frag:
            results.append({"type": "content", "content": self._fence_frag})
            self._fence_frag = ""
        if self._inline_frag:
            results.append({"type": "content", "content": self._inline_frag})
            self._inline_frag = ""
        if self._json_mode:
            raw = self._json_buffer
            self._json_mode = False
            self._json_buffer = ""
            if raw:
                results.extend(self._emit_json_block(self._json_inner(raw), raw))
            self._json_fenced = False
            self._json_fence_opener = ""
        return results

    def _process_content(self, content: str) -> List[dict]:
        if self._fence_frag:
            content = self._fence_frag + content
            self._fence_frag = ""
        if self._inline_frag:
            content = self._inline_frag + content
            self._inline_frag = ""
        if self._think_frag:
            content = self._think_frag + content
            self._think_frag = ""
        results: List[dict] = []
        rest = content
        while rest:
            if self._thought_mode:
                close = re.search(r"</think>", rest)
                if close:
                    self._think_buffer += rest[:close.start()]
                    rest = rest[close.end():]
                    self._thought_mode = False
                    if self._think_buffer.strip():
                        results.append({"type": "thinking", "content": self._think_buffer.strip()})
                    self._think_buffer = ""
                    continue
                frag = _think_fragment_tail(rest)
                if frag:
                    keep = rest[:-len(frag)] if frag != rest else ""
                    self._think_buffer += keep
                    self._think_frag = frag
                    rest = ""
                    break
                self._think_buffer += rest
                rest = ""
            elif self._json_mode:
                results.extend(self._consume_json(rest))
                break
            else:
                kind, pos = self._find_trigger(rest)
                if kind is not None:
                    before = rest[:pos]
                    if before:
                        results.append({"type": "content", "content": before})
                    if kind == "think":
                        self._thought_mode = True
                        rest = rest[pos + len("<think>"):]
                        continue
                    self._enter_json(rest[pos:])
                    rest = ""
                    # 立即尝试闭合（同 chunk 内联 JSON/完整围栏块）
                    results.extend(self._consume_json(""))
                else:
                    frag = _think_fragment_tail(rest)
                    if frag:
                        keep = rest[:-len(frag)] if frag != rest else ""
                        if keep:
                            results.append({"type": "content", "content": keep})
                        self._think_frag = frag
                        rest = ""
                        break
                    frag = _fence_frag_tail(rest)
                    if frag:
                        keep = rest[:-len(frag)] if frag != rest else ""
                        if keep:
                            results.append({"type": "content", "content": keep})
                        self._fence_frag = frag
                        rest = ""
                        break
                    frag = _inline_frag_tail(rest)
                    if frag:
                        keep = rest[:-len(frag)] if frag != rest else ""
                        if keep:
                            results.append({"type": "content", "content": keep})
                        self._inline_frag = frag
                        rest = ""
                        break
                    if rest:
                        results.append({"type": "content", "content": rest})
                    rest = ""
        return results

    def _find_trigger(self, rest: str):
        """返回 (kind, pos)：rest 中最早出现的思考块起点（think/fence/inline）"""
        candidates = []
        m = re.search(r"<think>", rest)
        if m:
            candidates.append(("think", m.start()))
        m = re.search(r"```json", rest, re.IGNORECASE)
        if m:
            candidates.append(("fence", m.start()))
        m = re.search(r'\{\s*"reasoning', rest)
        if m:
            candidates.append(("inline", m.start()))
        if not candidates:
            return None, -1
        return min(candidates, key=lambda c: c[1])

    def _enter_json(self, rest: str):
        """进入 JSON 思考块累积态（rest 从触发点起）"""
        self._json_mode = True
        self._json_fenced = rest.lower().startswith(_JSON_FENCE)
        self._json_fence_opener = rest[:len(_JSON_FENCE)] if self._json_fenced else ""
        self._json_buffer = rest

    def _consume_json(self, rest: str) -> List[dict]:
        """在 json_mode 下累积 rest，直至闭合（围栏 ``` 或完整 JSON）"""
        self._json_buffer += rest
        raw = self._json_buffer
        results: List[dict] = []
        if self._json_fenced:
            close = raw.find("```", len(self._json_fence_opener))
            if close != -1:
                inner = raw[len(self._json_fence_opener):close]
                tail = raw[close + 3:]
                self._json_mode = False
                self._json_buffer = ""
                self._json_fenced = False
                self._json_fence_opener = ""
                self._last_early_reasoning = None
                self._thinking_started = False
                results.extend(self._emit_json_block(inner.strip(), raw[:close + 3]))
                if tail:
                    results.extend(self._process_content(tail))
                return results
        else:
            end = self._find_json_end(raw)
            if end != -1:
                inner = raw[:end]
                tail = raw[end:]
                self._json_mode = False
                self._json_buffer = ""
                self._last_early_reasoning = None
                self._thinking_started = False
                results.extend(self._emit_json_block(inner.strip(), inner))
                if tail:
                    results.extend(self._process_content(tail))
                return results
        if len(raw) > _JSON_BUFFER_MAX:
            inner = self._json_inner(raw)
            self._json_mode = False
            self._json_buffer = ""
            self._json_fenced = False
            self._json_fence_opener = ""
            results.extend(self._emit_json_block(inner, raw))
        else:
            early = self._early_reasoning_event()
            if early:
                results.append(early)
        return results

    def _early_reasoning_event(self) -> Optional[dict]:
        raw = self._json_buffer
        if not raw:
            return None
        if re.search(r'"reasoning"\s*:\s*"', raw) is None:
            return None
        if not getattr(self, "_thinking_started", False):
            self._thinking_started = True
            return {"type": "reasoning", "content": "", "start": True}
        m = re.search(r'"reasoning"\s*:\s*"([\s\S]*?)"(?:\s*[,}]|\s*$)', raw)
        if m:
            value = m.group(1)
            try:
                value = json.loads('"' + value + '"')
            except Exception:
                value = value.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
            if not value.strip():
                value = "\u2026"
        else:
            value = "\u2026"
        if value == getattr(self, "_last_early_reasoning", None):
            return None
        self._last_early_reasoning = value
        return {"type": "reasoning", "content": value, "final": False}

    def _json_inner(self, raw: str) -> str:
        """从围栏原文中提取 JSON 文本（未闭合收尾用）"""
        if self._json_fenced and self._json_fence_opener and raw.startswith(self._json_fence_opener):
            raw = raw[len(self._json_fence_opener):]
        if raw.endswith("```"):
            raw = raw[:-3]
        return raw.strip()

    def _emit_json_block(self, inner: str, raw: str) -> List[dict]:
        data = None
        try:
            data = json.loads(inner)
        except Exception:
            data = None
        if isinstance(data, dict):
            reasoning = data.get("reasoning")
            if isinstance(reasoning, str) and reasoning.strip():
                return [{"type": "reasoning", "content": reasoning.strip(), "final": True}]
        m = re.search(r'"reasoning"\s*:\s*"((?:\\.|[^"\\])*)"', raw, re.DOTALL)
        if m:
            value = m.group(1)
            try:
                value = json.loads('"' + value + '"')
            except Exception:
                value = value.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
            if value.strip():
                self._last_early_reasoning = value
                return [{"type": "reasoning", "content": value.strip(), "final": True}]
        cached = getattr(self, "_last_early_reasoning", None)
        if cached:
            return [{"type": "reasoning", "content": cached, "final": True}]
        return [{"type": "content", "content": raw}]

    @staticmethod
    def _find_json_end(raw: str) -> int:
        """返回首个完整 JSON 对象结束位置（含 }），未闭合返回 -1"""
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        return -1

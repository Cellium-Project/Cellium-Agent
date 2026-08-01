# -*- coding: utf-8 -*-
import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+")


def _is_cjk(seg: str) -> bool:
    return bool(seg) and "\u4e00" <= seg[0] <= "\u9fff"


class ChineseTokenizer:
    SYNONYMS = {
        "偏好": ["喜好", "爱好", "设置", "配置"],
        "喜好": ["偏好", "爱好", "设置"],
        "配置": ["设置", "偏好", "config"],
        "设置": ["配置", "偏好", "setting"],
        "错误": ["异常", "报错", "error", "失败"],
        "异常": ["错误", "报错", "error"],
        "目录": ["文件夹", "路径", "folder", "path"],
        "路径": ["目录", "文件夹", "path"],
        "文件": ["文档", "file"],
        "命令": ["指令", "command", "cmd"],
        "项目": ["工程", "project"],
        "代码": ["程序", "code"],
        "函数": ["方法", "function", "method"],
        "变量": ["参数", "variable"],
        "删除": ["移除", "remove", "del"],
        "修改": ["更改", "更新", "update", "change"],
        "查询": ["搜索", "查找", "search", "find"],
        "执行": ["运行", "run", "execute"],
        "安装": ["部署", "install", "setup"],
        "用户偏好": ["用户喜好", "用户设置", "偏好设置"],
        "配置文件": ["config 文件", "设置文件", "配置文档"],
        "错误信息": ["报错信息", "异常信息", "error 信息"],
        "命令行": ["终端", "cmd", "terminal"],
        "config": ["配置", "设置"],
        "setting": ["设置", "配置"],
        "error": ["错误", "异常"],
        "path": ["路径", "目录"],
        "command": ["命令", "指令"],
    }

    def tokenize(self, text: str) -> List[str]:
        if not text or not text.strip():
            return []
        tokens = []
        for m in _WORD_RE.finditer(text):
            seg = m.group()
            if _is_cjk(seg) and len(seg) >= 2:
                tokens.append(seg)
                if len(seg) >= 3:
                    # 生成相邻 2-gram，保证 FTS5 子串检索命中
                    tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
            else:
                tokens.append(seg)
        return tokens

    def tokenize_for_search(self, text: str) -> str:
        tokens = self.tokenize(text)
        return " ".join(tokens)

    def extract_keywords(self, text: str, top_k: int = 5) -> List[str]:
        if not text or not text.strip():
            return []
        tokens = self.tokenize(text)
        sorted_tokens = sorted(set(tokens), key=len, reverse=True)
        return sorted_tokens[:top_k]

    def expand_query(self, query: str) -> List[str]:
        variants = [query]

        tokens = self.tokenize(query)

        if len(tokens) > 1:
            variants.append(" ".join(tokens))

        keywords = self.extract_keywords(query, top_k=3)
        for kw in keywords:
            if kw not in variants and len(kw) >= 2:
                variants.append(kw)

        synonyms = self._expand_synonyms(tokens)
        for syn in synonyms:
            if syn not in variants:
                variants.append(syn)

        return variants[:8]

    def _expand_synonyms(self, tokens: List[str]) -> List[str]:
        expanded = []

        for token in tokens:
            if token in self.SYNONYMS:
                for syn in self.SYNONYMS[token]:
                    if syn != token and syn not in expanded:
                        expanded.append(syn)

        return expanded


_tokenizer: Optional[ChineseTokenizer] = None


def get_tokenizer() -> ChineseTokenizer:
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = ChineseTokenizer()
    return _tokenizer

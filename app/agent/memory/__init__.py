# -*- coding: utf-8 -*-
"""app.agent.memory - 记忆系统模块"""

import importlib

_LAZY_EXPORTS = {
    "ThreeLayerMemory": "app.agent.memory.three_layer",
    "FTS5MemorySearcher": "app.agent.memory.fts5_searcher",
    "ArchiveStore": "app.agent.memory.archive_store",
    "KnowledgeExtractor": "app.agent.memory.knowledge_extractor",
    "MemoryRepository": "app.agent.memory.repository",
    "ChineseTokenizer": "app.agent.memory.chinese_tokenizer",
    "get_tokenizer": "app.agent.memory.chinese_tokenizer",
}

__all__ = list(_LAZY_EXPORTS.keys())


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_EXPORTS.keys()))

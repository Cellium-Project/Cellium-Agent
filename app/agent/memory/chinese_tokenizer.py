# -*- coding: utf-8 -*-
"""
中文分词器 - 支持 jieba 和简单分词两种模式

用于：
1. 存入 FTS5 前对中文内容分词（提升检索命中率）
2. 查询时对用户输入分词（扩大召回范围）
"""

import re
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# 尝试导入 jieba，失败则降级为简单分词
_jieba_available = False
try:
    import jieba
    import jieba.analyse
    _jieba_available = True
    logger.info("[ChineseTokenizer] jieba 已加载")
except ImportError:
    logger.warning("[ChineseTokenizer] jieba 未安装，使用简单分词（性能较低）")


class ChineseTokenizer:
    """中文分词器 - 自动选择最佳分词策略"""

    # 同义词映射表（用于查询扩展）
    SYNONYMS = {
        # 通用近义词
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
        # 复合词映射
        "用户偏好": ["用户喜好", "用户设置", "偏好设置"],
        "配置文件": ["config 文件", "设置文件", "配置文档"],
        "错误信息": ["报错信息", "异常信息", "error 信息"],
        "命令行": ["终端", "cmd", "terminal"],
        # 英文 -> 中文映射
        "config": ["配置", "设置"],
        "setting": ["设置", "配置"],
        "error": ["错误", "异常"],
        "path": ["路径", "目录"],
        "command": ["命令", "指令"],
    }

    def __init__(self, use_jieba: bool = True):
        self.use_jieba = use_jieba and _jieba_available
        self._init_custom_dict()

    def _init_custom_dict(self):
        """初始化自定义词典（常见技术术语）"""
        if not self.use_jieba:
            return

        custom_words = [
            # 编程相关
            "配置文件", "环境变量", "命令行", "源代码", "目标文件",
            "依赖包", "虚拟环境", "工作目录", "项目路径", "执行命令",
            "错误信息", "日志文件", "调试模式", "断点调试", "堆栈跟踪",
            # Windows 相关
            "注册表", "系统服务", "任务管理器", "控制面板", "资源管理器",
            "快捷方式", "批处理", "PowerShell", "管理员权限",
            # 通用
            "用户偏好", "自动启动", "后台运行", "定时任务", "文件关联",
        ]
        for word in custom_words:
            jieba.add_word(word)

    def tokenize(self, text: str) -> List[str]:
        """
        分词主入口

        Args:
            text: 待分词文本

        Returns:
            分词后的 token 列表
        """
        if not text or not text.strip():
            return []

        if self.use_jieba:
            return self._jieba_tokenize(text)
        else:
            return self._simple_tokenize(text)

    def _jieba_tokenize(self, text: str) -> List[str]:
        """jieba 精确模式分词"""
        # 精确模式，适合检索
        tokens = list(jieba.cut(text, cut_all=False))
        return [t.strip() for t in tokens if t.strip()]

    def _simple_tokenize(self, text: str) -> List[str]:
        """
        简单分词（无 jieba 时的降级方案）
        规则：
        1. 中文字符单字切分
        2. 英文单词保持完整
        3. 数字保持完整
        """
        tokens = []
        current_token = ""
        current_type = None  # 'cn', 'en', 'num', 'other'

        for char in text:
            char_type = self._get_char_type(char)

            if char_type == current_type:
                current_token += char
            else:
                if current_token:
                    tokens.append(current_token)
                current_token = char
                current_type = char_type

        if current_token:
            tokens.append(current_token)

        return [t.strip() for t in tokens if t.strip() and not self._is_punctuation(t)]

    @staticmethod
    def _get_char_type(char: str) -> str:
        """判断字符类型"""
        if '\u4e00' <= char <= '\u9fff':
            return 'cn'
        elif char.isalpha():
            return 'en'
        elif char.isdigit():
            return 'num'
        else:
            return 'other'

    @staticmethod
    def _is_punctuation(text: str) -> bool:
        """判断是否为标点符号"""
        puncts = set('，。！？、；：""''（）【】《》\n\r\t ')
        return all(c in puncts for c in text)

    def tokenize_for_search(self, text: str) -> str:
        """
        为 FTS5 搜索准备分词结果（空格连接）

        Args:
            text: 原始文本

        Returns:
            空格分隔的 token 字符串，适合 FTS5 MATCH 查询
        """
        tokens = self.tokenize(text)
        return " ".join(tokens)

    def extract_keywords(self, text: str, top_k: int = 5) -> List[str]:
        """
        提取关键词（用于自动打标签）

        Args:
            text: 文本内容
            top_k: 返回前 K 个关键词

        Returns:
            关键词列表
        """
        if not text or not text.strip():
            return []

        if self.use_jieba:
            # 使用 TF-IDF 提取关键词
            keywords = jieba.analyse.extract_tags(text, topK=top_k)
            return keywords
        else:
            # 降级：返回长度较长的 token 作为关键词
            tokens = self.tokenize(text)
            # 优先返回较长的 token（更有语义意义）
            sorted_tokens = sorted(set(tokens), key=len, reverse=True)
            return sorted_tokens[:top_k]

    def expand_query(self, query: str) -> List[str]:
        """
        查询扩展 - 生成多个搜索变体以提升召回率

        包括：
        1. 原始查询
        2. 分词后的查询
        3. 关键词扩展
        4. ★ 同义词扩展

        Args:
            query: 原始查询

        Returns:
            查询变体列表（包含原始查询）
        """
        variants = [query]

        tokens = self.tokenize(query)

        # 变体1：分词后用空格连接（FTS5 会自动 OR）
        if len(tokens) > 1:
            variants.append(" ".join(tokens))

        # 变体2：提取关键词单独搜索
        keywords = self.extract_keywords(query, top_k=3)
        for kw in keywords:
            if kw not in variants and len(kw) >= 2:
                variants.append(kw)

        # ★ 变体3：同义词扩展
        synonyms = self._expand_synonyms(tokens)
        for syn in synonyms:
            if syn not in variants:
                variants.append(syn)

        return variants[:8]  # 最多 8 个变体

    def _expand_synonyms(self, tokens: List[str]) -> List[str]:
        """
        同义词扩展：为每个 token 查找同义词

        Args:
            tokens: 分词后的 token 列表

        Returns:
            同义词查询变体列表
        """
        expanded = []

        for token in tokens:
            if token in self.SYNONYMS:
                # 找到同义词，加入扩展列表
                for syn in self.SYNONYMS[token]:
                    if syn != token and syn not in expanded:
                        expanded.append(syn)

        return expanded


# 全局单例（懒加载）
_tokenizer: Optional[ChineseTokenizer] = None


def get_tokenizer() -> ChineseTokenizer:
    """获取全局分词器实例"""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = ChineseTokenizer()
    return _tokenizer

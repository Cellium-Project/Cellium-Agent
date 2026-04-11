# -*- coding: utf-8 -*-
"""
Policy 模板定义

Policy = Heuristic 参数模板
每种 Policy 定义一组 Heuristic 引擎的参数配置

配置来源优先级：
  1. config/agent/learning.yaml → learning.policies
  2. 以下 _DEFAULT_TEMPLATES 作为后备默认值
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 默认 Policy 模板（后备值）
# ============================================================

_DEFAULT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "default": {
        # 默认策略：平衡效率与宽容度
        "stuck_iterations": 3,           # 无进展迭代阈值
        "repetition_threshold": 3,        # 重复调用阈值
        "progress_trend_threshold": -0.3, # 趋势恶化阈值
        "confirm_stop_threshold": 0.9,    # STOP 确认置信度
    },
    "efficient": {
        # 高效策略：更快终止，适合简单任务
        "stuck_iterations": 2,
        "repetition_threshold": 2,
        "progress_trend_threshold": -0.2,
        "confirm_stop_threshold": 0.8,
    },
    "aggressive": {
        # 激进策略：更宽容，适合复杂任务
        "stuck_iterations": 5,
        "repetition_threshold": 5,
        "progress_trend_threshold": -0.4,
        "confirm_stop_threshold": 0.95,
    },
}

# 缓存从配置文件加载的模板
_cached_templates: Optional[Dict[str, Dict[str, Any]]] = None


def _load_templates_from_config() -> Dict[str, Dict[str, Any]]:
    """
    从配置文件加载 Policy 模板

    Returns:
        Dict: 合并后的 Policy 模板（配置文件 + 默认值）
    """
    global _cached_templates

    if _cached_templates is not None:
        return _cached_templates

    templates = _DEFAULT_TEMPLATES.copy()

    try:
        from app.core.util.agent_config import get_config
        config = get_config()
        config_policies = config.get("learning.policies", {})

        if config_policies and isinstance(config_policies, dict):
            # 配置文件中的值覆盖默认值
            for name, params in config_policies.items():
                if isinstance(params, dict):
                    if name in templates:
                        # 合并：配置值覆盖默认值
                        templates[name] = {**templates[name], **params}
                    else:
                        # 新增 Policy
                        templates[name] = params.copy()
            logger.info(
                "[Policy] 从配置加载 %d 个 Policy 模板 | names=%s",
                len(config_policies),
                list(config_policies.keys()),
            )
    except Exception as e:
        logger.debug("[Policy] 配置加载失败，使用默认值: %s", e)

    _cached_templates = templates
    return templates


def get_policy_templates() -> Dict[str, Dict[str, Any]]:
    """获取所有 Policy 模板（从配置文件加载）"""
    return _load_templates_from_config()


def reload_templates():
    """强制重新加载配置（配置热更新时调用）"""
    global _cached_templates
    _cached_templates = None
    logger.info("[Policy] 已清除缓存，下次调用将重新加载配置")


# 兼容旧代码：POLICY_TEMPLATES 作为属性访问
class _PolicyTemplatesProxy:
    """延迟加载代理，兼容 POLICY_TEMPLATES 字典访问"""

    def __getitem__(self, key):
        return get_policy_templates()[key]

    def get(self, key, default=None):
        return get_policy_templates().get(key, default)

    def keys(self):
        return get_policy_templates().keys()

    def values(self):
        return get_policy_templates().values()

    def items(self):
        return get_policy_templates().items()

    def __contains__(self, key):
        return key in get_policy_templates()

    def __iter__(self):
        return iter(get_policy_templates())

    def __len__(self):
        return len(get_policy_templates())


POLICY_TEMPLATES = _PolicyTemplatesProxy()


def get_policy_params(policy_name: str) -> Dict[str, Any]:
    """获取指定 Policy 的参数"""
    templates = get_policy_templates()
    return templates.get(policy_name, templates.get("default", _DEFAULT_TEMPLATES["default"])).copy()


def list_policies() -> list:
    """列出所有 Policy 名称"""
    return list(get_policy_templates().keys())

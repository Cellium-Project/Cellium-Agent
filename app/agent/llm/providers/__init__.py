# -*- coding: utf-8 -*-
"""厂商适配器注册表 - 自动发现所有厂商"""
import importlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseProvider

logger = logging.getLogger(__name__)

_PROVIDERS: Dict[str, BaseProvider] = {}


def _discover_providers():
    package_dir = Path(__file__).parent
    for item in package_dir.iterdir():
        if item.is_dir() and not item.name.startswith('_'):
            init_file = item / "__init__.py"
            if not init_file.exists():
                continue
            try:
                module = importlib.import_module(f".{item.name}", package=__package__)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseProvider)
                            and attr is not BaseProvider):
                        provider = attr()
                        _PROVIDERS[provider.get_provider_id()] = provider
                        logger.info("[Providers] 已注册: %s", provider.get_provider_name())
            except Exception as e:
                logger.warning("[Providers] 加载 %s 失败: %s", item.name, e)


_discover_providers()


def get_provider(provider_id: str) -> Optional[BaseProvider]:
    return _PROVIDERS.get(provider_id)


def list_providers() -> List[BaseProvider]:
    return list(_PROVIDERS.values())


def detect_provider(base_url: str) -> Optional[BaseProvider]:
    if not base_url:
        return None
    base_url_lower = base_url.lower()
    for provider in _PROVIDERS.values():
        if provider.get_base_url().lower() in base_url_lower:
            return provider
    return None


def get_provider_info(provider_id: str) -> Optional[Dict]:
    p = _PROVIDERS.get(provider_id)
    if not p:
        return None
    return {
        "id": p.get_provider_id(),
        "name": p.get_provider_name(),
        "base_url": p.get_base_url(),
        "models_endpoint": p.get_models_endpoint(),
        "chat_endpoint": p.get_chat_endpoint(),
        "reasoning_param": p.get_reasoning_param_name(),
    }


def list_provider_infos() -> List[Dict]:
    return [
        {
            "id": p.get_provider_id(),
            "name": p.get_provider_name(),
            "base_url": p.get_base_url(),
            "models_endpoint": p.get_models_endpoint(),
            "chat_endpoint": p.get_chat_endpoint(),
            "reasoning_param": p.get_reasoning_param_name(),
        }
        for p in _PROVIDERS.values()
    ]

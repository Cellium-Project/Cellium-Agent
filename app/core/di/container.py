# -*- coding: utf-8 -*-
"""
依赖注入容器
提供服务定位和依赖注入功能
"""

import logging
import threading
from abc import ABCMeta
from typing import Dict, Type, Any, Optional, Callable

logger = logging.getLogger(__name__)


class DIContainer:
    """依赖注入容器"""
    
    _instance: Optional['DIContainer'] = None
    _lock: threading.Lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._services: Dict[str, Any] = {}
                    cls._instance._singletons: Dict[str, Any] = {}
        return cls._instance
    
    def register(self, service_type: Type, instance: Any, singleton: bool = True):
        """注册服务
        
        Args:
            service_type: 服务类型（类或协议）
            instance: 预配置实例
            singleton: 是否单例模式
        """
        key = f"{service_type.__module__}.{service_type.__name__}"
        
        self._services[key] = (instance, singleton)
        if singleton:
            self._singletons[key] = instance
        logger.info(f"已注册服务: {service_type.__name__} (singleton={singleton})")
    
    def register_factory(self, service_type: Type, factory: Callable):
        """注册工厂函数
        
        Args:
            service_type: 服务类型
            factory: 创建实例的工厂函数
        """
        key = f"{service_type.__module__}.{service_type.__name__}"
        self._services[key] = (factory, False)
        logger.info(f"[DI] 已注册工厂: {service_type.__name__}")
    
    def resolve(self, service_type: Type) -> Any:
        """解析服务实例
        
        Args:
            service_type: 服务类型
            
        Returns:
            Any: 服务实例
        """
        key = f"{service_type.__module__}.{service_type.__name__}"
        
        if key not in self._services:
            raise ValueError(f"[DI] 服务未注册: {service_type.__name__}")
        
        instance_or_factory, is_singleton = self._services[key]
        
        if is_singleton and key in self._singletons:
            return self._singletons[key]
        
        if callable(instance_or_factory) and not isinstance(instance_or_factory, type):
            instance = instance_or_factory()
        else:
            instance = instance_or_factory
        
        if is_singleton:
            self._singletons[key] = instance
        
        return instance
    
    def clear(self):
        """清空所有注册"""
        self._services.clear()
        self._singletons.clear()
        logger.info("[DI] 容器已清空")
    
    def has(self, service_type: Type) -> bool:
        """检查服务是否已注册
        
        Args:
            service_type: 服务类型
            
        Returns:
            bool: 是否已注册
        """
        key = f"{service_type.__module__}.{service_type.__name__}"
        return key in self._services


# 全局容器实例
_container: Optional[DIContainer] = None


def get_container() -> DIContainer:
    """获取全局容器实例"""
    global _container
    if _container is None:
        _container = DIContainer()
    return _container


def inject(service_type: Type):
    """依赖注入装饰器"""
    
    def decorator(prop_func):
        @property
        def wrapper(self):
            container = get_container()
            return container.resolve(service_type)
        return wrapper
    return decorator


class AutoInjectMeta(ABCMeta):
    """自动注入元类"""
    
    def __new__(cls, name, bases, namespace):
        new_namespace = {}
        for key, value in namespace.items():
            if isinstance(value, _InjectMarker):
                service_type = value.service_type
                new_namespace[key] = property(lambda self, st=service_type: get_container().resolve(st))
            else:
                new_namespace[key] = value
        
        return super().__new__(cls, name, bases, new_namespace)


class _InjectMarker:
    """注入标记类"""
    
    def __init__(self, service_type: Type):
        self.service_type = service_type


def injected(service_type: Type) -> _InjectMarker:
    """标记属性需要注入"""

    return _InjectMarker(service_type)


def setup_di_container():
    """初始化依赖注入容器"""
    from app.core.bus.event_bus import EventBus, get_event_bus
    from app.core.util.mp_manager import MultiprocessManager, get_multiprocess_manager
    
    container = get_container()
    
    container.register(EventBus, get_event_bus(), singleton=True)
    container.register(MultiprocessManager, get_multiprocess_manager(), singleton=True)
    
    logger.info("依赖注入容器初始化完成")

# -*- coding: utf-8 -*-
"""
多进程管理器
"""

from __future__ import annotations

import atexit
import logging
import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor, Future
from functools import wraps
from typing import Callable, Any, Optional
import traceback

logger = logging.getLogger(__name__)

_worker_initializers: list[Callable] = []
_worker_initialized = False


def _worker_init():
    """工作进程初始化（在每个子进程启动时调用）"""
    global _worker_initialized
    if _worker_initialized:
        return
    
    logger.info("[MP] 工作进程初始化开始")
    
    for init_func in _worker_initializers:
        try:
            init_func()
            logger.debug(f"[MP] 初始化函数 {init_func.__name__} 执行成功")
        except Exception as e:
            logger.error(f"[MP] 初始化函数 {init_func.__name__} 执行失败: {e}")
    
    _worker_initialized = True
    logger.info("[MP] 工作进程初始化完成")


class MultiprocessManager:
    """多进程管理器（单例模式）"""
    
    _instance: Optional['MultiprocessManager'] = None
    _executor: Optional[ProcessPoolExecutor] = None
    _enabled: bool = True
    
    def __new__(cls) -> 'MultiprocessManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        atexit.register(self.shutdown)
        logger.info("[MP] 多进程管理器已初始化")
    
    @property
    def executor(self) -> Optional[ProcessPoolExecutor]:
        """获取进程池（懒加载）"""
        if not self._enabled:
            return None
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=multiprocessing.cpu_count(),
                initializer=_worker_init
            )
            logger.info(f"进程池已启动，使用 {multiprocessing.cpu_count()} 个工作进程")
        return self._executor
    
    def is_enabled(self) -> bool:
        """是否启用多进程"""
        return self._enabled
    
    def set_enabled(self, enabled: bool):
        """设置是否启用多进程"""
        self._enabled = enabled
        if not enabled:
            self.shutdown()
    
    def submit(self, func: Callable, *args, **kwargs) -> Any:
        """提交任务（同步）"""
        if not self._enabled:
            return func(*args, **kwargs)
        
        executor = self.executor
        try:
            future: Future = executor.submit(func, *args, **kwargs)
            return future.result()
        except Exception:
            raise
    
    def submit_async(self, func: Callable, *args, **kwargs) -> Future:
        """提交任务（异步，返回 Future）"""
        if not self._enabled or self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=1)
            return executor.submit(func, *args, **kwargs)
        
        return self._executor.submit(func, *args, **kwargs)
    
    def map(self, func: Callable, args_list: list) -> list:
        """并行执行多个任务（同步）"""
        if not self._enabled or self._executor is None:
            return [func(*args) for args in args_list]
        
        return list(self._executor.map(func, args_list))
    
    def map_async(self, func: Callable, args_list: list):
        """并行执行多个任务（异步）"""
        if not self._enabled or self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            executor = ThreadPoolExecutor(max_workers=len(args_list))
            return [executor.submit(func, *args) for args in args_list]
        
        return [self._executor.submit(func, *args) for args in args_list]
    
    def shutdown(self, wait: bool = True):
        """关闭进程池"""
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None
            logger.info("进程池已关闭")
    
    def register_initializer(self, init_func: Callable):
        """注册工作进程初始化函数
        
        Args:
            init_func: 初始化函数，将在每个子进程启动时调用
        """
        if init_func not in _worker_initializers:
            _worker_initializers.append(init_func)
            logger.info(f"[MP] 已注册初始化函数: {init_func.__name__}")
    
    def clear_initializers(self):
        """清除所有工作进程初始化函数"""
        _worker_initializers.clear()
        logger.info("[MP] 已清除所有初始化函数")


_manager = MultiprocessManager()


def run_in_process(func: Callable) -> Callable:
    """装饰器：在独立进程中执行函数"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        return _manager.submit(func, *args, **kwargs)
    return wrapper


def run_in_process_async(func: Callable) -> Callable:
    """装饰器：在独立进程中异步执行函数"""
    import asyncio
    from functools import wraps
    
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if not _manager._enabled or _manager._executor is None:
            return await func(*args, **kwargs)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _manager.executor, 
            lambda: func(*args, **kwargs)
        )
    
    return wrapper


def get_multiprocess_manager() -> MultiprocessManager:
    """获取全局多进程管理器"""
    return _manager


def worker_initializer(init_func: Callable) -> Callable:
    """装饰器：注册工作进程初始化函数 """
    _manager.register_initializer(init_func)
    return init_func

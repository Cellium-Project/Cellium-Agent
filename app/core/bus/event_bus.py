# -*- coding: utf-8 -*-
"""
事件总线模块
提供发布-订阅模式的事件管理
支持同步和异步事件处理
支持命名空间、通配符、优先级、一次性事件
"""

import asyncio
import inspect
import logging
import fnmatch
import re
import threading
from typing import Callable, Dict, List, Any, Optional, Type, Set, Tuple
from functools import wraps
from .events import EventType
from .event_models import BaseEvent, AlertEvent, JsQueryEvent, FadeOutEvent, NavigationEvent, ButtonClickEvent, CalcResultEvent, SystemCommandEvent

logger = logging.getLogger(__name__)

# 模块级全局变量 - 添加线程锁保护
_EVENT_HANDLERS_REGISTRY: Dict[str, Set[Tuple[Callable, int, Tuple]]] = {}
_ONCE_HANDLERS_REGISTRY: Dict[str, Set[Callable]] = {}
_WILDCARD_HANDLERS: Dict[str, List[Tuple[Callable, int]]] = {}
_WILDCARD_HANDLER_FUNCTIONS: List[Callable] = []
_EVENT_NAMESPACE: str = ""
_REGISTRY_LOCK = threading.Lock()  # 保护全局注册表的锁


class EventPriority:
    LOWEST = 0
    LOW = 100
    NORMAL = 500
    HIGH = 1000
    HIGHEST = 10000


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._subscriber_priority: Dict[str, Dict[int, List[Callable]]] = {}
        self._event_classes: Dict[str, Type[BaseEvent]] = {}
        self._once_subscribers: Dict[str, Set[Callable]] = {}
        
        self._event_classes[str(EventType.ALERT)] = AlertEvent
        self._event_classes[str(EventType.JSQUERY)] = JsQueryEvent
        self._event_classes[str(EventType.FADE_OUT)] = FadeOutEvent
        self._event_classes[str(EventType.NAVIGATION)] = NavigationEvent
        self._event_classes[str(EventType.BUTTON_CLICK)] = ButtonClickEvent
        self._event_classes[str(EventType.CALC_RESULT)] = CalcResultEvent
        self._event_classes[str(EventType.SYSTEM_COMMAND)] = SystemCommandEvent
    
    def register_event_class(self, event_type: EventType, event_class: Type[BaseEvent]):
        self._event_classes[str(event_type)] = event_class
        logger.info(f"已注册事件类: {event_type} -> {event_class.__name__}")
    
    def subscribe(self, event_type: EventType, handler: Callable, priority: int = EventPriority.NORMAL):
        event_name = str(event_type)
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []
            self._subscriber_priority[event_name] = {}
        
        if priority not in self._subscriber_priority[event_name]:
            self._subscriber_priority[event_name][priority] = []
        
        if handler not in self._subscriber_priority[event_name][priority]:
            self._subscriber_priority[event_name][priority].append(handler)
            
        if handler not in self._subscribers[event_name]:
            self._subscribers[event_name].append(handler)

        logger.debug(f"[EVENT] 已订阅事件: {event_name} (优先级: {priority}) -> {handler.__name__}")

    def subscribe_wildcard(self, handler: Callable, priority: int = EventPriority.NORMAL):
        pattern = "*"
        with _REGISTRY_LOCK:
            if pattern not in _WILDCARD_HANDLERS:
                _WILDCARD_HANDLERS[pattern] = []

            if (handler, priority) not in [(h, p) for h, p in _WILDCARD_HANDLERS[pattern]]:
                _WILDCARD_HANDLERS[pattern].append((handler, priority))
                _WILDCARD_HANDLERS[pattern].sort(key=lambda x: x[1], reverse=True)
        logger.debug(f"[EVENT] 已订阅通配符事件 (优先级: {priority}) -> {handler.__name__}")

    def subscribe_pattern(self, pattern: str, handler: Callable, priority: int = EventPriority.NORMAL):
        with _REGISTRY_LOCK:
            if pattern not in _WILDCARD_HANDLERS:
                _WILDCARD_HANDLERS[pattern] = []

            if (handler, priority) not in [(h, p) for h, p in _WILDCARD_HANDLERS[pattern]]:
                _WILDCARD_HANDLERS[pattern].append((handler, priority))
                _WILDCARD_HANDLERS[pattern].sort(key=lambda x: x[1], reverse=True)
        logger.debug(f"[EVENT] 已订阅模式事件: {pattern} (优先级: {priority}) -> {handler.__name__}")
    
    def subscribe_once(self, event_type: EventType, handler: Callable):
        event_name = str(event_type)
        if event_name not in self._once_subscribers:
            self._once_subscribers[event_name] = set()
        self._once_subscribers[event_name].add(handler)
        logger.debug(f"[EVENT] 已订阅一次性事件: {event_name} -> {handler.__name__}")
    
    def unsubscribe(self, event_type: EventType, handler: Optional[Callable] = None):
        event_name = str(event_type)
        if handler is None:
            self._subscribers.pop(event_name, None)
            self._subscriber_priority.pop(event_name, None)
            self._once_subscribers.pop(event_name, None)
            logger.debug(f"已取消订阅所有事件处理器: {event_type}")
        else:
            if event_name in self._subscribers:
                self._subscribers[event_name] = [h for h in self._subscribers[event_name] if h != handler]
            if event_name in self._once_subscribers:
                self._once_subscribers[event_name].discard(handler)
            logger.debug(f"已取消订阅事件: {event_type}")
    
    def _match_pattern(self, event_name: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        if fnmatch.fnmatch(event_name, pattern):
            return True
        regex_pattern = pattern.replace(".", "\\.").replace("*", ".*").replace("?", ".")
        if re.match(f"^{regex_pattern}$", event_name):
            return True
        return False
    
    def _get_sorted_handlers(self, event_name: str) -> List[Callable]:
        handlers = []
        priorities = sorted(self._subscriber_priority.get(event_name, {}).keys(), reverse=True)
        for priority in priorities:
            handlers.extend(self._subscriber_priority[event_name][priority])
        handlers.extend([h for h in self._subscribers.get(event_name, []) if h not in handlers])
        return handlers
    
    def _invoke_handler(self, handler: Callable, event_name: str, event: Any = None, *args, **kwargs) -> Any:
        """统一调用事件处理器，处理同步和异步函数"""
        if inspect.iscoroutinefunction(handler):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    return asyncio.create_task(handler(event or event_name, *args, **kwargs))
                else:
                    return loop.run_until_complete(handler(event or event_name, *args, **kwargs))
            except RuntimeError:
                logger.warning(f"异步处理器需要事件循环: {handler.__name__}")
        else:
            if event:
                return handler(event)
            else:
                return handler(event_name, *args, **kwargs)
    
    def _create_event(self, event_name: str, args: tuple, kwargs: dict) -> Any:
        """统一创建事件对象"""
        if event_name in self._event_classes:
            try:
                event_class = self._event_classes[event_name]
                if args and isinstance(args[0], BaseEvent):
                    return args[0]
                else:
                    return event_class(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[WARNING] 创建事件对象失败: {e}")
        return None
    
    def publish(self, event_type: EventType, *args, **kwargs):
        event_name = str(event_type)
        
        event = self._create_event(event_name, args, kwargs)
        result = None
        
        for handler in self._get_sorted_handlers(event_name):
            try:
                result = self._invoke_handler(handler, event_name, event, *args, **kwargs)
            except Exception as e:
                logger.error(f"[ERROR] 事件处理器错误 [{event_type}]: {e}")
                import traceback
                logger.error(f"[ERROR] 堆栈跟踪: {traceback.format_exc()}")
        
        for pattern, handlers in _WILDCARD_HANDLERS.items():
            if self._match_pattern(event_name, pattern):
                for handler, priority in handlers:
                    try:
                        logger.debug(f"[TRACE-WILDCARD] 调用通配符处理器: handler={handler.__name__}, pattern={pattern}")
                        result = self._invoke_handler(handler, event_name, event, **kwargs)
                    except Exception as e:
                        logger.error(f"[ERROR] 通配符事件处理器错误 [{pattern}]: {e}")
                        import traceback
                        logger.error(f"[ERROR] 堆栈跟踪: {traceback.format_exc()}")
        
        if event_name in self._once_subscribers:
            handlers_to_remove = list(self._once_subscribers[event_name])
            for handler in handlers_to_remove:
                try:
                    logger.debug(f"[TRACE-ONCE] 调用一次性处理器: handler={handler.__name__}")
                    result = self._invoke_handler(handler, event_name, event, *args, **kwargs)
                except Exception as e:
                    logger.error(f"[ERROR] 一次性事件处理器错误 [{event_type}]: {e}")
                    import traceback
                    logger.error(f"[ERROR] 堆栈跟踪: {traceback.format_exc()}")
            self._once_subscribers[event_name].clear()
        
        return result
    
    async def _invoke_handler_async(self, handler: Callable, event_name: str, event: Any = None, *args, **kwargs) -> Any:
        """统一异步调用事件处理器"""
        if inspect.iscoroutinefunction(handler):
            if event:
                return await handler(event)
            else:
                return await handler(event_name, *args, **kwargs)
        else:
            if event:
                return handler(event)
            else:
                return handler(event_name, *args, **kwargs)
    
    async def publish_async(self, event_type: EventType, *args, **kwargs):
        event_name = str(event_type)
        
        event = self._create_event(event_name, args, kwargs)
        result = None
        
        for handler in self._get_sorted_handlers(event_name):
            try:
                result = await self._invoke_handler_async(handler, event_name, event, *args, **kwargs)
            except Exception as e:
                logger.error(f"[ERROR] 事件处理器错误 [{event_type}]: {e}")
        
        for pattern, handlers in _WILDCARD_HANDLERS.items():
            if self._match_pattern(event_name, pattern):
                for handler, priority in handlers:
                    try:
                        result = await self._invoke_handler_async(handler, event_name, event, **kwargs)
                    except Exception as e:
                        logger.error(f"[ERROR] 通配符事件处理器错误 [{pattern}]: {e}")
        
        if event_name in self._once_subscribers:
            handlers_to_remove = list(self._once_subscribers[event_name])
            for handler in handlers_to_remove:
                try:
                    result = await self._invoke_handler_async(handler, event_name, event, *args, **kwargs)
                except Exception as e:
                    logger.error(f"[ERROR] 一次性事件处理器错误 [{event_type}]: {e}")
            self._once_subscribers[event_name].clear()
        
        return result
    
    def has_subscribers(self, event_type: EventType) -> bool:
        event_name = str(event_type)
        return len(self._subscribers.get(event_name, [])) > 0
    
    def get_matching_subscribers(self, event_name: str) -> List[Callable]:
        handlers = self._get_sorted_handlers(event_name)
        for handler, pattern, _ in _WILDCARD_HANDLERS:
            if self._match_pattern(event_name, pattern) and handler not in handlers:
                handlers.append(handler)
        return handlers
    
    def clear(self, namespace: Optional[str] = None):
        if namespace:
            pattern = f"{namespace}.*"
            keys_to_remove = [k for k in self._subscribers.keys() if fnmatch.fnmatch(k, pattern)]
            for key in keys_to_remove:
                self._subscribers.pop(key, None)
                self._subscriber_priority.pop(key, None)
                self._once_subscribers.pop(key, None)
            with _REGISTRY_LOCK:
                _WILDCARD_HANDLERS.clear()
                _WILDCARD_HANDLER_FUNCTIONS.clear()
            logger.info(f"已清空命名空间 '{namespace}' 的所有事件订阅")
        else:
            self._subscribers.clear()
            self._subscriber_priority.clear()
            self._once_subscribers.clear()
            with _REGISTRY_LOCK:
                _WILDCARD_HANDLERS.clear()
                _WILDCARD_HANDLER_FUNCTIONS.clear()
            logger.info("已清空所有事件订阅")
    
    def get_subscribers_count(self, event_type: EventType) -> int:
        event_name = str(event_type)
        return len(self._subscribers.get(event_name, []))


def set_event_namespace(namespace: str):
    global _EVENT_NAMESPACE
    _EVENT_NAMESPACE = namespace
    logger.info(f"事件命名空间已设置为: {namespace}")


def get_event_namespace() -> str:
    return _EVENT_NAMESPACE


def event(event_type: str, priority: int = EventPriority.NORMAL):
    def decorator(func):
        @wraps(func)
        def wrapper(event_name, *args, **kwargs):
            return func(event_name, *args, **kwargs)

        full_event_type = event_type
        if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
            full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"

        handler_key = (func.__qualname__, func.__module__)
        with _REGISTRY_LOCK:
            if full_event_type not in _EVENT_HANDLERS_REGISTRY:
                _EVENT_HANDLERS_REGISTRY[full_event_type] = set()
            _EVENT_HANDLERS_REGISTRY[full_event_type].add((wrapper, priority, handler_key))
        logger.debug(f"[EVENT] 已注册事件处理器: {full_event_type} (优先级: {priority}) -> {func.__name__}")
        return wrapper
    return decorator


def event_once(event_type: str, priority: int = EventPriority.NORMAL):
    def decorator(func):
        @wraps(func)
        def wrapper(event_name, *args, **kwargs):
            return func(event_name, *args, **kwargs)

        full_event_type = event_type
        if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
            full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"

        with _REGISTRY_LOCK:
            if full_event_type not in _ONCE_HANDLERS_REGISTRY:
                _ONCE_HANDLERS_REGISTRY[full_event_type] = set()
            _ONCE_HANDLERS_REGISTRY[full_event_type].add((wrapper, priority))
        logger.debug(f"[EVENT] 已注册一次性事件处理器: {full_event_type} -> {func.__name__}")
        return wrapper
    return decorator


def event_pattern(pattern: str, priority: int = EventPriority.NORMAL):
    def decorator(func):
        @wraps(func)
        def wrapper(event_name, *args, **kwargs):
            return func(event_name, *args, **kwargs)

        if not hasattr(wrapper, '_event_patterns'):
            wrapper._event_patterns = []
        wrapper._event_patterns.append((pattern, priority))
        with _REGISTRY_LOCK:
            if wrapper not in _WILDCARD_HANDLER_FUNCTIONS:
                _WILDCARD_HANDLER_FUNCTIONS.append(wrapper)
        logger.debug(f"[EVENT] 已注册模式事件处理器: {pattern} (优先级: {priority}) -> {func.__name__}")
        return wrapper
    return decorator


def event_wildcard(priority: int = EventPriority.NORMAL):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        if not hasattr(wrapper, '_event_patterns'):
            wrapper._event_patterns = []
        wrapper._event_patterns.append(('*', priority))
        with _REGISTRY_LOCK:
            if wrapper not in _WILDCARD_HANDLER_FUNCTIONS:
                _WILDCARD_HANDLER_FUNCTIONS.append(wrapper)
        logger.debug(f"[EVENT] 已注册通配符事件处理器 (优先级: {priority}) -> {func.__name__}")
        return wrapper
    return decorator


def emitter(event_type: str, priority: int = EventPriority.NORMAL):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            full_event_type = event_type
            if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
                full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"
            
            event_bus.publish(full_event_type, *args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def subscribe_dynamic(event_type: str, handler: Callable, priority: int = EventPriority.NORMAL):
    full_event_type = event_type
    if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
        full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"
    event_bus.subscribe(full_event_type, handler, priority)


def subscribe_pattern_dynamic(pattern: str, handler: Callable, priority: int = EventPriority.NORMAL):
    event_bus.subscribe_pattern(pattern, handler, priority)


def subscribe_once_dynamic(event_type: str, handler: Callable):
    full_event_type = event_type
    if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
        full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"
    event_bus.subscribe_once(full_event_type, handler)


def register_component_handlers(component_instance: Any):
    component_name = type(component_instance).__name__
    
    if not hasattr(register_component_handlers, '_registered'):
        register_component_handlers._registered = set()
    
    if component_name in register_component_handlers._registered:
        logger.warning(f"[WARN] {component_name} 已经注册过事件处理器，跳过")
        return
    
    register_component_handlers._registered.add(component_name)
    
    for event_type, handlers in _EVENT_HANDLERS_REGISTRY.items():
        for handler, priority, handler_key in handlers:
            handler_class = handler_key[0].split('.')[0]
            handler_module = handler_key[1]
            component_class = type(component_instance).__name__
            component_module = component_instance.__class__.__module__
            
            if handler_class == component_class and handler_module == component_module:
                bound_handler = handler.__get__(component_instance, type(component_instance))
                event_bus.subscribe(event_type, bound_handler, priority)
                logger.debug(f"[EVENT] 订阅处理器: {event_type} -> {handler_key[0]}")
    
    for event_type, handlers in _ONCE_HANDLERS_REGISTRY.items():
        for handler, priority in handlers:
            if inspect.ismethod(handler) and hasattr(handler, '__self__'):
                event_bus.subscribe_once(event_type, handler)
            else:
                try:
                    bound_handler = handler.__get__(component_instance, type(component_instance))
                    if inspect.ismethod(bound_handler) and hasattr(bound_handler, '__self__') and hasattr(handler, '__self__') and handler.__self__ is bound_handler.__self__:
                        bound_handler = handler
                except (AttributeError, TypeError):
                    bound_handler = handler
                event_bus.subscribe_once(event_type, bound_handler)
            logger.debug(f"[EVENT] 自动注册一次性处理器: {event_type} -> {handler.__name__}")
    
    for pattern, handlers in _WILDCARD_HANDLERS.items():
        for handler, priority in handlers:
            if inspect.ismethod(handler) and hasattr(handler, '__self__'):
                event_bus.subscribe_wildcard(handler, priority) if pattern == "*" else event_bus.subscribe_pattern(pattern, handler, priority)
            else:
                try:
                    bound_handler = handler.__get__(component_instance, type(component_instance))
                    if inspect.ismethod(bound_handler) and hasattr(bound_handler, '__self__') and hasattr(handler, '__self__') and handler.__self__ is bound_handler.__self__:
                        bound_handler = handler
                except (AttributeError, TypeError):
                    bound_handler = handler
                logger.debug(f"[DEBUG] 绑定通配符处理器: original={handler}, bound={bound_handler}, pattern={pattern}")
                
                if pattern == "*":
                    event_bus.subscribe_wildcard(bound_handler, priority)
                else:
                    event_bus.subscribe_pattern(pattern, bound_handler, priority)
            logger.debug(f"[EVENT] 自动注册模式处理器: {pattern} -> {handler.__name__}")
    
    for handler in _WILDCARD_HANDLER_FUNCTIONS:
        patterns = getattr(handler, '_event_patterns', [])
        for pattern, priority in patterns:
            if inspect.ismethod(handler) and hasattr(handler, '__self__'):
                bound_handler = handler
            else:
                try:
                    bound_handler = handler.__get__(component_instance, type(component_instance))
                    if inspect.ismethod(bound_handler) and hasattr(bound_handler, '__self__') and hasattr(handler, '__self__') and handler.__self__ is bound_handler.__self__:
                        bound_handler = handler
                except (AttributeError, TypeError):
                    bound_handler = handler
            logger.debug(f"[DEBUG] 绑定通配符处理器(装饰器): original={handler}, bound={bound_handler}, pattern={pattern}")
            
            if pattern == "*":
                event_bus.subscribe_wildcard(bound_handler, priority)
            else:
                event_bus.subscribe_pattern(pattern, bound_handler, priority)
            logger.debug(f"[EVENT] 自动注册模式处理器(装饰器): {pattern} -> {handler.__name__}")


def register_event_class(event_type: str, event_class: Type[BaseEvent]):
    full_event_type = event_type
    if _EVENT_NAMESPACE and not event_type.startswith(_EVENT_NAMESPACE + "."):
        full_event_type = f"{_EVENT_NAMESPACE}.{event_type}"
    event_bus.register_event_class(full_event_type, event_class)


def auto_register(cls):
    if hasattr(cls, '_auto_register_event'):
        cls._auto_register_event = True
    return cls


class EventBusManager:
    """EventBus 延迟初始化单例管理器

    替代模块级全局变量 event_bus，支持：
      - 延迟初始化（首次调用时才创建）
      - 显式依赖注入
      - 测试时可替换
    """
    _instance: 'EventBus' = None

    @classmethod
    def get_event_bus(cls) -> 'EventBus':
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    @classmethod
    def set_event_bus(cls, bus: 'EventBus') -> None:
        cls._instance = bus


def get_event_bus() -> 'EventBus':
    return EventBusManager.get_event_bus()


# 向后兼容：模块级 event_bus 变量（延迟初始化）
# 新代码推荐使用 get_event_bus() 或 EventBusManager.get_event_bus()
class _EventBusLazy:
    """兼容旧代码的延迟引用"""
    def __getattr__(self, name):
        bus = EventBusManager.get_event_bus()
        return getattr(bus, name)
    def __call__(self, *args, **kwargs):
        bus = EventBusManager.get_event_bus()
        return bus(*args, **kwargs)

event_bus = _EventBusLazy()  # type: ignore[assignment]

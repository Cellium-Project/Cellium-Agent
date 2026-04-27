# -*- coding: utf-8 -*-

import builtins
import logging
import os
import sys
from types import ModuleType
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================
# 路径隔离（Root Jail）配置
# ============================================================

# 允许组件访问的根目录（默认为项目目录下的 sandbox_root）
SANDBOX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "sandbox_root"))

# 路径相关的函数需要拦截
PATH_SENSITIVE_FUNCTIONS = {
    "os": ["open", "listdir", "scandir", "mkdir", "makedirs", "remove", "unlink", 
           "rmdir", "rename", "replace", "chmod", "chown", "stat", "lstat", 
           "access", "link", "symlink", "readlink", "getcwd", "chdir"],
    "builtins": ["open"],
}


# ============================================================
# 危险方法定义（运行时拦截）
# ============================================================

# 完全禁止导入的模块（动态代码执行）
BANNED_IMPORTS: Set[str] = {
    "eval", "exec", "compile", "__import__", "execfile", "input",
}

# 允许导入但方法受限的模块
PROTECTED_IMPORTS: Set[str] = {
    "os", "subprocess", "shutil",
}

# 各模块的危险方法（运行时拦截）
DANGEROUS_METHODS: Dict[str, Set[str]] = {
    "os": {
        "system", "popen", "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "exec", "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
        "fork", "kill", "killpg", "nice", "abort",
    },
    "subprocess": {
        "call", "run", "Popen", "check_call", "check_output",
        "getoutput", "getstatusoutput",
    },
    "shutil": {
        "rmtree", "move", "copy2", "copytree",
    },
}


def _resolve_path(path: Any) -> str:
    if hasattr(path, "__fspath__"):
        return os.fspath(path)
    return str(path)


def _translate_path(user_path: str) -> str:
    try:
        # 确保沙箱根目录存在
        if not os.path.exists(SANDBOX_ROOT):
            os.makedirs(SANDBOX_ROOT, exist_ok=True)

        resolved = _resolve_path(user_path)

        resolved = resolved.replace('\\', '/')

        if len(resolved) >= 2 and resolved[1] == ':':
            drive = resolved[0].upper() 
            rest = resolved[2:].lstrip('/')
            relative_path = f"{drive}_/{rest}"
        elif resolved.startswith('/'):
            relative_path = resolved.lstrip('/')
        else:
            abs_path = os.path.abspath(resolved)
            abs_path_normalized = abs_path.replace('\\', '/')
            sandbox_root_normalized = os.path.abspath(SANDBOX_ROOT).replace('\\', '/')

            if abs_path_normalized.startswith(sandbox_root_normalized):
                return abs_path

            relative_path = resolved.lstrip('./')

        relative_path = relative_path.replace('/', os.sep)

        parts = relative_path.split(os.sep)
        safe_parts = [p for p in parts if p and p != '.' and p != '..']
        safe_relative = os.sep.join(safe_parts)

        final_path = os.path.join(SANDBOX_ROOT, safe_relative)
        return os.path.normpath(final_path)

    except Exception as e:
        logger.warning("[ProtectedModules] 路径映射失败: %s, 使用默认路径", e)
        return SANDBOX_ROOT


def _make_secure_path_func(original_func: Callable, func_name: str) -> Callable:
    def secure_func(path, *args, **kwargs):

        safe_path = _translate_path(path)
        return original_func(safe_path, *args, **kwargs)

    secure_func.__name__ = func_name
    secure_func.__doc__ = original_func.__doc__
    return secure_func


class ProtectedModuleProxy:
    """
    受保护的模块代理

    拦截对危险方法的访问，放行安全方法。
    使用 __getattr__ 实现透明代理。
    """

    def __init__(self, original_module: ModuleType, module_name: str):
        """
        Args:
            original_module: 原始模块对象（如 os）
            module_name: 模块名（用于错误提示）
        """
        object.__setattr__(self, "_original", original_module)
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_dangerous", DANGEROUS_METHODS.get(module_name, set()))
        object.__setattr__(self, "_path_sensitive", PATH_SENSITIVE_FUNCTIONS.get(module_name, []))

    def __getattr__(self, name: str) -> Any:
        """拦截属性访问"""
        if name in self._dangerous:
            raise PermissionError(
                f"[Security] Component blocked from calling {self._module_name}.{name}().\n"
                f"Reason: This method could cause system damage.\n"
                f"Alternative: Use the shell tool for system commands."
            )
        
        if name in self._path_sensitive:
            original_func = getattr(self._original, name)
            return _make_secure_path_func(original_func, name)
        
        return getattr(self._original, name)

    def __setattr__(self, name: str, value: Any):
        """允许设置属性（部分模块需要）"""
        setattr(self._original, name, value)

    def __repr__(self):
        return f"<ProtectedModuleProxy {self._module_name}>"


# ============================================================
# 保护状态管理
# ============================================================

_original_modules: Dict[str, ModuleType] = {}
_original_open: Optional[Callable] = None
_protection_depth = 0


def _enable_protection():
    """启用保护：替换 sys.modules 中的受保护模块，并 patch builtins.open"""
    global _original_open
    
    for module_name in PROTECTED_IMPORTS:
        if module_name in sys.modules:
            original = sys.modules[module_name]
            if not isinstance(original, ProtectedModuleProxy):
                _original_modules[module_name] = original
                sys.modules[module_name] = ProtectedModuleProxy(original, module_name)
                logger.debug("[ProtectedModules] Replaced sys.modules['%s']", module_name)
    
    if _original_open is None:
        _original_open = builtins.open
        builtins.open = _make_secure_path_func(builtins.open, "open")
        logger.debug("[ProtectedModules] Patched builtins.open")


def _disable_protection():
    global _original_open
    
    for module_name, original in _original_modules.items():
        sys.modules[module_name] = original
        logger.debug("[ProtectedModules] Restored sys.modules['%s']", module_name)
    _original_modules.clear()
    
    if _original_open is not None:
        builtins.open = _original_open
        _original_open = None
        logger.debug("[ProtectedModules] Restored builtins.open")


class ProtectedContext:
    """
    保护上下文管理器

    在 with 块内启用模块保护，退出时恢复。

    用法：
        from app.core.util.protected_modules import ProtectedContext

        with ProtectedContext():
            # 此处 import os 返回 ProtectedModuleProxy
            import some_component
            some_component.execute()
    """

    _depth = 0

    def __enter__(self):
        ProtectedContext._depth += 1
        if ProtectedContext._depth == 1:
            _enable_protection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ProtectedContext._depth -= 1
        if ProtectedContext._depth == 0:
            _disable_protection()
        return False


def protected_execution():
    return ProtectedContext()


def wrap_module(module: ModuleType, module_name: str) -> ProtectedModuleProxy:
    """
    包装模块为受保护的代理

    Args:
        module: 原始模块对象
        module_name: 模块名

    Returns:
        ProtectedModuleProxy 实例
    """
    return ProtectedModuleProxy(module, module_name)


def is_protected_module(module_name: str) -> bool:
    """检查模块是否需要运行时保护"""
    return module_name in PROTECTED_IMPORTS


def is_banned_module(module_name: str) -> bool:
    """检查模块是否完全禁止导入"""
    return module_name in BANNED_IMPORTS


def get_dangerous_methods(module_name: str) -> Set[str]:
    """获取模块的危险方法列表"""
    return DANGEROUS_METHODS.get(module_name, set())

# -*- coding: utf-8 -*-
"""
ProtectedModules — 组件运行时保护代理

为用户创建的组件提供运行时方法拦截，允许导入 os/subprocess 等模块，
但阻止调用危险方法（如 os.system、subprocess.run）。

架构：
    启用保护时 → 替换 sys.modules 中的受保护模块
    组件调用 os.system() → Proxy 拦截 → 抛出 PermissionError
    组件调用 os.path.join() → Proxy 放行 → 正常执行
"""

import logging
import sys
from types import ModuleType
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


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

    def __getattr__(self, name: str) -> Any:
        """拦截属性访问"""
        if name in self._dangerous:
            raise PermissionError(
                f"[Security] Component blocked from calling {self._module_name}.{name}().\n"
                f"Reason: This method could cause system damage.\n"
                f"Alternative: Use the shell tool for system commands."
            )
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
_protection_depth = 0


def _enable_protection():
    """启用保护：替换 sys.modules 中的受保护模块"""
    global _original_modules

    for module_name in PROTECTED_IMPORTS:
        if module_name in sys.modules:
            original = sys.modules[module_name]
            if not isinstance(original, ProtectedModuleProxy):
                _original_modules[module_name] = original
                sys.modules[module_name] = ProtectedModuleProxy(original, module_name)
                logger.debug("[ProtectedModules] Replaced sys.modules['%s']", module_name)


def _disable_protection():
    """禁用保护：恢复 sys.modules 中的原始模块"""
    global _original_modules

    for module_name, original in _original_modules.items():
        sys.modules[module_name] = original
        logger.debug("[ProtectedModules] Restored sys.modules['%s']", module_name)
    _original_modules.clear()


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
    """创建保护上下文"""
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

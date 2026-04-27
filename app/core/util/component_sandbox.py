# -*- coding: utf-8 -*-
"""
ComponentSandbox — 组件子进程沙箱

在独立进程中执行用户创建的组件代码，实现真正的进程隔离。
即使组件执行危险操作，也不会影响主进程。

架构：
    主进程                    沙箱子进程
    ──────                    ─────────
    CellToolAdapter  ──IPC──>  SandboxWorker
         │                         │
         │                    执行组件代码
         │                         │
    返回结果  <──IPC──  返回结果/异常

IPC 协议：multiprocessing.Queue
"""

import json
import logging
import multiprocessing
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.util.protected_modules import ProtectedContext

logger = logging.getLogger(__name__)

# 沙箱超时（秒）
SANDBOX_TIMEOUT = 120


def _sandbox_worker(input_queue: multiprocessing.Queue, output_queue: multiprocessing.Queue,
                    module_path: str, class_name: str, init_args: Dict, project_root: str):
    """
    沙箱工作进程 - 在独立进程中执行组件代码

    这个函数在子进程中运行，通过 Queue 与主进程通信
    """
    try:
        if project_root and project_root not in sys.path:
            sys.path.insert(0, project_root)

        with ProtectedContext():
            # 定义允许的 builtins（白名单）
            allowed_builtins = {
                'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'tuple', 'set',
                'print', 'range', 'enumerate', 'zip', 'map', 'filter', 'sum', 'min', 'max',
                'abs', 'round', 'pow', 'divmod', 'chr', 'ord', 'hex', 'oct', 'bin',
                'isinstance', 'issubclass', 'hasattr', 'getattr', 'setattr', 'delattr',
                'type', 'id', 'repr', 'format', 'sorted', 'reversed', 'open',
                'Exception', 'BaseException', 'TypeError', 'ValueError', 'KeyError',
                'IndexError', 'AttributeError', 'RuntimeError', 'StopIteration',
                'True', 'False', 'None', 'Ellipsis', 'NotImplemented',
                'bytes', 'bytearray', 'memoryview', 'frozenset',
                'iter', 'next', 'slice', 'super', 'object', 'staticmethod', 'classmethod',
                'property', 'vars', 'locals', 'globals', '__import__',
            }

            import builtins
            safe_builtins = {k: v for k, v in builtins.__dict__.items() if k in allowed_builtins}

            import importlib.util
            spec = importlib.util.spec_from_file_location("sandbox_component", module_path)
            module = importlib.util.module_from_spec(spec)

            module.__dict__['__builtins__'] = safe_builtins

            spec.loader.exec_module(module)
            component_class = getattr(module, class_name)
            component = component_class(**init_args)
            
            output_queue.put({"status": "ok", "cell_name": getattr(component, "cell_name", "unknown")})
            
            while True:
                try:
                    request = input_queue.get(timeout=SANDBOX_TIMEOUT)
                    if request is None:
                        break
                    
                    action = request.get("action")
                    
                    if action == "execute":
                        command = request.get("command", "")
                        args = request.get("args", [])
                        kwargs = request.get("kwargs", {})
                        result = component.execute(command, *args, **kwargs)
                        output_queue.put({"status": "ok", "result": result})
                        
                    elif action == "get_commands":
                        commands = component.get_commands()
                        output_queue.put({"status": "ok", "commands": commands})
                        
                    elif action == "ping":
                        output_queue.put({"status": "pong"})
                    
                    else:
                        output_queue.put({"status": "error", "error": f"Unknown action: {action}"})
                        
                except Exception as e:
                    error_str = str(e) or f"{type(e).__name__} (no error message)"
                    output_queue.put({
                        "status": "error",
                        "error": error_str,
                        "error_type": type(e).__name__,
                        "traceback": traceback.format_exc(),
                    })
                
    except Exception as e:
        error_str = str(e) or f"{type(e).__name__} (no error message)"
        output_queue.put({
            "status": "error",
            "error": f"Failed to init component: {error_str}",
            "error_type": type(e).__name__,
            "traceback": traceback.format_exc(),
        })


class SandboxProcess:
    """
    沙箱子进程管理器

    使用 multiprocessing 启动独立进程，用于执行组件代码。
    支持 Nuitka 打包环境。
    """

    def __init__(self, timeout: int = SANDBOX_TIMEOUT):
        """
        Args:
            timeout: 执行超时（秒）
        """
        self._timeout = timeout
        self._process: Optional[multiprocessing.Process] = None
        self._input_queue: Optional[multiprocessing.Queue] = None
        self._output_queue: Optional[multiprocessing.Queue] = None
        self._initialized = False
        self._module_path = None
        self._class_name = None

    def start(self, module_path: str, class_name: str, init_args: Dict = None, project_root: str = None):
        """
        启动沙箱进程并初始化组件
        
        Args:
            module_path: 组件文件路径
            class_name: 组件类名
            init_args: 初始化参数
            project_root: 项目根目录
        """
        if self._process is not None and self._process.is_alive():
            return

        self._module_path = module_path
        self._class_name = class_name
        init_args = init_args or {}
        project_root = project_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        self._input_queue = multiprocessing.Queue()
        self._output_queue = multiprocessing.Queue()

        self._process = multiprocessing.Process(
            target=_sandbox_worker,
            args=(self._input_queue, self._output_queue, module_path, class_name, init_args, project_root)
        )
        self._process.start()

        logger.debug("[Sandbox] Process started, pid=%s", self._process.pid)

        # 等待初始化结果
        try:
            init_result = self._output_queue.get(timeout=self._timeout)
            if init_result.get("status") == "ok":
                self._initialized = True
                logger.info("[Sandbox] Component initialized: %s", init_result.get("cell_name"))
            else:
                error = init_result.get("error", "Unknown error")
                logger.error("[Sandbox] Component init failed: %s", error)
                self.stop()
                raise RuntimeError(f"Sandbox init failed: {error}")
        except Exception as e:
            logger.error("[Sandbox] Timeout waiting for init: %s", e)
            self.stop()
            raise

    def execute(self, command: str, *args, **kwargs) -> Any:
        """
        在沙箱中执行命令
        
        Args:
            command: 命令名
            *args: 位置参数
            **kwargs: 关键字参数
            
        Returns:
            命令执行结果
            
        Raises:
            RuntimeError: 沙箱未初始化或执行失败
        """
        if not self._initialized or not self._process or not self._process.is_alive():
            raise RuntimeError("Sandbox not initialized or process dead")

        # 发送执行请求
        self._input_queue.put({
            "action": "execute",
            "command": command,
            "args": args,
            "kwargs": kwargs
        })

        # 等待结果
        try:
            result = self._output_queue.get(timeout=self._timeout)
            if result.get("status") == "ok":
                return result.get("result")
            else:
                error = result.get("error", "Unknown error")
                error_type = result.get("error_type", "Error")
                raise RuntimeError(f"[{error_type}] {error}")
        except Exception as e:
            logger.error("[Sandbox] Command execution failed: %s", e)
            raise

    def get_commands(self) -> list:
        """获取组件支持的命令列表"""
        if not self._initialized:
            return []

        self._input_queue.put({"action": "get_commands"})
        try:
            result = self._output_queue.get(timeout=5)
            if result.get("status") == "ok":
                return result.get("commands", [])
        except:
            pass
        return []

    def ping(self) -> bool:
        """检查沙箱进程是否存活"""
        if not self._process or not self._process.is_alive():
            return False
        
        try:
            self._input_queue.put({"action": "ping"})
            result = self._output_queue.get(timeout=5)
            return result.get("status") == "pong"
        except:
            return False

    def stop(self):
        """停止沙箱进程"""
        if self._process and self._process.is_alive():
            try:
                # 发送终止信号
                self._input_queue.put(None)
                # 等待进程结束
                self._process.join(timeout=5)
                # 强制终止
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=2)
            except Exception as e:
                logger.warning("[Sandbox] Error stopping process: %s", e)

        self._initialized = False
        self._process = None
        self._input_queue = None
        self._output_queue = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# 全局沙箱实例缓存
_sandbox_instances: Dict[str, 'ComponentSandbox'] = {}


class ComponentSandbox:
    """
    组件沙箱接口

    提供与 CellToolAdapter 兼容的接口，内部使用 SandboxProcess。
    """

    def __init__(self, name: str):
        """
        Args:
            name: 组件名称
        """
        self._name = name
        self._sandbox: Optional[SandboxProcess] = None

    def initialize(self, module_path: str, class_name: str, init_args: Dict = None):
        """
        初始化沙箱
        
        Args:
            module_path: 组件文件路径
            class_name: 组件类名
            init_args: 初始化参数
        """
        self._sandbox = SandboxProcess()
        self._sandbox.start(module_path, class_name, init_args)

    def execute(self, command: str, *args, **kwargs) -> Any:
        """执行命令"""
        if not self._sandbox:
            raise RuntimeError("Sandbox not initialized")
        return self._sandbox.execute(command, *args, **kwargs)

    def get_commands(self) -> list:
        """获取支持的命令列表"""
        if not self._sandbox:
            return []
        return self._sandbox.get_commands()

    def stop(self):
        """停止沙箱"""
        if self._sandbox:
            self._sandbox.stop()
            self._sandbox = None

    @classmethod
    def get_sandbox(cls, name: str) -> 'ComponentSandbox':
        """
        获取或创建沙箱实例（单例模式）
        
        Args:
            name: 组件名称
            
        Returns:
            ComponentSandbox 实例
        """
        if name not in _sandbox_instances:
            _sandbox_instances[name] = cls(name)
        return _sandbox_instances[name]

    @classmethod
    def reload_sandbox(cls, name: str) -> 'ComponentSandbox':
        """
        重新加载沙箱（用于组件热更新）
        
        Args:
            name: 组件名称
            
        Returns:
            新的 ComponentSandbox 实例
        """
        if name in _sandbox_instances:
            old_sandbox = _sandbox_instances[name]
            old_sandbox.stop()
            del _sandbox_instances[name]
            logger.debug("[ComponentSandbox] 已删除旧沙箱实例: %s", name)
        
        return cls.get_sandbox(name)

    def __del__(self):
        """析构时自动停止沙箱"""
        self.stop()

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

logger = logging.getLogger(__name__)

# 沙箱超时（秒）
SANDBOX_TIMEOUT = 60


def _sandbox_worker(input_queue: multiprocessing.Queue, output_queue: multiprocessing.Queue, 
                    module_path: str, class_name: str, init_args: Dict, project_root: str):
    """
    沙箱工作进程 - 在独立进程中执行组件代码
    
    这个函数在子进程中运行，通过 Queue 与主进程通信
    """
    try:
        # 设置项目根目录到 sys.path
        if project_root and project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        # 动态加载组件类
        import importlib.util
        spec = importlib.util.spec_from_file_location("sandbox_component", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        component_class = getattr(module, class_name)
        component = component_class(**init_args)
        
        # 通知主进程初始化成功
        output_queue.put({"status": "ok", "cell_name": getattr(component, "cell_name", "unknown")})
        
        # 处理命令
        while True:
            try:
                request = input_queue.get(timeout=SANDBOX_TIMEOUT)
                if request is None:  # 终止信号
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
                output_queue.put({
                    "status": "error",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                })
                
    except Exception as e:
        output_queue.put({
            "status": "error",
            "error": f"Failed to init component: {str(e)}",
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

        # 创建通信队列
        self._input_queue = multiprocessing.Queue()
        self._output_queue = multiprocessing.Queue()

        # 启动子进程
        self._process = multiprocessing.Process(
            target=_sandbox_worker,
            args=(self._input_queue, self._output_queue, module_path, class_name, init_args, project_root)
        )
        self._process.start()

        logger.debug("[Sandbox] Process started, pid=%s", self._process.pid)

        # 等待初始化响应
        try:
            response = self._output_queue.get(timeout=self._timeout)
            if response.get("status") == "ok":
                self._initialized = True
                logger.debug("[Sandbox] Component initialized: %s", response.get("cell_name"))
            else:
                error = response.get("error", "Unknown error")
                logger.error("[Sandbox] Failed to init component: %s", error)
                self.stop()
                raise RuntimeError(f"Failed to init component: {error}")
        except Exception as e:
            self.stop()
            raise RuntimeError(f"Sandbox communication error: {e}")

    def stop(self):
        """停止沙箱进程"""
        if self._process is None:
            return

        try:
            # 发送终止信号
            if self._input_queue:
                self._input_queue.put(None)
            
            # 等待进程结束
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2)
                if self._process.is_alive():
                    self._process.kill()
        except Exception as e:
            logger.warning("[Sandbox] Error stopping process: %s", e)
        finally:
            self._process = None
            self._initialized = False
            self._input_queue = None
            self._output_queue = None

        logger.debug("[Sandbox] Process stopped")

    def execute(self, command: str, *args, **kwargs) -> Dict[str, Any]:
        """执行组件命令"""
        if not self._initialized:
            raise RuntimeError("Sandbox not initialized")

        request = {
            "action": "execute",
            "command": command,
            "args": args,
            "kwargs": kwargs,
        }
        
        self._input_queue.put(request)
        return self._output_queue.get(timeout=self._timeout)

    def get_commands(self) -> Dict[str, Any]:
        """获取组件命令列表"""
        if not self._initialized:
            raise RuntimeError("Sandbox not initialized")

        request = {"action": "get_commands"}
        self._input_queue.put(request)
        return self._output_queue.get(timeout=self._timeout)

    def ping(self) -> bool:
        """检查沙箱是否存活"""
        if not self._initialized or not self._process or not self._process.is_alive():
            return False
        
        try:
            request = {"action": "ping"}
            self._input_queue.put(request)
            response = self._output_queue.get(timeout=5)
            return response.get("status") == "pong"
        except:
            return False


# 全局沙箱实例缓存
_sandbox_instances: Dict[str, 'ComponentSandbox'] = {}


class ComponentSandbox:
    """
    组件沙箱封装类

    提供与原始 Cell 相同的接口，但内部使用子进程隔离执行。
    """

    def __init__(self, name: str, module_path: str = None, class_name: str = None, init_args: Dict = None):
        """
        Args:
            name: 组件名称（唯一标识）
            module_path: 组件 .py 文件路径
            class_name: 组件类名
            init_args: 初始化参数字典
        """
        self.name = name
        self.module_path = module_path
        self.class_name = class_name
        self.init_args = init_args or {}
        self._sandbox = SandboxProcess()
        self._cell_name = None
        self._initialized = False

    @staticmethod
    def get_sandbox(name: str) -> 'ComponentSandbox':
        """获取或创建沙箱实例（单例模式）"""
        if name not in _sandbox_instances:
            _sandbox_instances[name] = ComponentSandbox(name)
        return _sandbox_instances[name]

    def init_component(self, module_path: str, class_name: str, init_args: Dict = None, project_root: str = None):
        """初始化组件"""
        self.module_path = module_path
        self.class_name = class_name
        if init_args:
            self.init_args.update(init_args)
        
        success = self.initialize(project_root)
        self._initialized = success
        return success

    def initialize(self, project_root: str = None) -> bool:
        """初始化沙箱和组件"""
        if not self.module_path or not self.class_name:
            logger.error("[ComponentSandbox] module_path and class_name required")
            return False
        
        logger.debug("[ComponentSandbox] Initializing %s from %s", self.class_name, self.module_path)
            
        try:
            self._sandbox.start(self.module_path, self.class_name, self.init_args, project_root)
            self._initialized = True
            logger.debug("[ComponentSandbox] Successfully initialized %s", self.class_name)
            return True
        except Exception as e:
            import traceback
            logger.error("[ComponentSandbox] Failed to initialize %s: %s\n%s", 
                        self.class_name, e, traceback.format_exc())
            self._initialized = False
            return False

    def execute(self, command: str, args: list = None, kwargs: dict = None) -> Any:
        """执行组件命令"""
        args = args or []
        kwargs = kwargs or {}
        result = self._sandbox.execute(command, *args, **kwargs)
        if result.get("status") == "error":
            raise RuntimeError(result.get("error", "Unknown error"))
        return result.get("result")

    def get_commands(self) -> list:
        """获取组件支持的命令列表"""
        result = self._sandbox.get_commands()
        if result.get("status") == "error":
            raise RuntimeError(result.get("error", "Unknown error"))
        return result.get("commands", [])

    @property
    def cell_name(self) -> str:
        """组件名称"""
        return self._cell_name or self.class_name.lower()

    def is_alive(self) -> bool:
        """检查沙箱是否存活"""
        return self._sandbox.ping()

    def stop(self):
        """停止沙箱"""
        self._sandbox.stop()

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

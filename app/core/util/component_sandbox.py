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
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.interface.icell import ICell
from app.core.util.protected_modules import ProtectedContext


def ensure_import(module_name: str, project_root: str = None) -> Any:
    import importlib
    
    if project_root:
        libs_dir = os.path.join(project_root, "libs")
        if os.path.exists(libs_dir) and libs_dir not in sys.path:
            sys.path.insert(0, libs_dir)
    
    try:
        return importlib.import_module(module_name)
    except ImportError:
        import importlib.machinery
        importlib.machinery.PathFinder.invalidate_caches()
        return importlib.import_module(module_name)

logger = logging.getLogger(__name__)

# 沙箱超时（秒）
SANDBOX_TIMEOUT = 120


def _sandbox_worker(input_queue: multiprocessing.Queue, output_queue: multiprocessing.Queue,
                    module_path: str, class_name: str, init_args: Dict, project_root: str):
    """
    沙箱工作进程 - 在独立进程中执行组件代码

    这个函数在子进程中运行，通过 Queue 与主进程通信
    优化：减少内存占用，只导入必要的模块
    """
    import time  # 心跳管理需要
    
    try:
        # 清理不必要的模块以减少内存占用（fork后继承的模块）
        # 注意：保留 multiprocessing 相关模块，因为 Queue 通信需要
        modules_to_remove = [
            'numpy', 'pandas', 'matplotlib', 'sklearn', 'torch', 'tensorflow',
            'cv2', 'PIL.Image', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin',
            'requests', 'urllib3', 'http.client', 'http.cookiejar',
            'email', 'xml.etree', 'xml.dom', 'html.parser',
            'csv', 'pickle', 'sqlite3', 'hashlib', 'hmac',
            'ssl', 'socketserver', 'asyncio', 'concurrent.futures',
            'subprocess', 'threading', 'queue',
        ]
        for mod_name in list(sys.modules.keys()):
            if any(mod_name.startswith(rm) for rm in modules_to_remove):
                try:
                    del sys.modules[mod_name]
                except:
                    pass
        
        # 强制垃圾回收
        import gc
        gc.collect()
        
        if project_root and project_root not in sys.path:
            sys.path.insert(0, project_root)

        libs_dir = os.path.join(project_root, "libs")
        if os.path.exists(libs_dir) and libs_dir not in sys.path:
            sys.path.insert(0, libs_dir)

        with ProtectedContext():
            import builtins
            import importlib.util

            def sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
                if libs_dir and os.path.exists(libs_dir) and libs_dir not in sys.path:
                    sys.path.insert(0, libs_dir)

                try:
                    return builtins.__import__(name, globals, locals, fromlist, level)
                except ImportError:
                    import importlib.machinery
                    importlib.machinery.PathFinder.invalidate_caches()
                    return builtins.__import__(name, globals, locals, fromlist, level)

            try:
                cached = importlib.util.cache_from_source(module_path)
                if cached and os.path.exists(cached):
                    os.remove(cached)
                    logger.debug("[Sandbox] 已删除缓存: %s", cached)
            except Exception as e:
                logger.debug("[Sandbox] 删除缓存失败: %s", e)

            try:
                pycache_dir = Path(module_path).parent / "__pycache__"
                if pycache_dir.exists():
                    stem = Path(module_path).stem
                    for cached_file in pycache_dir.glob(f"{stem}*.pyc"):
                        try:
                            cached_file.unlink()
                            logger.debug("[Sandbox] 已删除缓存(glob): %s", cached_file)
                        except Exception:
                            pass
            except Exception as e:
                logger.debug("[Sandbox] glob清理缓存失败: %s", e)

            spec = importlib.util.spec_from_file_location("sandbox_component", module_path)
            module = importlib.util.module_from_spec(spec)

            module.__dict__['__builtins__'] = builtins.__dict__
            module.__dict__['__import__'] = sandbox_import

            spec.loader.exec_module(module)
            component_class = getattr(module, class_name)
            component = component_class(**init_args)

            if hasattr(component, "on_load"):
                try:
                    component.on_load()
                except Exception as e:
                    logger.warning("[Sandbox] on_load failed: %s", e)

        output_queue.put({"status": "ok", "cell_name": getattr(component, "cell_name", "unknown")})

        # 心跳管理
        HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
        BACKGROUND_TIMEOUT = 300  # 后台组件超时（秒）
        last_heartbeat = time.time()
        
        from queue import Empty as QueueEmpty
        
        while True:
            try:
                has_background = False
                if hasattr(component, '_running'):
                    has_background = component._running
                
                if has_background:
                    # 后台模式：使用更长的超时时间
                    timeout = BACKGROUND_TIMEOUT
                else:
                    # 普通模式：使用默认超时
                    timeout = SANDBOX_TIMEOUT
                
                request = input_queue.get(timeout=timeout)
                if request is None:
                    break

                action = request.get("action")

                if action == "execute":
                    if project_root and project_root not in sys.path:
                        sys.path.insert(0, project_root)
                    if libs_dir and os.path.exists(libs_dir) and libs_dir not in sys.path:
                        sys.path.insert(0, libs_dir)

                    command = request.get("command", "")
                    args = request.get("args", [])
                    kwargs = request.get("kwargs", {})
                    result = component.execute(command, *args, **kwargs)
                    output_queue.put({"status": "ok", "result": result})

                elif action == "get_commands":
                    commands = component.get_commands()
                    output_queue.put({"status": "ok", "commands": commands})

                elif action == "get_command_params":
                    params_map = component.get_command_params() if hasattr(component, 'get_command_params') else {}
                    output_queue.put({"status": "ok", "params_map": params_map})

                elif action == "ping":
                    output_queue.put({"status": "pong"})

                elif action == "heartbeat":
                    last_heartbeat = time.time()
                    has_bg = False
                    if hasattr(component, '_running'):
                        has_bg = component._running
                    output_queue.put({"status": "ok", "has_background": has_bg})

                else:
                    output_queue.put({"status": "error", "error": f"Unknown action: {action}"})

            except QueueEmpty:
                continue
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
        self._is_executing = False 

    def start(self, module_path: str, class_name: str, init_args: Dict = None, project_root: str = None, retry: int = 2):
        """
        启动沙箱进程并初始化组件

        Args:
            module_path: 组件文件路径
            class_name: 组件类名
            init_args: 初始化参数
            project_root: 项目根目录
            retry: 初始化失败重试次数
        """
        if self._process is not None and self._process.is_alive():
            return

        self._module_path = module_path
        self._class_name = class_name
        init_args = init_args or {}
        project_root = project_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        last_error = None
        for attempt in range(retry + 1):
            if attempt > 0:
                logger.info(f"[Sandbox] Retrying initialization (attempt {attempt + 1}/{retry + 1})")
                self.stop()
                import time
                time.sleep(0.5)

            self._input_queue = multiprocessing.Queue()
            self._output_queue = multiprocessing.Queue()

            self._process = multiprocessing.Process(
                target=_sandbox_worker,
                args=(self._input_queue, self._output_queue, module_path, class_name, init_args, project_root)
            )
            self._process.start()

            logger.debug("[Sandbox] Process started, pid=%s", self._process.pid)

            try:
                init_result = self._output_queue.get(timeout=self._timeout)
                if init_result.get("status") == "ok":
                    self._initialized = True
                    logger.info("[Sandbox] Component initialized: %s", init_result.get("cell_name"))
                    break
                else:
                    last_error = init_result.get("error", "Unknown error")
                    logger.error("[Sandbox] Component init failed: %s", last_error)
            except Exception as e:
                last_error = str(e)
                logger.error("[Sandbox] Timeout waiting for init: %s", last_error)
        else:
            self.stop()
            raise RuntimeError(f"Sandbox init failed after {retry + 1} attempts: {last_error}")

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
        if not self._initialized:
            raise RuntimeError("Sandbox not initialized (init never succeeded)")
        if not self._process or not self._process.is_alive():
            logger.error("[Sandbox] Process is dead | pid=%s | module=%s", self._process.pid if self._process else None, self._module_path)
            raise RuntimeError("Sandbox not initialized or process dead")

        self._is_executing = True
        try:
            self._input_queue.put({
                "action": "execute",
                "command": command,
                "args": args,
                "kwargs": kwargs
            })

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
        finally:
            self._is_executing = False

    def get_commands(self) -> Dict[str, str]:
        """获取组件支持的命令列表"""
        if not self._initialized:
            return {}

        self._input_queue.put({"action": "get_commands"})
        try:
            result = self._output_queue.get(timeout=5)
            if result.get("status") == "ok":
                commands = result.get("commands", {})
                if isinstance(commands, dict):
                    return commands
                return {}
        except:
            pass
        return {}

    def get_command_params(self) -> Dict[str, list]:
        """获取每个命令的参数列表（用于沙箱模式的参数注入判断）"""
        if not self._initialized:
            return {}

        self._input_queue.put({"action": "get_command_params"})
        try:
            result = self._output_queue.get(timeout=5)
            if result.get("status") == "ok":
                return result.get("params_map", {})
        except:
            pass
        return {}

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

    def is_busy(self) -> bool:
        if self._is_executing:
            return True
        
        if not self._initialized or not self._process or not self._process.is_alive():
            return False
        
        try:
            self._input_queue.put({"action": "ping"})
            result = self._output_queue.get(timeout=2)
            if result.get("status") == "pong":
                return False
        except:
            pass
        
        return True

    def stop(self):
        """停止沙箱进程"""
        if self._process and self._process.is_alive():
            try:
                self._input_queue.put(None)
                self._process.join(timeout=5)
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

MAX_SANDBOX_COUNT = 15  # 最大沙箱数量
IDLE_TIMEOUT_SECONDS = 300  # 空闲超时（5分钟）

_sandbox_last_used: Dict[str, float] = {}
_sandbox_lock = threading.RLock()
_sandbox_cleanup_thread: Optional[threading.Thread] = None
_sandbox_cleanup_stop_event = threading.Event()


def _cleanup_idle_sandboxes():
    """后台线程：定期清理空闲沙箱"""
    while not _sandbox_cleanup_stop_event.is_set():
        try:
            time.sleep(60)  
            
            with _sandbox_lock:
                current_time = time.time()
                to_remove = []
                
                for name, last_used in list(_sandbox_last_used.items()):
                    if current_time - last_used > IDLE_TIMEOUT_SECONDS:
                        sandbox = _sandbox_instances.get(name)
                        if sandbox and not sandbox.is_busy():
                            to_remove.append(name)
                
                for name in to_remove:
                    logger.info("[SandboxCleanup] 清理空闲沙箱: %s", name)
                    ComponentSandbox.remove_sandbox(name)
                    
        except Exception as e:
            logger.error("[SandboxCleanup] 清理线程错误: %s", e)


def _start_cleanup_thread():
    """启动清理线程"""
    global _sandbox_cleanup_thread
    if _sandbox_cleanup_thread is None or not _sandbox_cleanup_thread.is_alive():
        _sandbox_cleanup_stop_event.clear()
        _sandbox_cleanup_thread = threading.Thread(target=_cleanup_idle_sandboxes, daemon=True)
        _sandbox_cleanup_thread.start()
        logger.info("[SandboxCleanup] 清理线程已启动")


def _stop_cleanup_thread():
    """停止清理线程"""
    global _sandbox_cleanup_thread
    if _sandbox_cleanup_thread and _sandbox_cleanup_thread.is_alive():
        _sandbox_cleanup_stop_event.set()
        _sandbox_cleanup_thread.join(timeout=5)
        logger.info("[SandboxCleanup] 清理线程已停止")


class ComponentSandbox(ICell):
    """
    组件沙箱接口

    提供与 CellToolAdapter 兼容的接口，内部使用 SandboxProcess。
    继承 ICell 以支持 tool_registry 正确注册。
    """

    def __init__(self, name: str):
        """
        Args:
            name: 组件名称
        """
        self._name = name
        self._sandbox: Optional[SandboxProcess] = None
        _start_cleanup_thread()  # 确保清理线程在运行

    @property
    def cell_name(self) -> str:
        """组件名称（与 BaseCell 接口兼容）"""
        return self._name

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

    def get_commands(self) -> Dict[str, str]:
        """获取支持的命令列表"""
        if not self._sandbox:
            return {}
        return self._sandbox.get_commands()

    def get_command_params(self) -> Dict[str, list]:
        """获取每个命令的参数列表"""
        if not self._sandbox:
            return {}
        return self._sandbox.get_command_params()

    def stop(self):
        """停止沙箱"""
        if self._sandbox:
            self._sandbox.stop()
            self._sandbox = None

    def is_alive(self) -> bool:
        return self._sandbox is not None and self._sandbox._process is not None and self._sandbox._process.is_alive()

    def is_busy(self) -> bool:
        """检查沙箱是否正在执行命令"""
        if not self._sandbox:
            return False
        return self._sandbox.is_busy()

    def heartbeat(self) -> Dict[str, Any]:
        """发送心跳，保持沙箱存活
        
        Returns:
            {"status": "ok", "has_background": bool}
        """
        if not self._sandbox:
            return {"status": "error", "error": "Sandbox not initialized"}
        
        if not self.is_alive():
            return {"status": "error", "error": "Sandbox not alive"}
        
        try:
            self._sandbox._input_queue.put({"action": "heartbeat"})
            result = self._sandbox._output_queue.get(timeout=10)
            return result
        except Exception as e:
            logger.warning("[ComponentSandbox] Heartbeat failed: %s", e)
            return {"status": "error", "error": str(e)}

    @classmethod
    def get_sandbox(cls, name: str) -> 'ComponentSandbox':
        """
        获取或创建沙箱实例
        
        Args:
            name: 组件名称
            
        Returns:
            ComponentSandbox 实例
        """
        with _sandbox_lock:
            _sandbox_last_used[name] = time.time()
            
            if name in _sandbox_instances:
                return _sandbox_instances[name]
            
            if len(_sandbox_instances) >= MAX_SANDBOX_COUNT:
                sorted_names = sorted(
                    _sandbox_last_used.keys(),
                    key=lambda n: _sandbox_last_used.get(n, 0)
                )
                
                for old_name in sorted_names:
                    if old_name != name and old_name in _sandbox_instances:
                        old_sandbox = _sandbox_instances[old_name]
                        if not old_sandbox.is_busy():
                            logger.info("[SandboxLRU] 淘汰旧沙箱: %s", old_name)
                            cls.remove_sandbox(old_name)
                            break
            
            _sandbox_instances[name] = cls(name)
            return _sandbox_instances[name]

    @classmethod
    def _get_existing_sandbox(cls, name: str) -> Optional['ComponentSandbox']:
        """
        获取已存在的沙箱实例（不会创建新的）
        
        Args:
            name: 组件名称
            
        Returns:
            ComponentSandbox 实例，如果不存在则返回 None
        """
        return _sandbox_instances.get(name)

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

    @classmethod
    def remove_sandbox(cls, name: str) -> bool:
        """
        删除沙箱实例（用于组件卸载时清理缓存）
        
        Args:
            name: 组件名称
            
        Returns:
            是否成功删除
        """
        with _sandbox_lock:
            if name in _sandbox_instances:
                sandbox = _sandbox_instances[name]
                sandbox.stop()
                del _sandbox_instances[name]
                # 同时清理使用时间记录
                _sandbox_last_used.pop(name, None)
                logger.info("[ComponentSandbox] 已删除沙箱实例: %s", name)
                return True
            return False

    @classmethod
    def get_all_sandbox_names(cls) -> list:
        """
        获取所有沙箱实例名称
        
        Returns:
            沙箱名称列表
        """
        return list(_sandbox_instances.keys())

    @classmethod
    def clear_all_sandboxes(cls):
        """清理所有沙箱实例"""
        with _sandbox_lock:
            for name in list(_sandbox_instances.keys()):
                sandbox = _sandbox_instances[name]
                sandbox.stop()
            _sandbox_instances.clear()
            _sandbox_last_used.clear()
            logger.info("[ComponentSandbox] 已清理所有沙箱实例")
            _stop_cleanup_thread()

    @classmethod
    def get_sandbox_stats(cls) -> Dict[str, Any]:
        """
        获取沙箱统计信息
        
        Returns:
            {
                "total": 沙箱总数,
                "max": 最大允许数量,
                "idle_timeout": 空闲超时秒数,
                "sandboxes": [
                    {"name": 名称, "alive": 是否存活, "last_used": 最后使用时间}
                ]
            }
        """
        with _sandbox_lock:
            sandboxes = []
            current_time = time.time()
            for name, sandbox in _sandbox_instances.items():
                last_used = _sandbox_last_used.get(name, 0)
                sandboxes.append({
                    "name": name,
                    "alive": sandbox.is_alive(),
                    "busy": sandbox.is_busy(),
                    "last_used": last_used,
                    "idle_seconds": int(current_time - last_used)
                })
            
            return {
                "total": len(_sandbox_instances),
                "max": MAX_SANDBOX_COUNT,
                "idle_timeout": IDLE_TIMEOUT_SECONDS,
                "sandboxes": sandboxes
            }

    def __del__(self):
        """析构时自动停止沙箱"""
        self.stop()

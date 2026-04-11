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

IPC 协议：JSON over stdin/stdout
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 沙箱超时（秒）
SANDBOX_TIMEOUT = 60

# 沙箱工作器脚本
SANDBOX_WORKER_SCRIPT = '''
import json
import sys
import traceback
import importlib.util
import os

# 项目根目录（由主进程传入）
_project_root = None

def load_component(module_path: str, class_name: str):
    """动态加载组件类"""
    # 确保项目根目录在 sys.path 中（在加载模块之前，因为模块顶层可能有 from app.xxx import）
    if _project_root and _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    spec = importlib.util.spec_from_file_location("sandbox_component", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)

def main():
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line)
            action = request.get("action")

            if action == "init":
                # 初始化组件
                global _project_root
                module_path = request["module_path"]
                class_name = request["class_name"]
                init_args = request.get("init_args", {})
                _project_root = request.get("project_root", "")

                # 设置项目根目录到 sys.path
                if _project_root and _project_root not in sys.path:
                    sys.path.insert(0, _project_root)

                global _component
                _component_class = load_component(module_path, class_name)
                _component = _component_class(**init_args)

                response = {"status": "ok", "cell_name": getattr(_component, "cell_name", "unknown")}

            elif action == "execute":
                # 执行命令
                command = request.get("command", "")
                args = request.get("args", [])
                kwargs = request.get("kwargs", {})

                result = _component.execute(command, *args, **kwargs)
                response = {"status": "ok", "result": result}

            elif action == "get_commands":
                # 获取命令列表
                commands = _component.get_commands()
                response = {"status": "ok", "commands": commands}

            elif action == "ping":
                response = {"status": "pong"}

            else:
                response = {"status": "error", "error": f"Unknown action: {action}"}

        except Exception as e:
            response = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc(),
            }

        sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()
'''


class SandboxProcess:
    """
    沙箱子进程管理器

    启动和管理一个独立的 Python 进程，用于执行组件代码。
    """

    def __init__(self, timeout: int = SANDBOX_TIMEOUT):
        """
        Args:
            timeout: 执行超时（秒）
        """
        self._timeout = timeout
        self._process: Optional[subprocess.Popen] = None
        self._initialized = False

    def start(self):
        """启动沙箱进程"""
        if self._process is not None:
            return

        # 创建临时工作器脚本
        self._worker_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        )
        self._worker_file.write(SANDBOX_WORKER_SCRIPT)
        self._worker_file.flush()
        self._worker_path = self._worker_file.name
        self._worker_file.close()

        # 启动子进程
        # 使用 utf-8 编码，忽略错误避免 Windows 控制台编码问题
        self._process = subprocess.Popen(
            [sys.executable, "-u", self._worker_path],  # -u: 无缓冲
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',  # 忽略编码错误
        )

        logger.debug("[Sandbox] Process started, pid=%s", self._process.pid)

    def stop(self):
        """停止沙箱进程"""
        if self._process is None:
            return

        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
        except Exception as e:
            logger.warning("[Sandbox] Error stopping process: %s", e)
        finally:
            self._process = None
            self._initialized = False

            # 清理临时文件
            try:
                os.unlink(self._worker_path)
            except Exception:
                pass

        logger.debug("[Sandbox] Process stopped")

    def _send(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """发送请求并接收响应"""
        if self._process is None:
            self.start()

        try:
            # 发送请求
            request_json = json.dumps(request, ensure_ascii=True)
            self._process.stdin.write(request_json + '\n')
            self._process.stdin.flush()

            # 接收响应
            response_line = self._process.stdout.readline()
            if not response_line:
                # 进程可能已退出
                stderr = self._process.stderr.read() if self._process.stderr else ""
                raise RuntimeError(f"Sandbox process closed unexpectedly. stderr: {stderr[:500]}")

            return json.loads(response_line)

        except json.JSONDecodeError as e:
            logger.error("[Sandbox] JSON decode error: %s", e)
            # 返回错误而不是抛出异常
            return {
                "status": "error",
                "error": "Sandbox communication error",
                "error_type": "SandboxError",
            }
        except Exception as e:
            logger.error("[Sandbox] IPC error: %s", e)
            # 重启进程
            self.stop()
            raise

    def init_component(self, module_path: str, class_name: str, init_args: Dict = None, project_root: str = None) -> str:
        """
        初始化组件

        Args:
            module_path: 组件模块文件路径
            class_name: 组件类名
            init_args: 初始化参数
            project_root: 项目根目录（用于沙箱中导入 app 模块）

        Returns:
            组件的 cell_name
        """
        if project_root is None:
            import os
            import sys

            def _find_project_root(start_path: str) -> str:
                """从给定路径向上搜索项目根目录（包含 app 目录）"""
                current = os.path.abspath(start_path)
                for _ in range(10):
                    if os.path.isdir(os.path.join(current, "app")):
                        return current
                    parent = os.path.dirname(current)
                    if parent == current:
                        break
                    current = parent
                return current

            project_root = _find_project_root(__file__)

        response = self._send({
            "action": "init",
            "module_path": module_path,
            "class_name": class_name,
            "init_args": init_args or {},
            "project_root": project_root,
        })

        if response.get("status") != "ok":
            raise RuntimeError(f"Failed to init component: {response.get('error')}")

        self._initialized = True
        return response.get("cell_name", "unknown")

    def execute(self, command: str = "", args: list = None, kwargs: dict = None) -> Dict[str, Any]:
        """
        执行组件命令

        Args:
            command: 命令名
            args: 位置参数
            kwargs: 关键字参数

        Returns:
            执行结果
        """
        if not self._initialized:
            raise RuntimeError("Component not initialized")

        response = self._send({
            "action": "execute",
            "command": command,
            "args": args or [],
            "kwargs": kwargs or {},
        })

        if response.get("status") != "ok":
            error = response.get("error", "Unknown error")
            error_type = response.get("error_type", "RuntimeError")
            traceback_str = response.get("traceback", "")

            return {
                "error": error,
                "error_type": error_type,
                "traceback": traceback_str,
                "status": "error",
            }

        return response.get("result", {})

    def get_commands(self) -> Dict[str, str]:
        """获取组件命令列表"""
        if not self._initialized:
            return {}

        response = self._send({"action": "get_commands"})

        if response.get("status") != "ok":
            return {}

        return response.get("commands", {})

    def ping(self) -> bool:
        """检查沙箱进程是否存活"""
        if self._process is None:
            return False

        try:
            response = self._send({"action": "ping"})
            return response.get("status") == "pong"
        except Exception:
            return False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


class ComponentSandbox:
    """
    组件沙箱管理器

    为组件创建和管理沙箱进程。支持组件的隔离执行。
    """

    _instances: Dict[str, SandboxProcess] = {}

    @classmethod
    def get_sandbox(cls, component_id: str, timeout: int = SANDBOX_TIMEOUT) -> SandboxProcess:
        """获取或创建沙箱实例"""
        if component_id not in cls._instances:
            cls._instances[component_id] = SandboxProcess(timeout=timeout)
        return cls._instances[component_id]

    @classmethod
    def release_sandbox(cls, component_id: str):
        """释放沙箱实例"""
        if component_id in cls._instances:
            cls._instances[component_id].stop()
            del cls._instances[component_id]

    @classmethod
    def release_all(cls):
        """释放所有沙箱实例"""
        for sandbox in list(cls._instances.values()):
            sandbox.stop()
        cls._instances.clear()

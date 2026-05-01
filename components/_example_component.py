# -*- coding: utf-8 -*-
"""
示例组件 — Agent 自扩展参考模板

【组件铁律示范】
  1. 继承 BaseCell
  2. cell_name = "xxx" （小写，用于命令路由）
  3. 命令方法以 _cmd_ 开头，必须有 docstring
  4. 文件放 components/ 下即自动生效

使用方式：
  - 前端/Agent 调用: cell.execute("命令名", *args)
  - 热加载: 放入目录 → 系统扫描发现 → 自动注册到 settings.yaml

复制此文件，改名为 your_tool.py 即可开始编写新组件。
"""

import threading
import time
from typing import Any, Dict
from app.core.interface.base_cell import BaseCell


class ExampleComponent(BaseCell):
    """
    示例组件 — 展示组件规范写法
    
    组件说明: 这是一个计算器示例，演示如何正确实现一个 Cellium 组件。
    """

    @property
    def cell_name(self) -> str:
        """组件标识（必须小写）— 用于全局唯一识别和命令路由"""
        return "calculator"

    # ================================================================
    # 命令方法（_cmd_ 前缀 + docstring 必须有）
    # ================================================================

    def _cmd_calc(self, expression: str) -> Dict[str, Any]:
        """
        安全计算数学表达式
        
        Args:
            expression: 数学表达式，如 "1+2*3"
            
        Returns:
            {"result": 数值结果, "expression": 原始表达式}
        
        使用: execute("calc", "2+3*4")
        """
        # 安全限制：只允许数字和基本运算符
        allowed = set("0123456789+-*/().% ")
        if not all(c in allowed for c in expression):
            return {"error": "表达式包含非法字符", "expression": expression}

        try:
            result = eval(expression)  # noqa: 已做字符过滤
            return {
                "result": result,
                "expression": expression,
                "type": type(result).__name__,
            }
        except Exception as e:
            return {"error": str(e), "expression": expression}

    def _cmd_echo(self, message: str, repeat: int = 1) -> Dict[str, Any]:
        """
        回显消息（可重复多次）
        
        Args:
            message: 要回显的文本
            repeat: 重复次数（默认1次）
        
        使用: execute("echo", "Hello") 或 execute("echo", "Hi", repeat=3)
        """
        result = (message + "\n") * repeat
        return {"output": result.strip(), "repeat": repeat}

    def _cmd_info(self) -> Dict[str, Any]:
        """
        返回组件自身信息（版本、能力列表等）
        
        使用: execute("info")
        """
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "class": self.__class__.__name__,
            "version": "1.0.0",
            "commands": commands,
            "command_count": len(commands),
        }

    # ================================================================
    # 生命周期钩子（可选）
    # ================================================================

    def on_load(self):
        """组件被加载后调用 — 可用于初始化资源、注册事件等"""
        super().on_load()
        print(f"[{self.cell_name}] 组件已加载就绪")

    def on_unload(self):
        """组件被卸载前调用 — 清理资源"""
        print(f"[{self.cell_name}] 组件正在卸载，清理资源...")


# ================================================================
# 后台运行组件示例（监控类组件模板）
# ================================================================

class BackgroundMonitorExample(BaseCell):
    """
    后台监控组件示例 — 展示如何创建持续运行的后台任务
    
    功能说明:
      - 组件加载时自动启动后台线程
      - 后台线程持续运行，可监控数据、定时检查等
      - Agent 可通过命令控制（启动/停止/查看状态）
      - 组件可主动触发 Agent 执行任务
    
    使用场景:
      - 实时监控（价格、日志、系统状态等）
      - 定时检查（服务健康检查、数据同步等）
      - 事件触发（检测到变化时通知 Agent）
    """

    def __init__(self):
        super().__init__()
        self._running = False
        self._thread = None
        self._counter = 0
        self._last_value = None
        self._target_sessions = []  # 目标 session 列表

    @property
    def cell_name(self) -> str:
        return "background_monitor_example"

    # ================================================================
    # 后台线程管理
    # ================================================================

    def on_load(self):
        """组件加载时自动启动后台任务"""
        super().on_load()
        self._start_background()

    def on_unload(self):
        """组件卸载时停止后台任务"""
        self._stop_background()

    def _start_background(self):
        """启动后台线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
        self.logger.info(f"[{self.cell_name}] 后台线程已启动")

    def _stop_background(self):
        """停止后台线程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.logger.info(f"[{self.cell_name}] 后台线程已停止")

    def _background_loop(self):
        """后台循环 — 在这里实现监控逻辑"""
        while self._running:
            try:
                # === 在这里实现你的监控逻辑 ===
                self._counter += 1
                
                # 示例：检测到变化时触发所有目标 session
                # if self._detect_change():
                #     for session_id in self._target_sessions:
                #         self._trigger_agent("检测到变化，请处理", session_id)
                
            except Exception as e:
                self.logger.error(f"[{self.cell_name}] 后台任务出错: {e}")
            
            time.sleep(60)  # 每60秒执行一次

    def _trigger_agent(self, message: str, session_id: str = None):
        """
        主动触发 Agent 执行任务
        
        推送消息到指定 session 的 Agent 对话，类似定时任务的机制。
        - 如果该 session 有运行中的任务，消息会追加到当前任务
        - 如果没有运行中的任务，会启动新任务
        
        Args:
            message: 要 Agent 处理的消息
            session_id: 目标会话 ID（必须指定，用于推送到正确的对话）
        """
        import httpx

        if not session_id:
            self.logger.warning(f"[{self.cell_name}] _trigger_agent 需要 session_id 参数")
            return {"success": False, "error": "session_id is required"}

        try:
            from app.core.util.agent_config import get_config
            cfg = get_config()
            host = cfg.get("server.host", "127.0.0.1")
            port = cfg.get("server.port", 18000)
            base_url = f"http://{host}:{port}"
        except Exception:
            base_url = "http://127.0.0.1:18000"

        try:
            response = httpx.post(
                f"{base_url}/api/component/event",
                json={
                    "session_id": session_id,
                    "message": message,
                    "source": self.cell_name,
                    "event_type": "background_trigger"
                },
                timeout=10.0
            )
            result = response.json()
            self.logger.info(f"[{self.cell_name}] 已触发 Agent | session={session_id} | status={result.get('status')}")
            return {"success": True, "result": result}
        except Exception as e:
            self.logger.error(f"[{self.cell_name}] 触发 Agent 失败: {e}")
            return {"success": False, "error": str(e)}

    # ================================================================
    # 命令方法（Agent 可调用）
    # ================================================================

    def _cmd_start(self) -> Dict[str, Any]:
        """
        启动后台监控任务
        
        Returns:
            {"success": True, "message": "监控已启动"}
        """
        self._start_background()
        return {"success": True, "message": "后台监控已启动", "running": self._running}

    def _cmd_stop(self) -> Dict[str, Any]:
        """
        停止后台监控任务
        
        Returns:
            {"success": True, "message": "监控已停止"}
        """
        self._stop_background()
        return {"success": True, "message": "后台监控已停止", "running": self._running}

    def _cmd_status(self) -> Dict[str, Any]:
        """
        获取后台监控状态
        
        Returns:
            {"running": bool, "counter": int, ...}
        """
        return {
            "running": self._running,
            "counter": self._counter,
            "last_value": self._last_value,
            "target_sessions": self._target_sessions,
            "thread_alive": self._thread.is_alive() if self._thread else False,
        }

    def _cmd_add_session(self, session_id: str = None) -> Dict[str, Any]:
        """
        添加目标 session（不传则使用当前对话）
        
        Args:
            session_id: 会话 ID（自动注入当前对话）
        """
        if session_id and session_id not in self._target_sessions:
            self._target_sessions.append(session_id)
        return {"success": True, "target_sessions": self._target_sessions}

    def _cmd_remove_session(self, session_id: str) -> Dict[str, Any]:
        """移除目标 session"""
        if session_id in self._target_sessions:
            self._target_sessions.remove(session_id)
        return {"success": True, "target_sessions": self._target_sessions}

    def _cmd_clear_sessions(self) -> Dict[str, Any]:
        """清空目标 session 列表"""
        self._target_sessions = []
        return {"success": True, "target_sessions": []}

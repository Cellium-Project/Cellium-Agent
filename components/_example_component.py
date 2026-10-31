# -*- coding: utf-8 -*-
"""
示例组件 — Agent 自扩展参考模板

【组件铁律】
  1. 继承 BaseCell
  2. cell_name = "xxx"（小写，全局唯一，用于命令路由）
  3. 命令方法以 _cmd_ 开头，必须有 docstring
  4. 文件名 = 类名 snake_case（MyComponent → my_component.py），放 components/ 下自动生效

【模板包含的能力】（不需要的整段删掉）
  - 命令方法：Agent 调用的入口
  - 沙箱状态接力：on_sandbox_save / on_sandbox_restore（有内存状态时实现）
  - 组件自管落盘：状态写 workspace/.cache/component_state/{cell_name}.json（主进程重启也不丢）
  - 后台线程：IS_BACKGROUND + 后台循环（沙箱模式自动心跳保活，不被空闲回收）
  - 主动触发 Agent：_trigger_agent HTTP 回调（后台检测到事件时上报）

"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict

from app.core.interface.base_cell import BaseCell


class ExampleComponent(BaseCell):
    """
    组件说明（第一行会被 Agent 看到，写清楚这个组件是干嘛的）

    示例：一个带状态持久化和后台任务的通用组件模板。
    """

    # ================================================================
    # 可选声明（按需保留）
    # ================================================================

    # 声明为后台组件：沙箱模式下启动心跳保活，空闲清理线程不会回收本沙箱
    IS_BACKGROUND = False

    @property
    def cell_name(self) -> str:
        return "calculator"

    def __init__(self):
        super().__init__()
        # 组件内存状态：全部收敛到一个 dict，方便接力与落盘
        self._counter = 0
        self._history: list = []

    # ================================================================
    # 命令方法（Agent 可调用）— _cmd_ 前缀 + docstring 必须有
    # ================================================================

    def _cmd_calc(self, input_data: str = "", **kwargs) -> Dict[str, Any]:
        """
        安全计算数学表达式

        Args:
            input_data: 数学表达式，如 "1+2*3"

        Returns:
            {"result": 数值结果, "expression": 原始表达式}

        使用: execute("calc", "2+3*4")
        """
        expression = input_data
        allowed = set("0123456789+-*/().% ")
        if not all(c in allowed for c in expression):
            return {"error": "表达式包含非法字符", "expression": expression}

        try:
            result = eval(expression)  # noqa: 已做字符过滤
            self._record(expression, result)
            return {"result": result, "expression": expression, "type": type(result).__name__}
        except Exception as e:
            return {"error": str(e), "expression": expression}

    def _cmd_echo(self, input_data: str = "", **kwargs) -> Dict[str, Any]:
        """
        回显消息

        Args:
            input_data: 要回显的文本
            **kwargs: 支持 repeat 指定重复次数

        使用: execute("echo", "Hello") 或 execute("echo", "Hi", repeat=3)
        """
        repeat = kwargs.get("repeat", 1)
        return {"output": (input_data + "\n") * repeat, "repeat": repeat}

    def _cmd_info(self) -> Dict[str, Any]:
        """
        返回组件自身信息（版本、命令列表等）

        使用: execute("info")
        """
        commands = self.get_commands()
        return {
            "name": self.cell_name,
            "class": self.__class__.__name__,
            "version": "1.0.0",
            "commands": commands,
            "command_count": len(commands),
            "state": self._state_snapshot(),
        }

    def _cmd_status(self) -> Dict[str, Any]:
        """
        查看组件内部状态

        使用: execute("status")
        """
        return {"running": self._running_flag(), "state": self._state_snapshot()}

    # ================================================================
    # 沙箱状态接力 — 框架在沙箱回收前调 save，重初始化后调 restore
    # （无内存状态的组件删掉这两段即可）
    # ================================================================

    def _state_snapshot(self) -> Dict[str, Any]:
        """导出当前状态（必须全部可 json/pickle 序列化）"""
        return {"counter": self._counter, "history": list(self._history)}

    def _cmd_on_sandbox_save(self, **kwargs) -> Dict[str, Any]:
        """沙箱回收前被框架调用，返回要保留的状态"""
        return {"success": True, "state": self._state_snapshot()}

    def _cmd_on_sandbox_restore(self, state=None, **kwargs) -> Dict[str, Any]:
        """沙箱重初始化后被框架调用，注入上次的状态"""
        if isinstance(state, dict):
            self._counter = state.get("counter", 0)
            self._history = list(state.get("history", []))
        return {"success": True, "restored": True}

    # ================================================================
    # 组件自管落盘 — 主进程重启也能恢复（可选，比沙箱接力覆盖更全）
    # 位置约定：workspace/.cache/component_state/{cell_name}.json，一组件一文件
    # ================================================================

    def _state_file(self) -> Path:
        return Path("workspace/.cache/component_state") / f"{self.cell_name}.json"

    def _load_state(self):
        try:
            data = json.loads(self._state_file().read_text("utf-8"))
            self._counter = data.get("counter", 0)
            self._history = list(data.get("history", []))
        except Exception:
            pass

    def _save_state(self):
        try:
            f = self._state_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(self._state_snapshot(), ensure_ascii=False), "utf-8")
        except Exception as e:
            self.logger.warning(f"[{self.cell_name}] 状态落盘失败: {e}")

    def _record(self, expression: str, result: Any):
        """状态变更后：更新内存 + 落盘"""
        self._counter += 1
        self._history.append({"expression": expression, "result": result})
        self._save_state()

    def _running_flag(self) -> bool:
        return getattr(self, "_running", False)

    # ================================================================
    # 生命周期钩子（可选）
    # ================================================================

    def on_load(self):
        super().on_load()
        self._load_state()  # 启动时恢复上次落盘的状态
        self.logger.info(f"[{self.cell_name}] 组件已加载就绪")

    def on_unload(self):
        self._save_state()
        self.logger.info(f"[{self.cell_name}] 组件正在卸载，清理资源...")


# ================================================================
# 后台任务组件模板 — 需要持续运行/定时轮询的组件照抄这段
# ================================================================

class BackgroundMonitorExample(BaseCell):
    """
    后台监控组件示例

    功能说明:
      - 组件加载时自动启动后台线程（沙箱模式自动心跳保活）
      - 后台线程持续运行，可监控数据、定时检查
      - 检测到事件时通过 _trigger_agent 主动触发 Agent 处理
      - Agent 可通过命令控制（启动/停止/查看状态）
    """

    # 后台组件必须声明：沙箱模式下心跳保活，防止被空闲回收
    IS_BACKGROUND = True

    def __init__(self):
        super().__init__()
        self._running = False
        self._thread = None
        self._counter = 0
        self._last_value = None
        self._target_sessions: list = []

    @property
    def cell_name(self) -> str:
        return "background_monitor_example"

    # ================================================================
    # 后台线程管理
    # ================================================================

    def on_load(self):
        super().on_load()
        self._start_background()

    def on_unload(self):
        self._stop_background()

    def _start_background(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
        self.logger.info(f"[{self.cell_name}] 后台线程已启动")

    def _stop_background(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.logger.info(f"[{self.cell_name}] 后台线程已停止")

    def _background_loop(self):
        """后台循环 — 在这里实现监控逻辑"""
        while self._running:
            try:
                self._counter += 1

                # 示例：检测到变化时触发所有目标 session
                # if self._detect_change():
                #     for session_id in self._target_sessions:
                #         self._trigger_agent("检测到变化，请处理", session_id)

            except Exception as e:
                self.logger.error(f"[{self.cell_name}] 后台任务出错: {e}")

            time.sleep(60)  # 每60秒执行一次

    # ================================================================
    # 主动触发 Agent（沙箱组件对主进程的唯一上报通道）
    # ================================================================

    def _trigger_agent(self, message: str, session_id: str = None, event_type: str = "background_trigger", event_data: dict = None):
        """
        主动触发 Agent 执行任务

        推送消息到指定 session 的 Agent 对话（走 /api/component/event HTTP 回调，
        与 napcat 等外部集成同一条入口）。

        Args:
            message: 要 Agent 处理的消息
            session_id: 目标会话 ID（必须指定）
            event_type: 事件类型标识（可选）
            event_data: 额外的事件数据（可选）
        """
        if not session_id:
            self.logger.warning(f"[{self.cell_name}] _trigger_agent 需要 session_id 参数")
            return {"success": False, "error": "session_id is required"}

        import httpx

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
                    "event_type": event_type,
                    "event_data": event_data or {},
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
        """启动后台监控任务"""
        self._start_background()
        return {"success": True, "message": "后台监控已启动", "running": self._running}

    def _cmd_stop(self) -> Dict[str, Any]:
        """停止后台监控任务"""
        self._stop_background()
        return {"success": True, "message": "后台监控已停止", "running": self._running}

    def _cmd_status(self) -> Dict[str, Any]:
        """获取后台监控状态"""
        return {
            "running": self._running,
            "counter": self._counter,
            "last_value": self._last_value,
            "target_sessions": self._target_sessions,
            "thread_alive": self._thread.is_alive() if self._thread else False,
        }

    def _cmd_add_session(self, session_id: str = None) -> Dict[str, Any]:
        """添加目标 session（不传则使用当前对话）"""
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

# -*- coding: utf-8 -*-
"""
组件热插拔监控器 — 后台实时监控 components/ 目录

能力：
  - 无需重启，文件放入/删除后 3 秒内自动生效
  - 后台守护线程，低开销（每 3 秒检查一次 mtime）
  - 自动加载新组件、自动卸载已删除的组件
  - 通过 file_system_event 总线事件通知其他模块
  - 支持优雅启停（start/stop）

用法：
    watcher = ComponentWatcher()
    watcher.start()     # 开始监控
    watcher.stop()      # 停止监控
    watcher.status()    # 查看状态
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.core.bus import event_bus
from app.core.di.container import get_container as get_di_container
from app.core.util.components_loader import (
    get_all_cells,
    discover_components,
    hot_reload,
    register_cell,
    unregister_cell,
)
from app.core.util.component_tool_registry import get_component_tool_registry

logger = logging.getLogger(__name__)


class ComponentWatcher:
    """
    组件热插拔监控器
    
    工作原理：
      1. 启动时记录当前目录快照（文件路径 → 修改时间）
      2. 每 interval 秒扫描一次目录
      3. 发现新文件或文件修改时间变化 → 触发热重载
      4. 发现文件被删除 → 自动卸载对应组件
    
    热插拔 = 写入 .py 文件到 components/ → 最多等 3 秒 → 组件可用
    """

    # 默认扫描间隔（秒）
    DEFAULT_INTERVAL = 3.0

    # 监控的目录
    COMPONENTS_DIR = None  # 延迟初始化，在 start() 时确定

    def __init__(
        self,
        interval: float = None,
        on_component_added: Callable[[str, str], None] = None,
        on_component_removed: Callable[[str], None] = None,
        auto_start: bool = False,
    ):
        """
        Args:
            interval: 扫描间隔（秒），默认 3.0
            on_component_added: 新组件加载回调 (cell_name, class_name)
            on_component_removed: 组件卸载回调 (cell_name)
            auto_start: 创建后是否立即启动
        """
        self._interval = interval or self.DEFAULT_INTERVAL
        self._on_added = on_component_added
        self._on_removed = on_component_removed

        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stop_event = threading.Event()

        # 文件状态缓存：{abs_path: mtime}
        self._file_snapshot: Dict[str, float] = {}

        # 统计信息
        self._stats = {
            "scan_count": 0,
            "total_added": 0,
            "total_removed": 0,
            "last_scan_time": None,
            "last_change_time": None,
            "started_at": None,
        }

        if auto_start:
            self.start()

    @property
    def is_running(self) -> bool:
        """监控器是否正在运行"""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> Dict[str, Any]:
        """获取运行统计"""
        return dict(self._stats)

    def start(self):
        """启动后台监控线程"""
        if self.is_running:
            logger.warning("[ComponentWatcher] 已在运行中，忽略重复启动")
            return

        from app.core.util.components_loader import get_components_dir

        if self.COMPONENTS_DIR is None:
            self.COMPONENTS_DIR = get_components_dir()

        self._stop_event.clear()
        self._running = True
        self._stats["started_at"] = time.time()

        # 初始快照
        self._take_snapshot()

        # ★ 启动时同步已有组件到工具注册表
        try:
            tool_registry = get_component_tool_registry()
            tool_registry.sync_from_components_loader()
            logger.info("[ComponentWatcher] 初始同步完成 → %d 个组件工具已注册", tool_registry.size)
        except Exception as e:
            logger.warning("[ComponentWatcher] 启动时组件工具同步失败: %s", e)

        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="ComponentWatcher",
            daemon=True,  # 主进程退出时自动结束
        )
        self._thread.start()

        file_count = len(self._file_snapshot)
        logger.info(
            "[ComponentWatcher] 已启动 | interval=%.1fs | 初始文件数=%d | dir=%s",
            self._interval,
            file_count,
            self.COMPONENTS_DIR,
        )

    def stop(self):
        """停止监控线程（等待当前扫描完成后退出）"""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        logger.info("[ComponentWatcher] 已停止 | 总扫描 %d 次", self._stats["scan_count"])

    def reload_now(self) -> Dict[str, Any]:
        """立即执行一次热重载（手动触发）"""
        try:
            container = get_di_container()
        except Exception as e:
            logger.debug("[ComponentWatcher] 获取 DI 容器失败: %s", e)
            container = None

        report = hot_reload(container=container)

        # ★ 关键：同步到组件工具注册表，让 AgentLoop 能立即使用新组件
        try:
            tool_registry = get_component_tool_registry()
            tool_registry.sync_from_components_loader()
            report["_tool_registry_synced"] = True
        except Exception as e:
            logger.warning("[ComponentWatcher] 工具注册表同步失败: %s", e)
            report["_tool_registry_synced"] = False

        # 更新快照
        self._take_snapshot()
        self._stats["last_change_time"] = time.time()

        added = report.get("added", [])
        removed = report.get("removed", [])

        for item in added:
            self._stats["total_added"] += 1
            if self._on_added:
                try:
                    self._on_added(item["name"], item.get("class", ""))
                except Exception as cb_e:
                    logger.warning("[ComponentWatcher] 添加回调失败: %s", cb_e)

        for item in removed:
            self._stats["total_removed"] += 1
            if self._on_removed:
                try:
                    self._on_removed(item["name"])
                except Exception as cb_e:
                    logger.warning("[ComponentWatcher] 移除回调失败: %s", cb_e)

        return report

    def status(self) -> Dict[str, Any]:
        """获取监控器完整状态"""
        tool_info = {}
        try:
            reg = get_component_tool_registry()
            tool_info = {
                "tool_count": reg.size,
                "tools": list(reg.get_all_names()),
            }
        except Exception as e:
            logger.debug("[ComponentWatcher] 获取工具注册表状态失败: %s", e)

        return {
            "running": self.is_running,
            "interval": self._interval,
            "components_dir": str(self.COMPONENTS_DIR) if self.COMPONENTS_DIR else None,
            "watched_files": len(self._file_snapshot),
            "loaded_components": len(get_all_cells()),
            "stats": dict(self._stats),
            "thread_alive": self._thread.is_alive() if self._thread else False,
            **tool_info,  # 工具注册表信息
        }

    # ================================================================
    # 内部方法
    # ================================================================

    def _monitor_loop(self):
        """后台监控主循环"""
        while self._running and not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as e:
                logger.error("[ComponentWatcher] 扫描异常: %s", e, exc_info=True)

            # 等待下一次扫描（可被 stop 中断）
            self._stop_event.wait(timeout=self._interval)

        logger.debug("[ComponentWatcher] 监控循环退出")

    def _scan_once(self):
        """执行一次目录扫描，检测变化"""
        self._stats["scan_count"] += 1
        self._stats["last_scan_time"] = time.time()

        current_files = set()

        if not self.COMPONENTS_DIR or not self.COMPONENTS_DIR.exists():
            return

        # 遍历当前目录中的 .py 文件
        for py_file in sorted(self.COMPONENTS_DIR.glob("*.py")):
            # 跳过特殊文件
            if py_file.name.startswith("_") and py_file.name != "__init__.py":
                continue
            if py_file.name == "__init__.py":
                continue

            abs_path = str(py_file.resolve())
            current_files.add(abs_path)

            try:
                current_mtime = os.path.getmtime(abs_path)
            except OSError:
                continue

            cached_mtime = self._file_snapshot.get(abs_path)

            # 新文件 或 文件被修改
            if cached_mtime is None or abs(current_mtime - cached_mtime) > 0.1:
                logger.info(
                    "[ComponentWatcher] 检测到变化: %s (new=%s, modified=%s)",
                    py_file.name,
                    cached_mtime is None,
                    cached_mtime is not None,
                )

                # 触发热重载
                self.reload_now()
                return  # reload_now 内部会更新快照，直接返回避免重复处理

        # 检测被删除的文件
        deleted_files = set(self._file_snapshot.keys()) - current_files
        if deleted_files:
            logger.info(
                "[ComponentWatcher] 检测到 %d 个文件被删除: %s",
                len(deleted_files),
                [os.path.basename(f) for f in deleted_files],
            )
            self.reload_now()

    def _take_snapshot(self):
        """拍摄当前目录文件状态快照"""
        self._file_snapshot.clear()

        if not self.COMPONENTS_DIR or not self.COMPONENTS_DIR.exists():
            return

        for py_file in self.COMPONENTS_DIR.glob("*.py"):
            if py_file.name.startswith("_") and py_file.name != "__init__.py":
                continue
            if py_file.name == "__init__.py":
                continue

            try:
                abs_path = str(py_file.resolve())
                self._file_snapshot[abs_path] = os.path.getmtime(abs_path)
            except OSError:
                pass


# ================================================================
# 全局单例
# ================================================================

_global_watcher: Optional[ComponentWatcher] = None


def get_watcher() -> ComponentWatcher:
    """获取全局监控器单例（不存在则创建但不启动）"""
    global _global_watcher
    if _global_watcher is None:
        _global_watcher = ComponentWatcher(auto_start=False)
    return _global_watcher


def start_watching(interval: float = 3.0) -> ComponentWatcher:
    """启动全局监控器（幂等：已运行则直接返回）"""
    w = get_watcher()
    if not w.is_running:
        w._interval = interval
        w.start()
    return w


def stop_watching():
    """停止全局监控器"""
    global _global_watcher
    if _global_watcher:
        _global_watcher.stop()

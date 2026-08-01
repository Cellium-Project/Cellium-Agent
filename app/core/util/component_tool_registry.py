# -*- coding: utf-8 -*-
"""
ComponentToolRegistry — 组件工具全局注册表（线程安全）

核心职责：
  1. 将 BaseCell 组件自动包装为 CellToolAdapter（BaseTool 子类）
  2. 线程安全地管理注册/注销/查询操作
  3. 为 AgentLoop 提供「组件工具」的动态读取接口
  4. 与 ComponentWatcher 联动：热插拔时自动更新

架构：
    components/xxx.py (BaseCell)
        ↓ 热插拔检测
    ComponentWatcher 检测到变化
        ↓ 调用
    ComponentToolRegistry.register(cell_instance) / unregister(name)
        ↓ 包装为
    CellToolAdapter (BaseTool)
        ↓ 存入
    _registry: Dict[str, CellToolAdapter]  （线程安全）
        ↓ AgentLoop 运行时动态读取
    get_all_tools() / get_tool_definitions() / get_tool(name)

线程安全保证：
    - 所有写操作加 _lock 保护
    - 读操作返回副本，避免并发修改问题
    - 单例模式，全系统共享同一个注册表

内置工具保护：
    - "shell", "memory", "file" 为系统保留名，不可被组件覆盖
    - 组件只能注册自己的 cell_name 作为 tool_name
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Set

from app.core.interface.icell import ICell
from app.core.util.cell_tool_adapter import CellToolAdapter
from app.core.util.components_loader import get_all_cells, get_cell

logger = logging.getLogger(__name__)


class ComponentToolRegistry:
    """
    组件工具注册表 — 管理所有从 BaseCell 衍生的工具
    
    使用方式：
        # 注册组件（通常由 ComponentWatcher 或 load_components 自动调用）
        registry = get_component_tool_registry()
        
        # 注册单个组件
        registry.register(my_cell_instance)          → CellToolAdapter
        
        # 注销组件  
        registry.unregister("mytool")                 → True/False
        
        # AgentLoop 运行时调用（每次对话都重新读取最新状态）
        registry.get_all_tools()                      → {"shell": ShellTool, "mytool": CellToolAdapter, ...}
        registry.get_component_tools()                → {"mytool": CellToolAdapter, ...}（仅组件工具）
        registry.get_tool_definitions()               → [definition_dict, ...]  LLM 格式
    """

    #系统保留的工具名 — 组件不能覆盖这些（默认内置工具）
    RESERVED_TOOL_NAMES: Set[str] = {
        "shell",      # ShellTool — 系统命令执行
        "memory",     # MemoryTool — 记忆管理
        "file",       # FileTool — 文件操作
    }

    def __init__(self):
        self._lock: threading.RLock = threading.RLock()
        # {tool_name: CellToolAdapter} — 仅存放组件衍生的工具
        self._registry: Dict[str, CellToolAdapter] = {}
        # 变更计数器（用于快速判断是否有更新）
        self._version: int = 0

    @property
    def version(self) -> int:
        """当前版本号（每变更+1）"""
        with self._lock:
            return self._version

    @property
    def size(self) -> int:
        """已注册的组件工具数量"""
        with self._lock:
            return len(self._registry)

    def register(self, cell: ICell, force: bool = False) -> Optional[CellToolAdapter]:
        """
        注册一个组件到工具注册表

        Args:
            cell: 已实例化的 BaseCell 子类
            force: 强制重新审计（热重载时用）

        Returns:
            创建好的 CellToolAdapter 实例，或 None（如果名称冲突）
        """
        if not isinstance(cell, ICell):
            logger.warning("[ComponentToolRegistry] 忽略非 ICell 对象: %s", type(cell).__name__)
            return None

        adapter = CellToolAdapter(cell)
        tool_name = adapter.tool_name

        if not force:
            with self._lock:
                existing = self._registry.get(tool_name)
            if existing is not None and existing.cell is cell:
                return existing

        from app.core.util.component_auditor import get_auditor
        audit_result = get_auditor().audit(cell)

        with self._lock:
            if tool_name in self.RESERVED_TOOL_NAMES:
                logger.warning(
                    "[ComponentToolRegistry] '%s' 是系统保留名，组件 %s 无法注册",
                    tool_name, adapter.component_type,
                )
                return None

            adapter._audit_issues = audit_result.issues
            adapter._audit_warnings = audit_result.warnings
            adapter._audit_score = audit_result.score
            adapter._audit_hint_text = audit_result.hint_text if audit_result.issues else ""

            if audit_result.issues:
                logger.warning(
                    "[ComponentToolRegistry] [注册但有警告] %s (type=%s) | score=%d | issues=%d",
                    tool_name, adapter.component_type,
                    audit_result.score, audit_result.issue_count,
                )

            existed = tool_name in self._registry
            self._registry[tool_name] = adapter
            self._version += 1

            cmds = list(adapter.get_commands().keys())
            logger.info(
                "[ComponentToolRegistry] [%s] %s (type=%s, commands=%s)",
                "更新" if existed else "注册", tool_name, adapter.component_type, cmds,
            )

            self._on_tool_registered(tool_name, adapter, is_new=not existed)

        return adapter

    def unregister(self, tool_name: str) -> bool:
        """
        从注册表移除一个组件工具
        
        Args:
            tool_name: 要移除的工具名（= cell_name）
            
        Returns:
            是否成功移除
        """
        with self._lock:
            if tool_name not in self._registry:
                return False

            adapter = self._registry.pop(tool_name)
            self._version += 1

            logger.info(
                "[ComponentToolRegistry] [卸载OK] %s (type=%s)",
                tool_name, adapter.component_type,
            )
            self._on_tool_unregistered(tool_name, adapter)
            return True

    def get(self, name: str) -> Optional[CellToolAdapter]:
        """获取指定工具的适配器实例"""
        with self._lock:
            return self._registry.get(name)

    def has(self, name: str) -> bool:
        """检查是否已注册指定工具"""
        with self._lock:
            return name in self._registry

    def get_all_names(self) -> List[str]:
        """获取所有已注册的组件工具名称列表"""
        with self._lock:
            return list(self._registry.keys())

    def get_all_adapters(self) -> Dict[str, CellToolAdapter]:
        """
        获取所有组件工具适配器（返回副本，线程安全）
        
        Returns:
            {tool_name: CellToolAdapter}
        """
        with self._lock:
            return dict(self._registry)

    def get_component_tools(self) -> Dict[str, Any]:
        """
        获取纯组件工具字典（可直接合并到 AgentLoop.tools）
        
        Returns:
            {tool_name: CellToolAdapter}
        """
        return self.get_all_adapters()

    def get_tool_definitions(self) -> List[Dict]:
        """
        获取所有组件工具的 LLM 定义列表
        
        Returns:
            [function_calling_definition_dict, ...]
        """
        definitions = []
        for adapter in self.get_all_adapters().values():
            try:
                definitions.append(adapter.definition)
            except Exception as e:
                logger.error(
                    "[ComponentToolRegistry] 获取 %s definition 失败: %s",
                    adapter.tool_name, e,
                )
        return definitions

    def sync_from_components_loader(self):
        """
        从 components_loader 的全局注册表同步所有组件
        
        在启动时和热重载后调用，确保注册表与实际加载的组件一致。
        """
        cells = get_all_cells()
        synced_count = 0
        
        for cell_name, cell_instance in cells.items():
            try:
                result = self.register(cell_instance)
                if result:
                    synced_count += 1
            except Exception as e:
                logger.error(
                    "[ComponentToolRegistry] 同步组件 %s 失败: %s",
                    cell_name, e,
                )

        current_names = set(cells.keys())
        registered_names = set(self.get_all_names())
        orphaned = registered_names - current_names
        
        for orphan in orphaned:
            self.unregister(orphan)

        logger.info(
            "[ComponentToolRegistry] 同步完成 | 同步=%d | 清理孤儿=%d | 总计=%d",
            synced_count, len(orphaned), self.size,
        )

    def clear(self):
        """清空全部组件工具"""
        with self._lock:
            old_size = len(self._registry)
            self._registry.clear()
            self._version += 1
            logger.info(
                "[ComponentToolRegistry] 已清空 | 移除 %d 个组件工具",
                old_size,
            )

    def status(self) -> Dict[str, Any]:
        """获取注册表完整状态"""
        with self._lock:
            tools_info = {}
            for name, adapter in self._registry.items():
                tools_info[name] = {
                    "component_type": adapter.component_type,
                    "commands": list(adapter.get_commands().keys()),
                }

            return {
                "total": len(self._registry),
                "version": self._version,
                "tools": tools_info,
                "reserved_names": sorted(self.RESERVED_TOOL_NAMES),
            }

    def _on_tool_registered(self, name: str, adapter: CellToolAdapter, is_new: bool):
        """工具注册后回调（子类可覆盖以实现事件通知）"""
        ...

    def _on_tool_unregistered(self, name: str, adapter: CellToolAdapter):
        """工具卸载后回调（子类可覆盖）"""
        if hasattr(adapter, '_stop_heartbeat'):
            try:
                adapter._stop_heartbeat()
                logger.debug(f"[ComponentToolRegistry] 已停止 {name} 的心跳线程")
            except Exception as e:
                logger.warning(f"[ComponentToolRegistry] 停止 {name} 心跳线程失败: {e}")


# ================================================================
# 全局单例
# ================================================================

_global_registry: Optional[ComponentToolRegistry] = None
_singleton_lock = threading.Lock()


def get_component_tool_registry() -> ComponentToolRegistry:
    """
    获取全局组件工具注册表单例（线程安全懒初始化）
    
    全系统唯一入口。AgentLoop、Watcher、API 都通过此函数获取同一份注册表。
    """
    global _global_registry
    if _global_registry is None:
        with _singleton_lock:
            # Double-check locking
            if _global_registry is None:
                _global_registry = ComponentToolRegistry()
                logger.info("[ComponentToolRegistry] 全局单例已初始化")
    return _global_registry


def reset_component_tool_registry():
    """重置全局单例（仅测试用）"""
    global _global_registry
    with _singleton_lock:
        _global_registry = None

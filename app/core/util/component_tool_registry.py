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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

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

    # 审查结果缓存（不合规组件的修复建议，供 AgentLoop 注入给 LLM）
    _audit_hints: Dict[str, str] = {}   # {tool_name: hint_text}

    # 用户审批队列（危险导入的组件等待用户 /trust 确认）
    # {tool_name: {"cell": BaseCell, "adapter": CellToolAdapter, "audit_result": AuditResult, "requested_at": str}}
    _pending_approvals: Dict[str, Dict[str, Any]] = {}

    @property
    def pending_count(self) -> int:
        """待用户确认信任的组件数量"""
        with self._lock:
            return len(self._pending_approvals)

    # ================================================================
    #  用户信任白名单管理（持久化到文件）
    # ================================================================

    @staticmethod
    def _get_trust_list_path() -> Path:
        """
        获取信任白名单文件路径

        使用 AgentConfig 统一管理路径，避免硬编码
        """
        from app.core.util.agent_config import get_config
        return get_config().config_root / "trusted_components.json"

    def _load_trust_list(self) -> Set[str]:
        """加载用户已信任的组件名称集合"""
        path = self._get_trust_list_path()
        if path.exists():
            try:
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return set(data.get("trusted_names", []))
            except Exception as e:
                logger.warning("[ComponentToolRegistry] 加载信任白名单失败: %s", e)
        return set()

    def _save_trust_list(self, trusted: Set[str]) -> None:
        """保存信任白名单"""
        path = self._get_trust_list_path()
        try:
            import json
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"trusted_names": sorted(trusted)}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("[ComponentToolRegistry] 保存信任白名单失败: %s", e)

    def is_trusted(self, tool_name: str) -> bool:
        """检查组件是否在用户信任白名单中"""
        return tool_name in self._load_trust_list()

    def untrust(self, tool_name: str) -> bool:
        """
        从信任白名单中移除组件
        
        Args:
            tool_name: 要移除的组件名称
            
        Returns:
            是否成功移除
        """
        trusted = self._load_trust_list()
        if tool_name in trusted:
            trusted.discard(tool_name)
            self._save_trust_list(trusted)
            logger.info("[ComponentToolRegistry] 已从信任白名单移除: %s", tool_name)
            return True
        return False

    def cleanup_trust_list(self, valid_tool_names: Set[str]) -> List[str]:
        trusted = self._load_trust_list()
        removed = []
        for name in list(trusted):
            if name not in valid_tool_names:
                trusted.discard(name)
                removed.append(name)
        if removed:
            self._save_trust_list(trusted)
            logger.info("[ComponentToolRegistry] 已清理信任白名单中的 %d 个无效组件: %s", len(removed), removed)
        return removed

    def trust(self, tool_name: str) -> Dict[str, Any]:
        """
        用户执行 /trust 命令，将组件加入信任白名单并立即注册
        
        Args:
            tool_name: 要信任的组件名称
            
        Returns:
            操作结果字典
        """
        from datetime import datetime

        with self._lock:
            if tool_name not in self._pending_approvals:
                if tool_name in self._registry:
                    return {
                        "success": False,
                        "message": f"'{tool_name}' 已经注册为工具，无需再次信任",
                        "status": "already_registered",
                    }
                return {
                    "success": False,
                    "message": f"'{tool_name}' 不在待审批队列中。可用的待审批组件: {list(self._pending_approvals.keys())}",
                    "status": "not_pending",
                }

            pending = self._pending_approvals.pop(tool_name)
            cell = pending["cell"]
            adapter = pending["adapter"]

            trusted = self._load_trust_list()
            trusted.add(tool_name)
            self._save_trust_list(trusted)

            self._audit_hints.pop(tool_name, None)

            existed = tool_name in self._registry
            self._registry[tool_name] = adapter
            self._version += 1

            cmds = list(adapter.get_commands().keys())
            logger.info(
                "[ComponentToolRegistry] [信任通过] %s (type=%s, commands=%s)",
                tool_name, adapter.component_type, cmds,
            )

            return {
                "success": True,
                "message": f"已信任并注册 '{tool_name}'，该组件现在可作为 LLM 工具使用",
                "tool_name": tool_name,
                "component_type": adapter.component_type,
                "commands": cmds,
                "action": "registered" if not existed else "updated",
                "trusted_at": datetime.now().isoformat(),
            }

    def get_pending_approvals(self) -> Dict[str, Any]:
        """
        获取所有待用户审批的组件详情
        
        Returns:
            {total: N, items: [{tool_name, component_type, issues, hint_summary}]}
        """
        with self._lock:
            items = []
            for tname, info in self._pending_approvals.items():
                audit = info.get("audit_result")
                issues = audit.issues if audit else []
                danger_issues = [i for i in issues if i.get("rule") == "no_dangerous_imports"]
                imports_found = [i["message"] for i in danger_issues]

                adapter = info.get("adapter")
                cell = info.get("cell")

                items.append({
                    "tool_name": tname,
                    "component_type": (
                        getattr(adapter, "component_type", None)
                        or (type(cell).__name__ if cell else "Unknown")
                    ),
                    "issues": issues,
                    "issue_count": len(issues),
                    "critical_count": sum(1 for i in issues if i.get("severity") == "critical"),
                    "danger_imports": imports_found,
                    "hint_summary": (audit.hint_text[:500] if audit else "") + ("..." if audit and len(audit.hint_text) > 500 else ""),
                    "requested_at": info.get("requested_at", ""),
                    "trust_command": f"/trust {tname}",
                })

            return {
                "total": len(items),
                "items": items,
                "note": (
                    "以上组件因包含危险导入(os/subprocess 等)，需要用户手动确认信任后才能注册为 LLM 工具。\n"
                    '请在聊天框输入 /trust <组件名> 来信任该组件，例如: /trust skill_installer'
                ),
            }

    def register(self, cell: ICell) -> Optional[CellToolAdapter]:
        """
        注册一个组件到工具注册表
        
        Args:
            cell: 已实例化的 BaseCell 子类
            
        Returns:
            创建好的 CellToolAdapter 实例，或 None（如果不合规或名称冲突）
            
        审查流程：
          1. 类型检查：必须是 ICell 子类
          2. 合规审计：ComponentAuditor 检查所有规范
          3. 名称保护：不覆盖系统保留名
          4. 危险导入 → 进入待审批队列，等用户 /trust
          5. 审查通过 → 存入注册表
        """
        if not isinstance(cell, ICell):
            logger.warning("[ComponentToolRegistry] 忽略非 ICell 对象: %s", type(cell).__name__)
            return None

        adapter = CellToolAdapter(cell)
        tool_name = adapter.tool_name

        from app.core.util.component_auditor import get_auditor
        audit_result = get_auditor().audit(cell)

        has_danger = any(i.get("rule") == "no_dangerous_imports" for i in audit_result.issues)
        if has_danger and not self.is_trusted(tool_name):
            from datetime import datetime
            with self._lock:
                self._pending_approvals[tool_name] = {
                    "cell": cell,
                    "adapter": adapter,
                    "audit_result": audit_result,
                    "requested_at": datetime.now().isoformat(),
                }

            logger.warning(
                "[ComponentToolRegistry] [待审批] %s (type=%s) | score=%d | 包含危险导入，等待用户 /trust",
                tool_name, adapter.component_type, audit_result.score,
            )
            self._audit_hints[tool_name] = self._format_approval_request_hint(tool_name, audit_result)
            return None

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
                self._audit_hints[tool_name] = audit_result.hint_text
                logger.warning(
                    "[ComponentToolRegistry] [注册但有警告] %s (type=%s) | score=%d | issues=%d | 已存储修复建议",
                    tool_name, adapter.component_type,
                    audit_result.score, audit_result.issue_count,
                )
            else:
                if tool_name in self._audit_hints:
                    self._audit_hints.pop(tool_name, None)
                    logger.info(
                        "[ComponentToolRegistry] [修复完成] %s (type=%s, score=%d)",
                        tool_name, adapter.component_type, audit_result.score,
                    )

            existed = tool_name in self._registry
            self._registry[tool_name] = adapter
            self._version += 1

            action = "更新" if existed else "注册"
            cmds = list(adapter.get_commands().keys())
            
            self._on_tool_registered(tool_name, adapter, is_new=not existed)

        return adapter

    def get_audit_hint(self, tool_name: str) -> str:
        """获取指定工具的审查修复建议文本"""
        return self._audit_hints.get(tool_name, "")

    def get_all_audit_hints(self) -> Dict[str, str]:
        """获取所有未通过审查的工具的修复建议"""
        return dict(self._audit_hints)

    def clear_audit_hint(self, tool_name: str):
        """清除指定工具的审查缓存"""
        self._audit_hints.pop(tool_name, None)

    @staticmethod
    def _format_approval_request_hint(tool_name: str, audit_result) -> str:
        danger_issues = [i for i in audit_result.issues if i.get("rule") == "no_dangerous_imports"]
        imports_list = []
        for issue in danger_issues:
            msg = issue.get("message", "")
            if "import " in msg:
                imp = msg[msg.index("import "):]
                imports_list.append(f"  - `{imp}`")

        return (
            f"### 组件安全审批请求\n\n"
            f"**组件 `{tool_name}` 因包含以下危险导入，无法自动注册为 LLM 工具：**\n\n"
            f"\n".join(imports_list) + "\n\n"
            f"**需要用户手动确认是否信任此组件。请将以下信息原样转告用户：**\n\n"
            f"> 组件 **{tool_name}** 包含危险模块导入（如 os/subprocess 等）。\n"
            f"> 这可能带来安全风险，但该组件是系统内置组件，通常是安全的。\n"
            f">\n"
            f"> 如果您信任此组件并希望启用它，请在聊天框中输入：\n"
            f"> `/trust {tool_name}`\n"
            f">\n"
            f"> 输入后组件将立即注册为可用工具。"
        )

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
        ...


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

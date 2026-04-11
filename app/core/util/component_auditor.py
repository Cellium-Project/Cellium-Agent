# -*- coding: utf-8 -*-
"""
ComponentAuditor — 组件合规性审查器

在组件注册到 ComponentToolRegistry 之前，自动检查是否满足所有规范要求。
不合规的组件将被拒绝注册，并返回具体的修复建议（可注入给 LLM）。

审查项目：
  1. cell_name 合规性：小写、非空、不含特殊字符
  2. 命令方法规范：必须以 _cmd_ 前缀开头
  3. docstring 完整性：每个命令必须有描述性的文档字符串
  4. 返回值格式：命令方法应返回 dict 类型
  5. 命名冲突：不能与内置工具或已注册组件重名
  6. 安全检查：不允许 import os/sys/subprocess 等危险模块
  7. _cmd_help 方法：推荐有（用于 LLM 自学习用法）

用法：
    auditor = ComponentAuditor()
    result = auditor.audit(cell_instance)
    
    if result.passed:
        registry.register(cell_instance)   # ✅ 通过
    else:
        # ❌ 不通过 → 将 result.hint_text 注入给 LLM 让它修复
        print(result.hint_text)
"""

import ast
import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)


@dataclass
class AuditResult:
    """单次审查结果"""
    passed: bool                          # 是否全部通过
    component_name: str                   # 组件标识
    component_type: str                   # 类名
    issues: List[Dict[str, Any]] = field(default_factory=list)   # 问题列表
    warnings: List[str] = field(default_factory=list)           # 警告列表（不影响通过）
    score: int = 0                        # 评分 (0-100)

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def has_critical(self) -> bool:
        """是否有严重问题（必须修复才能注册）"""
        return any(i.get("severity") == "critical" for i in self.issues)

    @property
    def hint_text(self) -> str:
        """
        生成给 LLM 的修复建议文本
        
        当组件不合规时，将此文本注入给 LLM，
        让它知道自己创建的组件哪里不对、如何修改。
        """
        if self.passed:
            return ""

        lines = [
            f"## ⚠️ 组件 `{self.component_name}` 注册被拒绝",
            "",
            f"**类型**: {self.component_type}",
            f"**问题数**: {self.issue_count} 个（{len([i for i in self.issues if i['severity']=='critical'])} 个严重）",
            f"**评分**: {self.score}/100",
            "",
        ]

        # 按严重程度分组显示
        critical = [i for i in self.issues if i["severity"] == "critical"]
        error = [i for i in self.issues if i["severity"] == "error"]

        if critical:
            lines.append("### 🔴 必须修复的问题")
            lines.append("")
            for idx, issue in enumerate(critical, 1):
                lines.append(f"{idx}. **[{issue['rule']}]** {issue['message']}")
                fix = issue.get("fix", "")
                if fix:
                    lines.append(f"   🔧 修复方法: {fix}")
                example = issue.get("example", "")
                if example:
                    lines.append(f"   📝 示例代码:")
                    lines.append(f"```python")
                    lines.append(example.strip())
                    lines.append(f"```")
                lines.append("")

        if error:
            lines.append("### 🟡 建议修复的问题")
            lines.append("")
            for idx, issue in enumerate(error, 1):
                lines.append(f"{idx}. **[{issue['rule']}]** {issue['message']}")
                fix = issue.get("fix", "")
                if fix:
                    lines.append(f"   建议: {fix}")
                lines.append("")

        if self.warnings:
            lines.append("### ⚪ 提示信息")
            lines.append("")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        lines.append("---")
        lines.append(
            "请使用 `file write` 或 `file edit` 修改组件文件解决以上问题，"
            "然后调用 `component.reload()` 重新加载。"
        )

        return "\n".join(lines)


# ============================================================
# 危险导入黑名单（安全审计）
# ============================================================
# 分级安全策略：
# - BANNED_IMPORTS：完全禁止（动态代码执行，无法安全使用）
# - PROTECTED_IMPORTS：允许导入但方法受限（运行时拦截）

from app.core.util.protected_modules import (
    BANNED_IMPORTS,
    PROTECTED_IMPORTS,
    is_banned_module,
    is_protected_module,
)

# 合并所有危险导入（用于兼容旧逻辑）
DANGEROUS_IMPORTS: Set[str] = BANNED_IMPORTS | PROTECTED_IMPORTS

DANGEROUS_FROM_IMPORTS: Set[str] = {
    "builtins.exec", "builtins.eval",
}


class ComponentAuditor:
    """
    组件合规性审查器
    
    在组件加载/热插拔时自动调用，
    确保只有符合规范的组件才能注册为工具。
    
    ★ 白名单机制：
        系统内置组件（如 component）在 EXEMPTED_NAMES 中，
        豁免部分严格规则（保留名检查、危险导入、help方法要求）。
        但仍检查基本规范（有命令、类docstring），确保可用性。
    """

    # ★ 系统内置组件白名单 — 豁免部分 strict 规则
    EXEMPTED_NAMES: Set[str] = {
        "component",        # ComponentBuilder — 系统核心组件，需要 import os/json
        "skill_installer",  # SkillInstaller — Skill 包管理器，需要 import os 操作文件系统
        "web_query",        # WebQuery — 网络请求组件
    }

    # 审查规则配置
    RULES = {
        "cell_name": {
            "severity": "critical",
            "description": "cell_name 必须是小写英文字母+下划线",
            "pattern": r"^[a-z][a-z0-9_]*$",
        },
        "has_commands": {
            "severity": "critical",
            "description": "至少需要一个 _cmd_ 方法",
        },
        "docstring": {
            "severity": "error",
            "description": "每个 _cmd_ 方法必须有 docstring",
        },
        "return_dict": {
            "severity": "error",
            "description": "命令方法应返回 dict 类型",
        },
        "has_help": {
            "severity": "warning",
            "description": "推荐提供 _cmd_help 方法供 LLM 查询用法",
        },
        "no_dangerous_imports": {
            "severity": "critical",
            "description": "禁止导入危险模块 (os/subprocess 等)",
        },
        "class_docstring": {
            "severity": "error",
            "description": "类必须有 docstring 描述功能",
        },
    }

    def __init__(self, strict_mode: bool = False):
        """
        Args:
            strict_mode: 严格模式 — warning 也视为不通过
        """
        self._strict = strict_mode

    def audit(self, cell: BaseCell) -> AuditResult:
        """
        对组件实例执行全面审查
        
        Args:
            cell: 待审查的 BaseCell 子类实例
            
        Returns:
            AuditResult 审查结果
        """
        cell_type = type(cell).__name__
        try:
            cell_name = cell.cell_name
        except Exception:
            cell_name = ""

        # ★ 白名单检查：系统内置组件豁免部分 strict 规则
        is_exempted = cell_name in self.EXEMPTED_NAMES

        issues = []
        warnings = []
        score = 100  # 起始满分，每项扣分

        # ── 1. cell_name 审查（白名单跳过保留名检查）──
        name_issues = self._check_cell_name(cell_name, skip_reserved=is_exempted)
        issues.extend(name_issues)
        
        # ── 2. 类 docstring 审查 ──
        doc_issues = self._check_class_docstring(cell_type, cell)
        issues.extend(doc_issues)

        # ── 3. 命令方法审查 ──
        cmd_issues, cmd_warnings = self._check_commands(cell)
        issues.extend(cmd_issues)
        warnings.extend(cmd_warnings)

        # ── 4. _cmd_help 存在性（白名单跳过此项）──
        if not is_exempted:
            has_help = hasattr(cell, "_cmd_help") and callable(getattr(cell, "_cmd_help"))
            if not has_help:
                issues.append({
                    "rule": "has_help",
                    "severity": "warning",
                    "message": f"缺少 `_cmd_help` 方法。LLM 无法查询此组件的用法，连续失败 3 次后只能靠系统自动推断。",
                    "fix": "添加一个 `_cmd_help(self, topic: str=\"\")` 方法，返回该组件的详细使用说明。",
                    "example": '''
def _cmd_help(self, topic: str = "") -> Dict[str, Any]:
    """查询组件使用帮助
    
    Args:
        topic: 具体主题（留空返回总览）
    
    Returns:
        组件的使用说明、参数格式、示例等
    """
    commands = self.get_commands()
    return {
        "name": self.cell_name,
        "description": """在此写组件的功能描述""",
        "available_commands": commands,
        "usage_examples": [
            {"command": "xxx", "args": {...}, "description": "示例说明"},
        ],
        "notes": ["注意点1", "注意点2"],
    }''',
                })
                score -= 5
        
        # ── 5. 源码安全审查（白名单跳过此项 — 系统内置组件需要 os/json 等）──
        if not is_exempted:
            sec_issues = self._check_security(cell)
            issues.extend(sec_issues)

        # ── 计算最终结果 ──
        # 严重问题直接不通过；严格模式下 warning 也不通过
        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        error_count = sum(1 for i in issues if i["severity"] == "error")

        if self._strict:
            passed = len(issues) == 0  # 严格模式：任何问题都不通过
        else:
            passed = critical_count == 0  # 非严格：只看严重问题

        # 扣分计算
        score -= critical_count * 25
        score -= error_count * 10
        score -= len(warnings) * 3
        score = max(0, min(100, score))

        result = AuditResult(
            passed=passed,
            component_name=cell_name or "(未知)",
            component_type=cell_type,
            issues=issues,
            warnings=warnings,
            score=score,
        )

        log_level = logging.INFO if passed else logging.WARNING
        logger.log(log_level,
            "[ComponentAudit] %s | passed=%s | score=%d | issues=%d | critical=%d",
            cell_type, passed, score, len(issues), critical_count,
        )

        return result

    # ============================================================
    # 各项审查逻辑
    # ============================================================

    def _check_cell_name(self, name: str, skip_reserved: bool = False) -> List[Dict]:
        """检查 cell_name 合规性
        
        Args:
            name: 组件名称
            skip_reserved: 是否跳过保留名检查（白名单组件使用）
        """
        issues = []

        if not name:
            issues.append({
                "rule": "cell_name",
                "severity": "critical",
                "message": "cell_name 为空或未定义。每个组件必须有唯一的小写名称。",
                "fix": '在类中定义 cell_name 属性：\n\n@property\ndef cell_name(self) -> str:\n    return "my_component"',
                "example": '''@property
def cell_name(self) -> str:
    """组件标识（小写英文）"""
    return "my_tool"''',
            })
            return issues

        pattern = self.RULES["cell_name"]["pattern"]
        if not re.match(pattern, name):
            issues.append({
                "rule": "cell_name",
                "severity": "critical",
                "message": f"cell_name '{name}' 不合法：必须是「小写字母开头 + 小写字母数字下划线」。",
                "fix": f'改为小写格式，如 "{name.lower().replace("-", "_").replace(" ", "_")}"',
                "example": '''@property
def cell_name(self) -> str:
    return "my_component"  # ← 只用小写字母和下划线''',
            })

        # 保留字检查（白名单组件跳过）
        if not skip_reserved:
            reserved = {"shell", "memory", "file", "component", "system"}
            if name in reserved:
                issues.append({
                    "rule": "cell_name",
                    "severity": "critical",
                    "message": f"'{name}' 是系统保留名称，不可使用。",
                    "fix": f"换一个名字，如 '{name}_tool' 或 '{name}_plus'",
                })

        return issues

    def _check_class_docstring(self, class_name: str, cell: BaseCell) -> List[Dict]:
        """检查类是否有文档说明"""
        issues = []
        cls_doc = type(cell).__doc__ or ""

        if not cls_doc.strip():
            issues.append({
                "rule": "class_docstring",
                "severity": "error",
                "message": f"类 {class_name} 缺少 docstring。LLM 无法了解这个组件是做什么的。",
                "fix": "在 class 定义下方添加三引号文档，描述组件的功能、用途和使用场景。",
                "example": '''class MyTool(BaseCell):
    """
    我的工具 — 一句话描述做什么
    
    功能说明: 详细描述这个工具能干什么
    使用场景: 什么时候会用到这个工具
    """''',
            })

        return issues

    def _check_commands(self, cell: BaseCell) -> Tuple[List[Dict], List[str]]:
        """检查所有命令方法的规范性"""
        issues = []
        warnings = []
        
        commands = cell.get_commands()
        
        if not commands:
            issues.append({
                "rule": "has_commands",
                "severity": "critical",
                "message": "没有任何 _cmd_ 方法。组件必须至少提供一个可用命令。",
                "fix": '添加一个 _cmd_xxx 方法，如：\n\ndef _cmd_execute(self, input_data: str) -> Dict[str, Any]:\n    """执行主功能"""\n    return {"result": input_data}',
                "example": '''def _cmd_do_something(self, param: str) -> Dict[str, Any]:
    """做某事
    
    Args:
        param: 参数说明
        
    Returns:
        {"result": 处理结果}
    """
    # TODO: 实现你的逻辑
    return {"status": "ok"}''',
            })
            return issues, warnings

        for cmd_name, cmd_desc in commands.items():
            method_name = f"_cmd_{cmd_name}"
            method = getattr(cell, method_name, None)

            if not method or not callable(method):
                issues.append({
                    "rule": "has_commands",
                    "severity": "error",
                    "message": f"命令 '{cmd_name}' 在 get_commands() 中但对应的方法不存在或不可调用。",
                    "fix": f"确保定义了 `def {method_name}(self, ...)` 方法。",
                })
                continue

            # docstring 检查
            doc = method.__doc__ or ""
            if not doc.strip() or len(doc.strip()) < 10:
                issues.append({
                    "rule": "docstring",
                    "severity": "error",
                    "message": f"命令 '{cmd_name}' 的 docstring 为空或过短（<10字符）。LLM 不知道如何调用这个命令。",
                    "fix": f"为 {method_name} 添加详细的三段式 docstring（功能描述 + Args + Returns）。",
                    "example": '''def {}(self, some_param: str) -> Dict[str, Any]:
    \"\"\"
    一句话描述这个命令做什么
    
    Args:
        some_param: 参数的详细说明
        
    Returns:
        {{\"result\": 返回值说明}}
    \"\"\"'''.format(method_name),
                })

            # 返回值签名提示（静态分析）
            sig = inspect.signature(method)
            has_return_annotation = sig.return_annotation != inspect.Parameter.empty
            
            # 尝试从 docstring 推断返回值
            returns_dict_hint = "dict" in doc.lower() or "returns:" in doc.lower()
            
            if not has_return_annotation and not returns_dict_hint:
                warnings.append(
                    f"命令 '{cmd_name}' 未标注返回类型。建议添加 -> Dict[str, Any] 注解或 docstring 中包含 Returns 说明。"
                )

        return issues, warnings

    def _check_security(self, cell: BaseCell) -> List[Dict]:
        """源码级安全审查：检测危险导入

        分级策略：
        - BANNED_IMPORTS（eval/exec 等）：完全禁止，critical 错误
        - PROTECTED_IMPORTS（os/subprocess 等）：允许导入，warning 提示
          （运行时会被 ProtectedModuleProxy 拦截危险方法）
        """
        issues = []

        try:
            source_file = getattr(cell, '_source_file', None)
            if source_file is None:
                # 尝试从inspect获取
                try:
                    source_file = inspect.getfile(type(cell))
                except TypeError:
                    return issues

            with open(source_file, "r", encoding="utf-8") as f:
                source_code = f.read()

            tree = ast.parse(source_code)

            for node in ast.walk(tree):
                # import xxx
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_name = alias.name.split(".")[0]

                        # 完全禁止的模块（动态代码执行）
                        if module_name in BANNED_IMPORTS:
                            issues.append({
                                "rule": "no_dangerous_imports",
                                "severity": "critical",
                                "message": f"检测到禁止导入: `import {alias.name}`。动态代码执行模块不允许使用。",
                                "fix": "移除该导入。如果需要动态执行，请通过 shell 工具间接完成。",
                            })

                        # 受保护模块（运行时拦截）—— 仅警告
                        elif module_name in PROTECTED_IMPORTS:
                            issues.append({
                                "rule": "protected_import",
                                "severity": "warning",
                                "message": f"检测到受保护导入: `import {alias.name}`。危险方法（如 os.system）将被运行时拦截。",
                                "fix": "如需执行系统命令，请使用 shell 工具。当前导入的安全方法可正常使用。",
                            })

                # from xxx import yyy
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        full_path = f"{node.module}"
                        module_name = full_path.split(".")[0]

                        for alias in node.names:
                            combined = f"{full_path}.{alias.name}"

                            # 检查导入的是否是禁止函数
                            if alias.name in BANNED_IMPORTS or any(combined.startswith(d) for d in DANGEROUS_FROM_IMPORTS):
                                issues.append({
                                    "rule": "no_dangerous_imports",
                                    "severity": "critical",
                                    "message": f"检测到禁止导入: `from {node.module} import {alias.name}`。",
                                    "fix": "移除该导入。动态代码执行不允许使用。",
                                })

                            # 受保护模块的导入 —— 仅警告
                            elif module_name in PROTECTED_IMPORTS:
                                issues.append({
                                    "rule": "protected_import",
                                    "severity": "warning",
                                    "message": f"检测到受保护导入: `from {node.module} import {alias.name}`。危险方法将被运行时拦截。",
                                    "fix": "如需执行系统命令，请使用 shell 工具。",
                                })

        except Exception as e:
            logger.debug("[ComponentAudit] 安全审查跳过（无法读取源码）: %s", e)

        return issues


# ================================================================
# 全局单例
# ================================================================

_global_auditor: Optional[ComponentAuditor] = None


def get_auditor(strict_mode: bool = False) -> ComponentAuditor:
    """获取全局审查器单例"""
    global _global_auditor
    if _global_auditor is None:
        _global_auditor = ComponentAuditor(strict_mode=strict_mode)
    return _global_auditor

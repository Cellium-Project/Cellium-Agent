# -*- coding: utf-8 -*-

import importlib
import importlib.util
import inspect
import logging
import os
import pathlib
import re
import sys
import time
from typing import Any, Dict, List, Optional, Set, Type

try:
    import yaml
except ImportError:
    yaml = None

from app.core.di.container import DIContainer

# 组件采用外置模式，通过文件系统动态加载
from app.core.interface.icell import ICell
from app.core.interface.base_cell import BaseCell

logger = logging.getLogger(__name__)

# ============================================================
# 全局状态
# ============================================================

_cell_registry: Dict[str, ICell] = {}          # cell_name → instance
_component_classes: Dict[str, type] = {}        # class_name → class (原始类引用)
_loaded_files: Set[str] = set()                 # 已加载的文件路径集合
_file_mtimes: Dict[str, float] = {}              # 文件路径 → mtime（用于检测变更）
_load_errors: Dict[str, Dict] = {}               # 文件路径 → 加载错误信息（供 LLM 查询）


def get_all_cells() -> Dict[str, ICell]:
    """获取所有已注册的组件实例"""
    return dict(_cell_registry)


def get_load_errors() -> Dict[str, Dict]:
    """获取组件加载错误列表（供 LLM 查询）"""
    return dict(_load_errors)


def get_cell(name: str) -> Optional[ICell]:
    """根据 cell_name 获取组件实例"""
    return _cell_registry.get(name.lower())


def get_all_commands() -> Dict[str, Dict[str, str]]:
    """
    获取所有组件的可用命令汇总
    
    Returns:
        {cell_name: {command: description}}
    """
    result = {}
    for name, cell in _cell_registry.items():
        try:
            commands = cell.get_commands()
            if commands:
                result[name] = commands
        except Exception as e:
            logger.warning(f"获取 {name} 命令列表失败: {e}")
    return result


def register_cell(cell: ICell):
    """注册组件到全局注册表"""
    key = cell.cell_name.lower()
    if key in _cell_registry:
        logger.warning(f"组件已存在，将被覆盖: {key} ({type(_cell_registry[key]).__name__})")
    _cell_registry[key] = cell
    logger.info(f"[Component] 已注册: {key}")


def unregister_cell(name: str) -> bool:
    """卸载组件"""
    key = name.lower()
    if key in _cell_registry:
        cell = _cell_registry.pop(key)
        # 清理类引用
        cls_name = type(cell).__name__
        _component_classes.pop(cls_name, None)
        # 清理文件记录
        file_path = getattr(cell, '_source_file', None)
        if file_path:
            _loaded_files.discard(file_path)

        # 调用清理钩子
        if hasattr(cell, 'on_unload'):
            try:
                cell.on_unload()
            except Exception as e:
                logger.warning(f"组件 {key} on_unload 失败: {e}")

        logger.info(f"[Component] 已卸载: {key}")
        return True
    return False


def clear_registry():
    """清空全部组件"""
    for name in list(_cell_registry.keys()):
        unregister_cell(name)


# ============================================================
# 配置文件管理（自动读写 settings.yaml）
# ============================================================

COMPONENTS_CONFIG_PATH: Optional[pathlib.Path] = None


def get_config_path() -> pathlib.Path:
    """获取 settings.yaml 路径"""
    global COMPONENTS_CONFIG_PATH
    if COMPONENTS_CONFIG_PATH and COMPONENTS_CONFIG_PATH.exists():
        return COMPONENTS_CONFIG_PATH

    base_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    COMPONENTS_CONFIG_PATH = base_dir / "config" / "settings.yaml"
    return COMPONENTS_CONFIG_PATH


def load_settings() -> Dict[str, Any]:
    """读取 settings.yaml"""
    path = get_config_path()
    if not path.exists():
        return {"enabled_components": []}

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    if yaml is None:
        raise RuntimeError("PyYAML 未安装")

    config = yaml.safe_load(content)
    return config or {"enabled_components": []}


def save_settings(config: Dict[str, Any]):
    """写回 settings.yaml（保持格式整洁）"""
    path = get_config_path()
    if yaml is None:
        raise RuntimeError("PyYAML 未安装")

    # 确保 enabled_components 存在且有序
    components = config.get("enabled_components", []) or []
    
    with open(path, "w", encoding="utf-8") as f:
        # 先写注释头
        f.write("# ============================================================\n")
        f.write("# 组件配置 — 由系统自动维护\n")
        f.write("#\n")
        f.write("# 【组件使用铁律】\n")
        f.write("# 1. 继承 BaseCell，定义 cell_name 和 execute()\n")
        f.write("# 2. 命令方法以 _cmd_ 前缀命名，必须写 docstring\n")
        f.write("# 3. 文件放 components/ 下，系统自动发现并注册\n")
        f.write("# 4. 不要手动修改 enabled_components 列表（系统自动生成）\n")
        f.write("# 5. 卸载组件只需删除对应的 .py 文件\n")
        f.write("#\n")
        f.write("# 启用的组件列表（系统自动生成，请勿手动编辑）\n")
        f.write("# ============================================================\n")
        
        yaml.dump(
            {"enabled_components": components},
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False
        )


def register_to_config(module_path: str):
    """
    将新发现的组件模块路径追加到 settings.yaml
    
    Args:
        module_path: 如 "components.my_component.MyComponent"
    """
    # 跳过临时模块路径（扫描时生成的 _component_xxx_ 前缀）
    if module_path.startswith("_component_"):
        return False
    
    try:
        config = load_settings()
        components = config.get("enabled_components", []) or []
        
        if module_path in components:
            return False  # 已存在
        
        components.append(module_path)
        config["enabled_components"] = components
        
        save_settings(config)
        logger.info(f"[Component] 已写入配置: {module_path}")
        return True
        
    except Exception as e:
        logger.error(f"[Component] 写入配置失败 {module_path}: {e}")
        return False


def unregister_from_config(module_path: str):
    """从 settings.yaml 移除组件条目"""
    try:
        config = load_settings()
        components = config.get("enabled_components", []) or []
        
        if module_path not in components:
            return False
        
        components.remove(module_path)
        config["enabled_components"] = components
        
        save_settings(config)
        logger.info(f"[Component] 已从配置移除: {module_path}")
        return True
        
    except Exception as e:
        logger.error(f"[Component] 从配置移除失败 {module_path}: {e}")
        return False


# ============================================================
# 自动发现 — 核心扫描引擎
# ============================================================

def get_components_dir() -> pathlib.Path:
    """获取组件扫描目录（components/）"""
    # 支持 Nuitka 打包后的路径
    if getattr(sys, 'frozen', False):
        # Nuitka 打包后，sys.executable 指向 .exe 文件
        base_dir = pathlib.Path(sys.executable).resolve().parent
    else:
        # 开发环境
        base_dir = pathlib.Path(__file__).resolve().parent.parent.parent.parent
    return base_dir / "components"



def discover_components() -> List[Dict[str, Any]]:
    """
    扫描 components/ 目录，发现所有符合条件的组件类
    
    识别规则：
      1. .py 文件（非 __init__.py，非 _ 开头文件）
      2. 文件中包含继承 BaseCell 或 ICell 的类
      3. 类不是抽象的（可以实例化）
    
    扫描范围：
      - components/*.py → 系统组件
    
    Returns:
        发现结果列表 [{file, class_name, module_path, is_new}]
    """
    components_dir = get_components_dir()
    
    results = []
    
    scan_dirs = [components_dir]
    
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        
        for py_file in sorted(scan_dir.glob("*.py")):
            if py_file.name.startswith("_") and py_file.name != "__init__.py":
                continue
            if py_file.name == "__init__":
                continue

            try:
                result = _extract_cell_classes(py_file)
                if isinstance(result, tuple):
                    classes, error_info = result
                    if error_info:
                        logger.warning(f"[Component] 文件有错误 {py_file.name}: {error_info['error_type']} - {error_info['error']}")
                        _load_errors[str(py_file)] = {
                            "file": py_file.name,
                            "error": error_info['error'],
                            "error_type": error_info['error_type'],
                            "timestamp": time.time(),
                        }
                else:
                    classes = result
                    if str(py_file) in _load_errors:
                        del _load_errors[str(py_file)]

                for cls_info in classes:
                    is_new = str(py_file) not in _loaded_files
                    results.append({
                        "file": str(py_file),
                        "class_name": cls_info["class_name"],
                        "module_path": cls_info["module_path"],
                        "cls": cls_info["cls"],
                        "is_new": is_new,
                    })
                    
            except Exception as e:
                logger.error(f"[Component] 解析文件失败 {py_file.name}: {e}")

    return results


def _extract_cell_classes(file_path: pathlib.Path) -> tuple:
    rel_path = file_path.relative_to(get_components_dir())
    module_name = f"_component_{rel_path.stem}_{hash(str(file_path)) & 0xFFFFFF}"

    try:
        cached = importlib.util.cache_from_source(str(file_path))
        if cached and os.path.exists(cached):
            os.remove(cached)
            logger.debug(f"[Component] discover 阶段删除缓存: {cached}")
    except Exception as e:
        logger.debug(f"[Component] discover 阶段删除缓存失败: {e}")

    if module_name in sys.modules:
        del sys.modules[module_name]
        logger.debug(f"[Component] 清除 discover 旧模块: {module_name}")

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        return [], {"error": f"无法加载文件: {file_path}", "file": str(file_path)}

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[Component] 导入模块失败 {module_name}: {e}\n{tb}")
        return [], {
            "error": str(e),
            "error_type": type(e).__name__,
            "traceback": tb,
            "module": module_name,
            "file": str(file_path),
        }

    found = []
    
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module_name:
            continue

        # 检查是否是 BaseCell 或 ICell 子类
        if issubclass(obj, BaseCell) or (
            issubclass(obj, ICell) and obj is not ICell and obj is not BaseCell
        ):
            if not inspect.isabstract(obj):
                # 使用正确的模块路径（不是临时的 _component_xxx_ 路径）
                correct_module_path = f"components.{rel_path.stem}.{name}"
                found.append({
                    "class_name": name,
                    "module_path": correct_module_path,
                    "cls": obj,
                })

    return found, None


# ============================================================
# 加载与热加载
# ============================================================

def load_components(
    container: DIContainer = None,
    auto_discover: bool = True,
    auto_register: bool = True,
) -> Dict[str, ICell]:

    if auto_discover:
        discovered = discover_components()
        for item in discovered:
            if item["is_new"] and auto_register:
                register_to_config(item["module_path"])
    
    config = load_settings()
    component_list = config.get("enabled_components", []) or []
    
    loaded: Dict[str, ICell] = {}
    failed: List[str] = []
    
    logger.info(f"[Component] 开始加载组件，共 {len(component_list)} 个")
    
    for module_path in component_list:
        try:
            instance, info = _instantiate_component(module_path)
            
            if instance is None:
                failed.append(module_path)
                continue
            
            register_cell(instance)
            loaded[instance.cell_name] = instance
            
            if container:
                cls = type(instance)
                container.register(cls, instance)
            
            source_file = info.get("source_file")
            if source_file:
                _loaded_files.add(source_file)
                _file_mtimes[source_file] = os.path.getmtime(source_file)
                instance._source_file = source_file

            if hasattr(instance, "on_load"):
                instance.on_load()
            
            status = "NEW" if info.get("is_new") else "OK"
            logger.info(f"[Component] [{status}] {info['class_name']} "
                       f"(cell={instance.cell_name}, cmds={len(instance.get_commands())})")
                        
        except ImportError as e:
            if "组件文件不存在" in str(e):
                logger.warning(f"[Component] 组件文件已删除，从配置移除: {module_path}")
                unregister_from_config(module_path)
            else:
                logger.error(f"[Component] 加载失败 {module_path}: {e}", exc_info=True)
            failed.append(module_path)
        except Exception as e:
            logger.error(f"[Component] 加载失败 {module_path}: {e}", exc_info=True)
            failed.append(module_path)
    
    logger.info(f"[Component] 加载完成: {len(loaded)} 成功, {len(failed)} 失败")
    
    if loaded:
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            registry = get_component_tool_registry()
            registry.sync_from_components_loader()
            logger.info(f"[Component] 已同步 {len(loaded)} 个组件到工具注册表")
        except Exception as e:
            logger.warning(f"[Component] 同步到工具注册表失败: {e}")
    
    return loaded


def _instantiate_component(module_path: str) -> tuple:
    """
    实例化单个组件（支持外部热加载，不依赖 components package）

    热重载策略：
      - 先清除 sys.modules 中的旧模块（含 discover 阶段的临时模块）
      - 再用 spec_from_file_location 从文件重新加载
      - 这比 importlib.reload 更可靠，后者对 spec_from_file_location
        加载的模块经常失败（缺少 __spec__.loader）

    Returns:
        (instance, info_dict) 或 (None, error_info)
    """
    parts = module_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"无效的模块路径: {module_path}")

    module_name, class_name = parts

    # ── 定位组件源文件 ──
    components_dir = get_components_dir()
    import re
    s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', class_name)
    snake_name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s1).lower()
    file_path = components_dir / f"{snake_name}.py"

    if not file_path.exists():
        raise ImportError(f"组件文件不存在: {file_path}")

    # ── 清除旧模块引用（确保重新加载而非复用缓存） ──
    old_file = None
    if module_name in sys.modules:
        old_mod = sys.modules[module_name]
        old_file = getattr(old_mod, '__file__', None)
        del sys.modules[module_name]
        logger.debug(f"[Component] 清除旧模块: {module_name}")

    # 也清除 discover 阶段创建的临时模块（_component_xxx 前缀）
    for key in list(sys.modules.keys()):
        if not key.startswith("_component_"):
            continue
        mod = sys.modules.get(key)
        if mod is None:
            continue
        mod_file = getattr(mod, '__file__', None)
        if mod_file and (mod_file == str(file_path) or mod_file == old_file):
            del sys.modules[key]
            logger.debug(f"[Component] 清除临时模块: {key}")

    try:
        cached = importlib.util.cache_from_source(str(file_path))
        if cached and os.path.exists(cached):
            os.remove(cached)
            logger.debug(f"[Component] 删除缓存文件: {cached}")
    except Exception as e:
        logger.debug(f"[Component] 删除缓存文件失败: {e}")

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if not spec or not spec.loader:
        raise ImportError(f"无法加载文件: {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"类不存在: {class_name} 在 {module_name}")

    instance = cls()

    source_file = ""
    try:
        source_file = inspect.getfile(cls)
    except TypeError:
        pass

    info = {
        "class_name": class_name,
        "module_path": module_path,
        "source_file": source_file,
        "is_new": source_file not in _loaded_files if source_file else True,
    }

    return instance, info


def hot_reload(container: DIContainer = None) -> Dict[str, Any]:
    """
    热重载：重新扫描目录，增量加载/卸载组件
    
    Returns:
        变更报告 {"added": [...], "removed": [...], "updated": [...]}
    """
    report = {"added": [], "removed": [], "updated": []}
    
    current_files = set()
    discovered = discover_components()
    
    for item in discovered:
        file_path = item["file"]
        current_files.add(file_path)
        
        cell_name_lower = item["cls"]().__class__.__name__.lower()
        
        if file_path not in _loaded_files:
            try:
                instance, info = _instantiate_component(item["module_path"])
                register_cell(instance)
                _loaded_files.add(file_path)
                _file_mtimes[file_path] = os.path.getmtime(file_path)  # 记录 mtime
                instance._source_file = file_path
                
                if hasattr(instance, "on_load"):
                    instance.on_load()
                
                if container:
                    container.register(type(instance), instance)
                
                register_to_config(item["module_path"])
                report["added"].append({"name": instance.cell_name, "class": item["class_name"]})
                logger.info(f"[HotReload] 新组件: {instance.cell_name} ({item['class_name']})")
            except Exception as e:
                logger.error(f"[HotReload] 加载新组件失败 {item['class_name']}: {e}")
        else:
            try:
                current_mtime = os.path.getmtime(file_path)
                if file_path in _file_mtimes and _file_mtimes[file_path] != current_mtime:
                    logger.info(f"[HotReload] 检测到组件更新: {item['class_name']} (mtime changed)")
                    try:
                        old_instance = None
                        for name, cell in list(_cell_registry.items()):
                            if getattr(cell, '_source_file', '') == file_path:
                                old_instance = cell
                                break
                        if old_instance and hasattr(old_instance, "on_unload"):
                            old_instance.on_unload()

                        from app.core.util.cell_tool_adapter import EXEMPTED_NAMES
                        cell_name_lower = item["class_name"].lower()
                        use_sandbox = cell_name_lower not in EXEMPTED_NAMES

                        if use_sandbox:
                            try:
                                from app.core.util.component_sandbox import ComponentSandbox
                                sandbox_name = cell_name_lower
                                ComponentSandbox.reload_sandbox(sandbox_name)
                                logger.info(f"[HotReload] 已重启沙箱: {sandbox_name}")
                            except Exception as e:
                                logger.error(f"[HotReload] 重启沙箱失败 {item['class_name']}: {e}")
                                logger.error(f"[HotReload] 组件 {item['class_name']} 热更新中止：沙箱重启失败")
                                report["failed"] = report.get("failed", [])
                                report["failed"].append({"name": cell_name_lower, "class": item["class_name"], "reason": f"沙箱重启失败: {e}"})
                                continue  

                        instance, info = _instantiate_component(item["module_path"])
                        register_cell(instance)
                        _loaded_files.add(file_path)
                        _file_mtimes[file_path] = current_mtime
                        instance._source_file = file_path

                        if hasattr(instance, "on_load"):
                            instance.on_load()

                        if container:
                            container.register(type(instance), instance)

                        report["updated"].append({"name": instance.cell_name, "class": item["class_name"]})
                        logger.info(f"[HotReload] 已更新: {instance.cell_name} ({item['class_name']})")
                    except Exception as e:
                        logger.error(f"[HotReload] 更新组件失败 {item['class_name']}: {e}")
            except OSError:
                pass
    
    removed_files = _loaded_files - current_files
    removed_tool_names = []
    for file_path in removed_files:
        target_name = None
        for name, cell in list(_cell_registry.items()):
            if getattr(cell, '_source_file', '') == file_path:
                target_name = name
                break
        
        if target_name:
            cls_name = type(_cell_registry[target_name]).__name__
            module_path = f"components.{pathlib.Path(file_path).stem}.{cls_name}"
            
            # 清理沙箱缓存（修复：组件删除时沙箱实例未清理）
            try:
                from app.core.util.component_sandbox import ComponentSandbox
                if target_name in ComponentSandbox.get_all_sandbox_names():
                    ComponentSandbox.remove_sandbox(target_name)
                    logger.debug(f"[HotReload] 已清理沙箱缓存: {target_name}")
            except Exception as e:
                logger.debug(f"[HotReload] 清理沙箱缓存失败 {target_name}: {e}")
            
            # 清理组件类引用缓存（修复：清理孤儿引用）
            if cls_name in _component_classes:
                del _component_classes[cls_name]
                logger.debug(f"[HotReload] 已清理类引用缓存: {cls_name}")
            
            unregister_cell(target_name)
            unregister_from_config(module_path)
            removed_tool_names.append(target_name)
            
            # 清理工具注册表（修复：组件删除时从工具注册表移除）
            try:
                from app.core.util.component_tool_registry import get_component_tool_registry
                registry = get_component_tool_registry()
                registry.unregister(target_name)
                logger.debug(f"[HotReload] 已从工具注册表移除: {target_name}")
            except Exception as e:
                logger.debug(f"[HotReload] 从工具注册表移除失败 {target_name}: {e}")
            
            # 清理加载错误缓存（修复：删除组件时清理错误记录）
            if file_path in _load_errors:
                del _load_errors[file_path]
                logger.debug(f"[HotReload] 已清理错误缓存: {file_path}")
            
            report["removed"].append({"name": target_name, "class": cls_name})
            logger.info(f"[HotReload] 已卸载: {target_name}")
        
        _loaded_files.discard(file_path)
        _file_mtimes.pop(file_path, None)
    
    # 清理信任白名单中已删除的组件（修复：信任白名单残留）
    if removed_tool_names:
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            registry = get_component_tool_registry()
            current_names = set(_cell_registry.keys())
            cleaned = registry.cleanup_trust_list(current_names)
            if cleaned:
                logger.info(f"[HotReload] 已清理信任白名单: {cleaned}")
        except Exception as e:
            logger.debug(f"[HotReload] 清理信任白名单失败: {e}")
    
    # 同步到工具注册表（修复：确保新增/更新的组件能被 Agent 使用）
    if report.get("added") or report.get("updated"):
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            registry = get_component_tool_registry()
            registry.sync_from_components_loader()
            added_count = len(report.get("added", []))
            updated_count = len(report.get("updated", []))
            logger.info(f"[HotReload] 已同步到工具注册表: {added_count} 新增, {updated_count} 更新")
        except Exception as e:
            logger.warning(f"[HotReload] 同步到工具注册表失败: {e}")
    
    return report


# ============================================================
# 缓存清理 API
# ============================================================

def clear_all_caches(force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """
    手动清理所有组件相关缓存
    
    用于解决系统级缓存问题，如：
    - 组件删除后仍提示错误
    - 沙箱实例残留
    - 信任白名单过期
    
    Args:
        force: 是否强制清理（包括正在运行的沙箱）
        dry_run: 是否只预览，不实际执行
    
    Returns:
        清理结果报告
    """
    global _load_errors, _component_classes
    
    report = {
        "dry_run": dry_run,
        "load_errors_cleared": 0,
        "component_classes_cleared": 0,
        "sandboxes_cleared": 0,
        "sandboxes_skipped": [],
        "trust_list_cleaned": [],
        "warnings": [],
    }
    
    # 1. 清理加载错误缓存（安全）
    if _load_errors:
        report["load_errors_cleared"] = len(_load_errors)
        if not dry_run:
            _load_errors.clear()
            logger.info(f"[CacheCleanup] 已清理 {report['load_errors_cleared']} 条加载错误缓存")
    
    # 2. 清理组件类引用缓存（安全，只清理孤儿引用）
    current_classes = {type(cell).__name__ for cell in _cell_registry.values()}
    orphaned = [name for name in list(_component_classes.keys()) if name not in current_classes]
    report["component_classes_cleared"] = len(orphaned)
    if orphaned and not dry_run:
        for name in orphaned:
            del _component_classes[name]
        logger.info(f"[CacheCleanup] 已清理 {len(orphaned)} 个孤儿类引用")
    
    # 3. 清理沙箱缓存（需要检查是否正在运行）
    try:
        from app.core.util.component_sandbox import ComponentSandbox
        all_sandboxes = ComponentSandbox.get_all_sandbox_names()
        current_names = set(_cell_registry.keys())
        stale_sandboxes = [name for name in all_sandboxes if name not in current_names]
        
        for name in stale_sandboxes:
            # 获取已存在的沙箱实例（不会创建新的）
            sandbox = ComponentSandbox._get_existing_sandbox(name)
            if sandbox is None:
                continue
            
            # 检查沙箱是否正在执行命令
            is_busy = sandbox.is_alive()
            
            if is_busy and not force:
                report["sandboxes_skipped"].append(name)
                report["warnings"].append(f"沙箱 {name} 可能正在运行，跳过清理（使用 force=True 强制清理）")
                continue
            
            if not dry_run:
                ComponentSandbox.remove_sandbox(name)
            report["sandboxes_cleared"] += 1
            
        if stale_sandboxes:
            logger.info(f"[CacheCleanup] {'[预览] ' if dry_run else ''}发现 {len(stale_sandboxes)} 个残留沙箱实例，清理 {report['sandboxes_cleared']} 个，跳过 {len(report['sandboxes_skipped'])} 个")
    except Exception as e:
        report["warnings"].append(f"清理沙箱缓存失败: {e}")
        logger.warning(f"[CacheCleanup] 清理沙箱缓存失败: {e}")
    
    # 4. 清理信任白名单（安全，只清理不存在的）
    try:
        from app.core.util.component_tool_registry import get_component_tool_registry
        registry = get_component_tool_registry()
        current_names = set(_cell_registry.keys())
        
        # 先获取将要清理的列表
        trusted = registry._load_trust_list()
        to_remove = [name for name in trusted if name not in current_names]
        report["trust_list_cleaned"] = to_remove
        
        if to_remove and not dry_run:
            registry.cleanup_trust_list(current_names)
            logger.info(f"[CacheCleanup] 已清理信任白名单: {to_remove}")
    except Exception as e:
        report["warnings"].append(f"清理信任白名单失败: {e}")
        logger.warning(f"[CacheCleanup] 清理信任白名单失败: {e}")
    
    # 5. 同步工具注册表（清理已删除组件的工具）
    if not dry_run:
        try:
            from app.core.util.component_tool_registry import get_component_tool_registry
            registry = get_component_tool_registry()
            for tool_name in list(registry.get_all_names()):
                if tool_name not in _cell_registry and tool_name not in registry.RESERVED_TOOL_NAMES:
                    registry.unregister(tool_name)
                    logger.info(f"[CacheCleanup] 已从工具注册表移除: {tool_name}")
        except Exception as e:
            report["warnings"].append(f"同步工具注册表失败: {e}")
    
    if dry_run:
        logger.info(f"[CacheCleanup] [预览模式] 发现的问题: {report}")
    else:
        logger.info(f"[CacheCleanup] 缓存清理完成: {report}")
    
    return report


# ============================================================
# 兼容旧 API
# ============================================================

def load_component_config(config_path=None) -> Dict[str, Any]:
    """兼容旧接口"""
    return load_settings()

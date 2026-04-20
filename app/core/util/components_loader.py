# -*- coding: utf-8 -*-
"""
组件加载器 v2 — 自动发现 + 热加载 + 自扩展

核心能力：
  1. 扫描 components/ 目录（含 skills/ 子目录），自动发现符合 ICell 契约的组件类
  2. 发现新组件后自动写入 config/settings.yaml（无需人工干预）
  3. 支持热加载：运行时动态注册/卸载组件
  4. Agent 只需在 components/ 下放一个 .py 文件即可扩展自身能力

目录约定：
  components/
  ├── __init__.py              # （空包标记）
  ├── _example_component.py     # 组件模板参考
  ├── component_builder.py      # 组件生成器（系统内置）
  ├── skill_installer.py        # Skill 管理器（系统内置）
  ├── my_tool.py               # Agent 创建的组件（系统级）
  └── skills/                  # Skill 安装目录（插件式，通过 skill_installer 管理）
      ├── __init__.py          # 包标记
      ├── git_helper.py         # 已安装的 Skill
      └── code_refactor.py      # 已安装的 Skill

【Skill vs 组件】
  组件 (Components): 系统内置工具，随启动自动加载，通过 component.generate() 创建
  Skill (Skills): 插件式能力包，安装于 skills/ 子目录，通过 skill_installer 管理
  两者最终都是 BaseCell 子类，都会被注册为 LLM 可调用工具

组件铁律（Agent 必须遵守）：
  - 文件必须包含一个继承 BaseCell 的类（或实现 ICell 接口）
  - 类名即组件标识，cell_name 属性用于命令路由
  - 命令方法以 _cmd_ 前缀定义，自动映射为 execute(command)
  - 每个命令必须有 docstring 描述其用途

配置文件自动维护：
  发现新组件 → 自动追加到 settings.yaml 的 enabled_components
  Agent 不需要手动编辑 YAML，只需写好 .py 文件放入目录
"""

import importlib
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


def get_skills_dir() -> pathlib.Path:
    """获取 Skill 安装目录（components/skills/）"""
    return get_components_dir() / "skills"


def discover_components() -> List[Dict[str, Any]]:
    """
    扫描 components/ 目录（含 skills/ 子目录），发现所有符合条件的组件类
    
    识别规则：
      1. .py 文件（非 __init__.py，非 _ 开头文件）
      2. 文件中包含继承 BaseCell 或 ICell 的类
      3. 类不是抽象的（可以实例化）
    
    扫描范围：
      - components/*.py        → 系统组件
      - components/skills/*.py  → 安装的 Skill
    
    Returns:
        发现结果列表 [{file, class_name, module_path, is_new}]
    """
    components_dir = get_components_dir()
    skills_dir = get_skills_dir()
    
    results = []
    
    # 扫描两个目录：组件目录 + skills 目录
    scan_dirs = [components_dir]
    if skills_dir.exists() and skills_dir.is_dir() and skills_dir != components_dir:
        scan_dirs.append(skills_dir)
    
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
                        # 记录错误供 LLM 查询
                        _load_errors[str(py_file)] = {
                            "file": py_file.name,
                            "error": error_info['error'],
                            "error_type": error_info['error_type'],
                            "timestamp": time.time(),
                        }
                else:
                    classes = result
                    # 清除之前的错误记录
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
    """
    从单个 .py 文件中提取所有合法的组件类（支持外部热加载，不依赖 components package）

    Returns:
        (classes, error_info) 元组
        - classes: 找到的组件类列表
        - error_info: 如果有错误则包含错误详情，否则为 None
    """
    # 使用唯一模块名避免冲突（基于文件路径）
    rel_path = file_path.relative_to(get_components_dir())
    module_name = f"_component_{rel_path.stem}_{hash(str(file_path)) & 0xFFFFFF}"

    # 动态导入
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

    # 提取类
    found = []
    
    for name, obj in inspect.getmembers(module, inspect.isclass):
        # 跳过非本文件定义的类
        if obj.__module__ != module_name:
            continue

        # 检查是否是 BaseCell 或 ICell 子类
        if issubclass(obj, BaseCell) or (
            issubclass(obj, ICell) and obj is not ICell and obj is not BaseCell
        ):
            # 检查是否可实例化（非抽象）
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
    """
    加载组件（支持自动发现 + 配置驱动）
    
    流程：
      1. 如果 auto_discover=True，先扫描目录发现新组件
      2. 新发现的自动写入 settings.yaml（如果 auto_register）
      3. 按 settings.yaml 列表加载所有启用的组件
    
    Args:
        container: DI 容器（可选）
        auto_discover: 是否自动扫描发现新组件
        auto_register: 发现后是否自动注册到配置
    
    Returns:
        已加载的组件字典 {cell_name: instance}
    """
    # 1. 自动发现
    if auto_discover:
        discovered = discover_components()
        for item in discovered:
            if item["is_new"] and auto_register:
                register_to_config(item["module_path"])
    
    # 2. 从配置加载
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
            
            # 注册到全局表
            register_cell(instance)
            loaded[instance.cell_name] = instance
            
            # 注册到 DI 容器
            if container:
                cls = type(instance)
                container.register(cls, instance)
            
            # 记录来源文件
            source_file = info.get("source_file")
            if source_file:
                _loaded_files.add(source_file)
                _file_mtimes[source_file] = os.path.getmtime(source_file)
                instance._source_file = source_file

            # 调用加载钩子
            if hasattr(instance, "on_load"):
                instance.on_load()
            
            status = "NEW" if info.get("is_new") else "OK"
            logger.info(f"[Component] [{status}] {info['class_name']} "
                       f"(cell={instance.cell_name}, cmds={len(instance.get_commands())})")
                        
        except Exception as e:
            logger.error(f"[Component] 加载失败 {module_path}: {e}", exc_info=True)
            failed.append(module_path)
    
    logger.info(f"[Component] 加载完成: {len(loaded)} 成功, {len(failed)} 失败")
    return loaded


def _instantiate_component(module_path: str) -> tuple:
    """
    实例化单个组件（支持外部热加载，不依赖 components package）

    Returns:
        (instance, info_dict) 或 (None, error_info)
    """
    parts = module_path.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"无效的模块路径: {module_path}")

    module_name, class_name = parts

    # 从 sys.modules 中查找已加载的模块
    if module_name in sys.modules:
        module = sys.modules[module_name]
    else:
        # 尝试从文件系统加载（支持外部热加载）
        components_dir = get_components_dir()
        # 类名转 snake_case: ComponentBuilder -> component_builder, QQFiles -> qq_files
        import re
        # 先处理小写+大写的情况 (如 ComponentBuilder -> Component_Builder)
        s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', class_name)
        # 再处理连续大写+小写的情况 (如 QQFiles -> QQ_Files)
        snake_name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s1).lower()
        file_path = components_dir / f"{snake_name}.py"
        if file_path.exists():
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            else:
                raise ImportError(f"无法加载文件: {file_path}")
        else:
            raise ImportError(f"组件文件不存在: {file_path}")
    
    # 获取类
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"类不存在: {class_name} 在 {module_name}")
    
    # 验证接口
    instance = cls()
    
    # 来源信息
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
    
    # 当前已知的文件
    current_files = set()
    discovered = discover_components()
    
    for item in discovered:
        file_path = item["file"]
        current_files.add(file_path)
        
        cell_name_lower = item["cls"]().__class__.__name__.lower()
        
        if file_path not in _loaded_files:
            # 新组件
            try:
                instance, info = _instantiate_component(item["module_path"])
                register_cell(instance)
                _loaded_files.add(file_path)
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
            # 已存在的组件 — 检查是否有变更（mtime）
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
    
    # 检测被删除的文件
    removed_files = _loaded_files - current_files
    for file_path in removed_files:
        # 找到对应组件名
        target_name = None
        for name, cell in list(_cell_registry.items()):
            if getattr(cell, '_source_file', '') == file_path:
                target_name = name
                break
        
        if target_name:
            cls_name = type(_cell_registry[target_name]).__name__
            module_path = f"components.{pathlib.Path(file_path).stem}.{cls_name}"
            
            unregister_cell(target_name)
            unregister_from_config(module_path)
            report["removed"].append({"name": target_name, "class": cls_name})
            logger.info(f"[HotReload] 已卸载: {target_name}")
    
    return report


# ============================================================
# 兼容旧 API
# ============================================================

def load_component_config(config_path=None) -> Dict[str, Any]:
    """兼容旧接口"""
    return load_settings()

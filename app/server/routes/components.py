# -*- coding: utf-8 -*-
"""
组件管理 API — Agent 自扩展接口

提供运行时查看、扫描、注册/卸载组件的能力。
Agent 可以通过此 API 了解自身能力，并动态增删功能模块。

接口列表:
  GET    /api/components              → 组件总览（已加载 + 可用命令）
  GET    /api/components/scan         → 扫描目录发现新组件（不加载）
  POST   /api/components/reload       → 热重载（增量扫描 + 自动注册/卸载）
  POST   /api/components/{name}/load  → 手动加载指定组件
  DELETE /api/components/{name}       → 卸载指定组件
  GET    /api/components/rules        → 查看组件使用铁律
"""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/components", tags=["components"])

# 延迟导入避免循环引用


def _get_container():
    from app.core.di.container import get_container
    return get_container()


@router.get("")
async def list_components():
    """
    列出所有已加载的组件及其可用命令
    
    返回每个组件的：
      - cell_name: 组件标识
      - class: 类名
      - commands: 可用命令 {名称: 描述}
      - command_count: 命令数量
    """
    from app.core.util.components_loader import (
        get_all_cells,
        get_all_commands,
        _loaded_files,
    )

    cells = get_all_cells()
    commands = get_all_commands()

    result = {
        "total": len(cells),
        "components": [],
        "total_commands": sum(len(cmds) for cmds in commands.values()),
    }

    for name, cell in cells.items():
        result["components"].append({
            "cell_name": name,
            "class": type(cell).__name__,
            "commands": commands.get(name, {}),
            "command_count": len(commands.get(name, {})),
            "source_file": getattr(cell, '_source_file', None),
        })

    return result


@router.get("/scan")
async def scan_components():
    """
    扫描 components/ 目录，发现所有可用的组件类
    
    不执行加载，仅返回发现结果。
    用于 Agent 查看"有哪些新能力可以激活"。
    """
    from app.core.util.components_loader import (
        discover_components,
        _loaded_files,
        load_settings,
    )

    discovered = discover_components()
    config = load_settings()
    enabled = set(config.get("enabled_components", []) or [])

    items = []
    for item in discovered:
        items.append({
            "file": item["file"],
            "class_name": item["class_name"],
            "module_path": item["module_path"],
            "is_new": item["is_new"],
            "is_enabled": item["module_path"] in enabled,
            "is_loaded": not item["is_new"] and str(item["file"]) in _loaded_files,
        })

    return {
        "scanned_at": __import__("time").time(),
        "discovered_count": len(items),
        "new_items": [i for i in items if i["is_new"]],
        "all": items,
    }


@router.post("/reload")
async def hot_reload():
    """
    热重载所有组件
    
    执行流程：
      1. 扫描 components/ 发现新增 .py 文件 → 自动注册并加载
      2. 检测被删除的 .py 文件 → 自动卸载对应组件
      3. 更新 settings.yaml
    
    无需重启服务，Agent 放入文件即生效。
    """
    container = _get_container()

    try:
        from app.core.util.components_loader import hot_reload
        report = hot_reload(container=container)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "ok",
        "message": f"热重载完成",
        "added": report["added"],
        "removed": report["removed"],
        "changes": len(report["added"]) + len(report["removed"]),
    }


@router.post("/discover-and-load")
async def discover_and_load():
    """
    发现新组件并全部加载
    
    与 reload 的区别：此接口会扫描 + 注册到配置 + 全量加载，
    适用于首次初始化或批量添加组件后调用。
    """
    container = _get_container()

    try:
        from app.core.util.components_loader import load_components
        loaded = load_components(
            container=container,
            auto_discover=True,
            auto_register=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "status": "ok",
        "loaded_count": len(loaded),
        "components": list(loaded.keys()),
    }


@router.post("/{name}/load")
async def load_single_component(name: str):
    """手动按组件名加载单个组件"""
    # 先查找该组件的 module_path
    from app.core.util.components_loader import (
        discover_components,
        _instantiate_component,
        register_cell,
        register_to_config,
        get_config_path,
        _loaded_files,
    )
    from app.core.di.container import get_container

    discovered = discover_components()
    target = None
    for item in discovered:
        cls_instance = item["cls"]()
        if cls_instance.cell_name.lower() == name.lower():
            target = item
            break

    if not target:
        raise HTTPException(status_code=404, detail=f"未找到组件: {name}")

    try:
        instance, info = _instantiate_component(target["module_path"])
        register_cell(instance)
        _loaded_files.add(target["file"])
        instance._source_file = target["file"]

        if hasattr(instance, "on_load"):
            instance.on_load()

        container = get_container()
        container.register(type(instance), instance)

        register_to_config(target["module_path"])

        return {
            "status": "ok",
            "message": f"组件 [{instance.cell_name}] 已加载",
            "component": {
                "cell_name": instance.cell_name,
                "class": target["class_name"],
                "commands": instance.get_commands(),
                "file": target["file"],
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加载失败: {e}")


@router.delete("/{name}")
async def unload_component(name: str):
    """卸载指定组件"""
    from app.core.util.components_loader import unregister_cell, unregister_from_config, get_cell

    cell = get_cell(name)

    if not cell:
        raise HTTPException(status_code=404, detail=f"组件不存在或未加载: {name}")

    cls_name = type(cell).__name__
    source_file = getattr(cell, '_source_file', '')

    # 构造 module_path
    import pathlib
    if source_file:
        stem = pathlib.Path(source_file).stem
        module_path = f"components.{stem}.{cls_name}"
        unregister_from_config(module_path)

    success = unregister_cell(name)

    return {
        "status": "ok" if success else "warning",
        "message": f"组件 [{name}] 已卸载" if success else f"卸载失败: {name}",
        "unloaded_class": cls_name,
    }


@router.get("/rules")
async def get_rules():
    """返回组件使用铁律（供 Agent 参考）"""
    return {
        "iron_rules": [
            {
                "rule": 1,
                "title": "继承 BaseCell",
                "detail": "必须继承 app.core.interface.base_cell.BaseCell，定义 cell_name 和 execute()",
            },
            {
                "rule": 2,
                "title": "_cmd_ 前缀命名命令",
                "detail": "命令方法必须以 _cmd_ 开头，且必须有 docstring 描述用途和参数",
            },
            {
                "rule": 3,
                "title": "放入 components 目录",
                "detail": ".py 文件放 components/ 下，系统自动发现并注册",
            },
            {
                "rule": 4,
                "title": "不手动编辑配置",
                "detail": "不要修改 settings.yaml 的 enabled_components，系统自动生成和维护",
            },
            {
                "rule": 5,
                "title": "删除即卸载",
                "detail": "删除 .py 文件即可卸载组件，下次热重载时自动清理配置残留",
            },
        ],
        "template_location": "components/_example_component.py",
        "note": "复制示例组件文件，改名为 your_tool.py 即可开始编写新组件",
    }

# -*- coding: utf-8 -*-
"""
组件管理 API — Agent 自扩展接口

提供运行时查看、扫描、注册/卸载组件的能力。
Agent 可以通过此 API 了解自身能力，并动态增删功能模块。

接口列表:
  GET    /api/components                     → 组件总览（已加载 + 可用命令）
  GET    /api/components/scan                → 扫描目录发现新组件（不加载）
  POST   /api/components/reload              → 热重载（增量扫描 + 自动注册/卸载）
  POST   /api/components/{name}/load         → 手动加载指定组件
  DELETE /api/components/{name}              → 卸载组件（默认连磁盘文件一起删）
  GET    /api/components/rules               → 查看组件使用铁律
  GET    /api/components/market/search       → 组件市场搜索
  GET    /api/components/market/info         → 组件市场元信息
  POST   /api/components/market/install      → 从组件市场下载安装
"""

import asyncio
import pathlib

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/components", tags=["components"])

_builder_fallback = None


def _is_builtin(name: str) -> bool:
    from app.core.util.component_tool_registry import DEFAULT_COMPONENT_MODULES
    return (name or "").lower() in DEFAULT_COMPONENT_MODULES


def _get_container():
    from app.core.di.container import get_container
    return get_container()


def _component_builder():
    """优先用已加载的 component 组件，未加载时回退到独立实例"""
    from app.core.util.components_loader import get_cell
    cell = get_cell("component")
    if cell is not None:
        return cell
    global _builder_fallback
    if _builder_fallback is None:
        from components.component_builder import ComponentBuilder
        _builder_fallback = ComponentBuilder()
    return _builder_fallback


class DepsInstallRequest(BaseModel):
    specs: list[str] = []


class MarketInstallRequest(BaseModel):
    slug: str = ""
    version: str = ""
    force: bool = False
    dry_run: bool = False
    install_deps: bool = False


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
        builtin = _is_builtin(name)
        result["components"].append({
            "cell_name": name,
            "class": type(cell).__name__,
            "commands": commands.get(name, {}),
            "command_count": len(commands.get(name, {})),
            "source_file": getattr(cell, '_source_file', None),
            "is_builtin": builtin,
            "removable": not builtin,
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
        from app.core.util.component_tool_registry import get_component_tool_registry
        report = hot_reload(container=container)
        tool_count = get_component_tool_registry().size
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    updated = report.get("updated", [])
    return {
        "status": "ok",
        "message": f"热重载完成",
        "added": report["added"],
        "removed": report["removed"],
        "updated": updated,
        "changes": len(report["added"]) + len(report["removed"]) + len(updated),
        "tools": tool_count,
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
    """手动加载单个组件，name 支持 cell_name / 类名 / module_path / 文件名"""
    from app.core.util.components_loader import (
        discover_components,
        _instantiate_component,
        register_cell,
        register_to_config,
        sync_tool_registry,
        _loaded_files,
    )
    from app.core.di.container import get_container

    key = (name or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="缺少组件名")

    def _rank(item) -> int:
        if str(item.get("module_path", "")).lower() == key:
            return 0
        if str(item.get("class_name", "")).lower() == key:
            return 1
        if pathlib.Path(str(item.get("file", ""))).stem.lower() == key:
            return 2
        return 3

    discovered = discover_components()
    ordered = sorted(discovered, key=_rank)

    target = None
    instance = None
    failures = []
    for item in ordered:
        exact = _rank(item) < 3
        cls_name = item.get("class_name", "")
        try:
            cls = item.get("cls")
            if cls is not None:
                candidate = cls()
            else:
                candidate, _info = _instantiate_component(item["module_path"])
            if candidate is None:
                raise RuntimeError("实例化为空")
        except Exception as e:
            from app.core.util.components_loader import (
                dependency_hint,
                missing_dependency_specs,
            )

            reason = f"{type(e).__name__}: {e}"
            hint = dependency_hint(e, str(item.get("file", "")))
            if hint:
                reason = f"{hint}（{reason}）"
            failures.append(f"{cls_name}: {reason}")
            if exact:
                specs = missing_dependency_specs(e, str(item.get("file", "")))
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": (
                            f"组件加载失败 [{cls_name}] ({item.get('module_path')}): {reason}"
                        ),
                        "component": cls_name,
                        "dependencies": {"targets": specs},
                    },
                )
            continue
        if exact or candidate.cell_name.lower() == key:
            target = item
            instance = candidate
            break

    if not target or instance is None:
        detail = f"未找到组件: {name}"
        if failures:
            detail += "；候选加载失败 → " + " | ".join(failures[:3])
        raise HTTPException(status_code=404, detail=detail)

    try:
        register_cell(instance)
        _loaded_files.add(target["file"])
        instance._source_file = target["file"]

        if hasattr(instance, "on_load"):
            instance.on_load()

        container = get_container()
        container.register(type(instance), instance)

        register_to_config(target["module_path"])

        tools = sync_tool_registry()

        return {
            "status": "ok",
            "message": f"组件 [{instance.cell_name}] 已加载",
            "tools": tools,
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
async def unload_component(name: str, delete_files: bool = True):
    """卸载组件；delete_files=True（默认）连磁盘文件一起删，内置组件不可卸载"""
    from app.core.util.components_loader import remove_component

    result = remove_component(name, delete_files=delete_files)

    if not result.get("success"):
        reason = result.get("reason")
        detail = result.get("error") or "卸载失败"
        if reason == "builtin":
            raise HTTPException(status_code=403, detail=detail)
        if reason == "not_found":
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=500, detail=detail)

    return {
        "status": "ok",
        "message": (
            f"组件 [{result['name']}] 已卸载并删除文件"
            if result.get("deleted")
            else f"组件 [{result['name']}] 已卸载"
        ),
        **result,
    }


@router.post("/deps/install")
async def install_dependencies(req: DepsInstallRequest):
    """安装组件依赖（用户确认后调用）"""
    specs = [str(s).strip() for s in (req.specs or []) if str(s).strip()]
    if not specs:
        raise HTTPException(status_code=400, detail="缺少依赖列表")

    result = await asyncio.to_thread(_component_builder()._pip_install, specs)
    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=(result.get("output") or result.get("error") or "pip install 失败")[-800:],
        )
    return result


@router.post("/upload")
async def upload_component(
    file: UploadFile = File(...),
    force: bool = False,
    install_deps: bool = False,
):
    """上传本地组件文件（.py 或压缩包）并安装"""
    import shutil
    import tempfile

    filename = pathlib.Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    if not filename.lower().endswith(
        (".py", ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
    ):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {filename}")

    limit = 50 * 1024 * 1024
    tmpdir = tempfile.mkdtemp(prefix="cellium_upload_")
    try:
        dest = pathlib.Path(tmpdir) / filename
        size = 0
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=400, detail="文件超过 50MB")
                f.write(chunk)

        result = await asyncio.to_thread(
            _component_builder()._cmd_install_local,
            path=str(dest),
            force=force,
            install_deps=install_deps,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error") or "安装失败")
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        await file.close()


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


# ════════════════════════════════════════════════════════════════
#  组件市场
# ════════════════════════════════════════════════════════════════

@router.get("/market/search")
def market_search(
    keyword: str = "",
    category: str = "",
    tags: str = "",
    page: int = 1,
    page_size: int = 10,
    refresh: bool = False,
):
    """搜索组件市场（同源 https://components.cellium.top）"""
    return _component_builder()._cmd_market_search(
        keyword=keyword,
        category=category,
        tags=tags,
        page=page,
        page_size=page_size,
        refresh=refresh,
    )


@router.get("/market/info")
def market_info(slug: str = "", version: str = ""):
    """查看市场组件详情：版本、安装包地址、校验值、README"""
    if not (slug or "").strip():
        raise HTTPException(status_code=400, detail="缺少参数 slug")
    return _component_builder()._cmd_market_info(slug=slug, version=version)


@router.post("/market/install")
def market_install(req: MarketInstallRequest):
    """从组件市场下载安装：下载 → 校验 → 审查 → 装入 → 热重载"""
    if not (req.slug or "").strip():
        raise HTTPException(status_code=400, detail="缺少参数 slug")
    return _component_builder()._cmd_market_install(
        slug=req.slug,
        version=req.version,
        force=req.force,
        dry_run=req.dry_run,
        install_deps=req.install_deps,
    )

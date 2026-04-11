# -*- coding: utf-8 -*-
"""
配置管理 API 路由 — 热重载接口

提供运行时查看、重载、修改配置的能力，无需重启 Agent 核心。

接口列表:
  GET  /api/config/status      → 配置文件状态总览
  GET  /api/config             → 查看当前完整配置（脱敏）
  GET  /api/config/{section}   → 查看指定配置段
  POST /api/config/reload      → 全量热重载
  POST /api/config/reload/{section} → 单段热重载
  PUT  /api/config/{section}   → 运行时修改配置（内存+可选持久化）
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

router = APIRouter(prefix="/api/config", tags=["config"])

def _get_agent_config():
    from app.core.util.agent_config import get_config
    return get_config()


def _get_original_api_key(name: str, new_api_key: str) -> str:
    """如果新 api_key 是 '***'，返回原配置中的值"""
    if new_api_key != '***':
        return new_api_key
    config = _get_agent_config()
    llm_section = config.get_section("llm")
    models = llm_section.get("models", [])
    for m in models:
        if m.get("name") == name:
            return m.get("api_key", new_api_key)
    presets = _load_models_presets()
    for p in presets:
        if p.get("name") == name:
            return p.get("api_key", new_api_key)
    return new_api_key


def _sync_model_to_llm_config(name: str, model_dict: Dict, add: bool = False):
    """将模型配置同步到 llm.models 列表"""
    import logging
    logger = logging.getLogger(__name__)

    model_dict = dict(model_dict)
    if model_dict.get("api_key") == '***':
        model_dict.pop("api_key", None)

    config = _get_agent_config()
    llm_section = config.get_section("llm")
    models = llm_section.get("models", [])
    current_model = llm_section.get("current_model", "")

    logger.info("[_sync_model_to_llm_config] name=%s add=%s current_model='%s' models_count=%d",
                name, add, current_model, len(models))

    for i, m in enumerate(models):
        if m.get("name") == name:
            models[i] = model_dict
            config.set("llm.models", models, persist=True)
            if not current_model:
                config.set("llm.current_model", name, persist=True)
                logger.info("[_sync_model_to_llm_config] 设置 current_model=%s", name)
            return

    if add:
        models.append(model_dict)
        config.set("llm.models", models, persist=True)

    if not current_model and models:
        new_current = models[-1].get("name", "")
        config.set("llm.current_model", new_current, persist=True)
        logger.info("[_sync_model_to_llm_config] 设置 current_model=%s", new_current)


def _remove_model_from_llm_config(name: str):
    """从 llm.models 列表移除模型"""
    config = _get_agent_config()
    llm_section = config.get_section("llm")
    models = llm_section.get("models", [])
    current_model = llm_section.get("current_model", "")

    models = [m for m in models if m.get("name") != name]
    config.set("llm.models", models, persist=True)

    if current_model == name:
        if models:
            config.set("llm.current_model", models[0].get("name", ""), persist=True)
        else:
            config.set("llm.current_model", "", persist=True)


class ConfigValue(BaseModel):
    """动态设置配置值"""
    value: Any
    persist: bool = False  


@router.get("/status")
async def config_status():
    """配置文件状态：哪些文件存在、大小、贡献了哪些段"""
    config = _get_agent_config()
    return {
        "config_dir": str(config.config_dir),
        "auto_reload": config.auto_reload,
        "loaded_sections": config.sections,
        "files": config.list_files(),
    }


@router.get("")
async def get_all_config():
    """查看当前完整配置"""
    config = _get_agent_config()
    raw = config.raw
    # 脱敏处理
    return _sanitize(raw)

@router.get("/{section}")
async def get_section(section: str):
    """获取指定配置段"""
    config = _get_agent_config()
    if section not in config.sections:
        raise HTTPException(status_code=404, detail=f"配置段不存在: {section}")
    
    data = config.get_section(section)
    # 只有 llm 和 security 配置段需要脱敏
    if section in ("llm", "security"):
        return {"section": section, **_sanitize(data)}
    return {"section": section, **data}


@router.post("/reload")
async def reload_all():
    """
    全量热重载所有配置文件
    
    会重新读取 config/agent/ 下所有 .yaml 并深度合并，
    对比旧配置后触发变更回调通知各模块。
    """
    config = _get_agent_config()
    changes = config.reload()

    return {
        "status": "ok",
        "message": f"全量重载完成，{len(changes)} 个配置段有变更",
        "changes": changes,
        "current_sections": config.sections,
    }


@router.post("/reload/{section}")
async def reload_section(section: str):
    """仅重载指定的配置段（高效增量重载）"""
    config = _get_agent_config()

    if section not in config.sections:
        raise HTTPException(status_code=404, detail=f"配置段不存在: {section}")

    success = config.reload_section(section)

    if success:
        return {
            "status": "ok",
            "message": f"配置段 [{section}] 已热重载",
            "section": section,
            "data": config.get_section(section),
        }
    else:
        return {
            "status": "warning",
            "message": f"配置段 [{section}] 无变更或加载失败",
            "section": section,
        }


@router.put("/{section:path}")
async def update_section(
    section: str,
    body: ConfigValue,
):
    """
    运行时修改配置

    Args:
        section: 点号路径，如 llm.openai.model 或 security.permission_level
        body.value: 新值
        body.persist: 是否同时写回 YAML 文件（默认仅内存生效）
    """
    config = _get_agent_config()

    # 验证安全限制（不允许通过 API 修改某些关键项）
    _forbidden_prefixes = ("server.host", "server.port", "routes.")
    for prefix in _forbidden_prefixes:
        if section.startswith(prefix):
            raise HTTPException(
                status_code=403,
                detail=f"运行时禁止修改关键配置: {prefix}*（需重启服务）"
            )

    old_value = config.get(section)
    
    # 保护敏感字段：如果新值中的敏感字段是脱敏值 "***"，则保留原始值
    protected_value = _protect_sensitive_fields(body.value, old_value)
    
    config.set(section, protected_value, persist=body.persist)

    return {
        "status": "ok",
        "section": section,
        "old_value": _sanitize_value(old_value),
        "new_value": _sanitize_value(body.value),
        "persisted": body.persist,
        "note": (
            "已写入内存并同步到文件，下次启动将保留"
            if body.persist else
            "已写入内存（进程重启后丢失），设 persist=true 可写回文件"
        ),
    }


@router.post("/auto-reload")
async def toggle_auto_reload(enabled: bool = True):
    """开启/关闭文件变更自动检测"""
    config = _get_agent_config()
    config.auto_reload = enabled
    return {
        "status": "ok",
        "auto_reload": enabled,
        "message": ("已开启自动检测（每次读取前检查文件 mtime）"
                   if enabled else "已关闭自动检测"),
    }


@router.get("/validate")
async def validate_config():
    """校验当前配置合法性"""
    config = _get_agent_config()
    errors = config.validate()
    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True, "errors": {}, "message": "配置校验通过"}


# ============================================================
# 工具函数
# ============================================================

_SENSITIVE_KEYS = ("api_key", "key", "password", "secret", "token")


def _recursive_sanitize(obj: Any) -> Any:
    """递归脱敏处理"""
    if isinstance(obj, dict):
        sanitized = {}
        for key, value in obj.items():
            # 只有字符串类型的值才需要脱敏，数字类型不需要
            if isinstance(value, str) and _is_sensitive_key(key):
                sanitized[key] = "***"
            else:
                sanitized[key] = _recursive_sanitize(value)
        return sanitized
    elif isinstance(obj, list):
        return [_recursive_sanitize(item) for item in obj]
    return obj


def _is_sensitive_key(key: str) -> bool:
    """
    判断 key 是否为敏感字段
    
    规则：
    - 完全匹配敏感词（如 api_key, password）
    - 以敏感词结尾（如 openai_api_key）
    - 不匹配包含敏感词但不是敏感字段的情况（如 token_threshold）
    """
    key_lower = key.lower()
    for sensitive in _SENSITIVE_KEYS:
        if key_lower == sensitive:
            return True
        if key_lower.endswith('_' + sensitive):
            return True
        if key_lower.endswith(sensitive):
            return True
    return False


def _sanitize(data: Dict[str, Any]) -> Dict[str, Any]:
    """递归脱敏整个字典"""
    import copy
    result = copy.deepcopy(data)
    return _recursive_sanitize(result)


def _sanitize_value(value: Any) -> Any:
    """单个值的脱敏"""
    if isinstance(value, str) and any(s in value.lower() for s in _SENSITIVE_KEYS):
        if len(value) > 8:
            return value[:4] + "***" + value[-4:]
        return "***"
    return value


def _protect_sensitive_fields(new_value: Any, old_value: Any) -> Any:
    """
    保护敏感字段：如果新值中的敏感字段是脱敏值 "***"，则保留原始值

    这解决了前端获取配置时敏感字段被脱敏，保存时脱敏值覆盖原始值的问题
    """
    if isinstance(new_value, dict) and isinstance(old_value, dict):
        import copy
        result = copy.deepcopy(new_value)

        def _recursive_protect(new_obj: Any, old_obj: Any):
            """递归保护敏感字段，支持字典和列表"""
            if isinstance(new_obj, dict) and isinstance(old_obj, dict):
                for key, value in new_obj.items():
                    if key in old_obj:
                        if isinstance(value, dict) and isinstance(old_obj[key], dict):
                            _recursive_protect(value, old_obj[key])
                        elif isinstance(value, list) and isinstance(old_obj[key], list):
                            _recursive_protect(value, old_obj[key])
                        elif isinstance(value, str) and _is_sensitive_key(key):
                            # 如果新值是脱敏值，使用旧值
                            if value == "***" or (len(value) > 4 and value[4:-4].count('*') >= 3):
                                new_obj[key] = old_obj[key]
            elif isinstance(new_obj, list) and isinstance(old_obj, list):
                # 处理列表：按索引匹配
                for i, (new_item, old_item) in enumerate(zip(new_obj, old_obj)):
                    if isinstance(new_item, dict) and isinstance(old_item, dict):
                        _recursive_protect(new_item, old_item)

        _recursive_protect(result, old_value)
        return result

    return new_value


# ============================================================
# 模型预设 API
# ============================================================

class ModelSwitchRequest(BaseModel):
    """切换模型请求"""
    name: str
    provider: str = "openai"
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.7
    timeout: int = 120


class LocalModelListRequest(BaseModel):
    """获取本地模型列表请求"""
    base_url: str


@router.post("/model/list-local")
async def list_local_models(body: LocalModelListRequest):
    """
    获取本地服务的可用模型列表
    
    支持 LM Studio, Ollama, vLLM 等 OpenAI 兼容服务
    """
    import httpx
    import logging
    
    logger = logging.getLogger(__name__)
    base_url = body.base_url.rstrip("/")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/models")
            
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", data.get("models", []))
                
                if isinstance(models, list):
                    return {"models": models}
            
            return {"models": []}
            
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"无法连接到 {base_url}，请确认服务已启动"
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail=f"连接 {base_url} 超时"
        )
    except Exception as e:
        logger.error("[ConfigAPI] 获取本地模型列表失败: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"获取模型列表失败: {str(e)}"
        )


@router.post("/model/switch")
async def switch_model(body: ModelSwitchRequest):
    """
    切换当前使用的模型

    只需更新 llm.current_model，模型配置已存储在 llm.models 列表中
    """
    import logging
    logger = logging.getLogger(__name__)

    config = _get_agent_config()

    llm_section = config.get_section("llm")
    models = llm_section.get("models", [])

    model_exists = any(m.get("name") == body.name for m in models)
    if not model_exists:
        raise HTTPException(status_code=400, detail=f"模型不存在: {body.name}")

    config.set("llm.current_model", body.name, persist=True)

    logger.info(
        "[ConfigAPI] 模型已切换 | name=%s",
        body.name
    )

    return {
        "status": "ok",
        "message": f"已切换到模型: {body.name}",
        "config": {
            "current_model": body.name,
        }
    }


@router.post("/model/reload-engine")
async def reload_llm_engine():
    """
    重新加载 LLM 引擎（切换模型后需要调用）

    会直接更新现有 AgentLoop 的 llm 引用，而不是替换整个实例
    """
    import logging
    from app.core.di.container import get_container
    from app.agent.llm.engine import create_llm_engine
    from app.agent.loop.agent_loop import AgentLoop

    logger = logging.getLogger(__name__)

    config = _get_agent_config()
    # 强制从文件重载 llm 配置，确保获取最新的 API key
    config.reload_section("llm")
    llm_config = config.get_section("llm")

    if not llm_config:
        raise HTTPException(status_code=500, detail="LLM 配置不存在")

    try:
        container = get_container()

        new_engine = create_llm_engine(llm_config)

        # 更新 DI 容器中的引擎注册
        from app.agent.llm.engine import BaseLLMEngine
        container.register(BaseLLMEngine, new_engine, singleton=True)
        logger.info("[ConfigAPI] BaseLLMEngine 已重新注册 | 新模型=%s", new_engine.model)

        if container.has(AgentLoop):
            old_loop = container.resolve(AgentLoop)
            old_loop.llm = new_engine
            logger.info("[ConfigAPI] AgentLoop.llm 已更新 | 新模型=%s", new_engine.model)
        else:
            logger.warning("[ConfigAPI] AgentLoop 未注册，跳过 llm 更新")

        return {
            "status": "ok",
            "message": "LLM 引擎已重新加载",
            "model": new_engine.model,
        }

    except Exception as e:
        logger.error("[ConfigAPI] 重新加载 LLM 引擎失败: %s", str(e))
        raise HTTPException(status_code=500, detail=f"重新加载失败: {str(e)}")


# ============================================================
# 模型预设管理 API（存储在 settings.yaml）
# ============================================================

class ModelPreset(BaseModel):
    name: str
    provider: str = "openai"
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.7
    timeout: int = 120


class ModelPresetsRequest(BaseModel):
    models: list[ModelPreset]


_MODELS_CONFIG_PATH: str | None = None


def _get_models_config_path() -> str:
    global _MODELS_CONFIG_PATH
    if _MODELS_CONFIG_PATH:
        return _MODELS_CONFIG_PATH
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    _MODELS_CONFIG_PATH = str(base_dir / "config" / "models.yaml")
    return _MODELS_CONFIG_PATH


def _load_models_presets() -> list[dict]:
    import yaml
    path = _get_models_config_path()
    if not Path(path).exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("model_presets", []) if data else []


def _save_models_presets(presets: list[dict]):
    import yaml
    path = _get_models_config_path()
    llm_section = _get_agent_config().get_section("llm")
    llm_models = {m.get("name"): m for m in llm_section.get("models", [])}
    for p in presets:
        if p.get("api_key") == "***" and p.get("name") in llm_models:
            p["api_key"] = llm_models[p["name"]].get("api_key", "")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"model_presets": presets}, f, default_flow_style=False, allow_unicode=True)


@router.get("/models")
async def list_models():
    """获取所有已保存的模型预设（脱敏 api_key）"""
    presets = _load_models_presets()
    sanitized = []
    for p in presets:
        p = dict(p)
        if p.get("api_key"):
            p["api_key"] = "***"
        sanitized.append(p)
    return {"models": sanitized}


@router.post("/models")
async def save_models(body: ModelPresetsRequest):
    """保存模型预设列表（覆盖式）"""
    presets = [m.model_dump() for m in body.models]
    _save_models_presets(presets)
    return {"status": "ok", "count": len(presets)}


@router.post("/model")
async def add_or_update_model(body: ModelPreset):
    """添加或更新单个模型预设（同时更新 llm.models）"""
    presets = _load_models_presets()
    model_dict = body.model_dump()

    for i, p in enumerate(presets):
        if p.get("name") == body.name:
            presets[i] = model_dict
            _save_models_presets(presets)
            _sync_model_to_llm_config(body.name, model_dict)
            return {"status": "ok", "message": f"模型 [{body.name}] 已更新", "model": model_dict}

    presets.append(model_dict)
    _save_models_presets(presets)
    _sync_model_to_llm_config(body.name, model_dict, add=True)
    return {"status": "ok", "message": f"模型 [{body.name}] 已添加", "model": model_dict}


@router.delete("/model/{name}")
async def delete_model(name: str):
    """删除指定名称的模型预设（同时从 llm.models 移除）"""
    presets = _load_models_presets()
    original_len = len(presets)
    presets = [p for p in presets if p.get("name") != name]

    if len(presets) == original_len:
        raise HTTPException(status_code=404, detail=f"未找到模型: {name}")

    _save_models_presets(presets)
    _remove_model_from_llm_config(name)
    return {"status": "ok", "message": f"模型 [{name}] 已删除"}

# -*- coding: utf-8 -*-
"""
长期记忆管理 API 路由
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agent.memory.three_layer import ThreeLayerMemory
from app.core.di.container import get_container

router = APIRouter(prefix="/api/memories", tags=["memory"])


class MemoryUpsertRequest(BaseModel):
    title: str
    content: str
    category: str = "general"
    tags: str = ""
    schema_type: str = "general"
    memory_key: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    allow_sensitive: bool = False
    merge_strategy: str = "merge"


class MemoryUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[str] = None
    schema_type: Optional[str] = None
    memory_key: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    allow_sensitive: bool = False


class MemoryForgetRequest(BaseModel):
    query: Optional[str] = None
    source: Optional[str] = None
    memory_key: Optional[str] = None
    all_matches: bool = False


class MemoryMergeRequest(BaseModel):
    memory_key: Optional[str] = None
    schema_type: Optional[str] = None


def _get_memory_service() -> ThreeLayerMemory:
    container = get_container()
    if not container.has(ThreeLayerMemory):
        raise HTTPException(status_code=503, detail="长期记忆系统未初始化")
    return container.resolve(ThreeLayerMemory)


@router.get("/summary")
async def memory_summary():
    memory = _get_memory_service()
    return memory.summarize_memories()


@router.get("")
async def list_or_search_memories(
    query: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    schema_type: Optional[str] = Query(default=None),
    include_sensitive: bool = Query(default=False),
    include_deleted: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    memory = _get_memory_service()
    normalized_query = (query or "").strip()
    if normalized_query:
        all_results = memory.search_memories(
            normalized_query,
            top_k=limit + offset,
            category=category,
            schema_type=schema_type,
            include_sensitive=include_sensitive,
        )
        mode = "search"
        total = len(all_results)
        items = all_results[offset : offset + limit]
    else:
        result = memory.list_memories(
            category=category,
            schema_type=schema_type,
            include_deleted=include_deleted,
            include_sensitive=include_sensitive,
            limit=limit,
            offset=offset,
        )
        mode = "list"
        items = result.get("items", [])
        total = result.get("total", 0)

    return {
        "mode": mode,
        "query": normalized_query,
        "total": total,
        "items": items,
        "filters": {
            "category": category,
            "schema_type": schema_type,
            "include_sensitive": include_sensitive,
            "include_deleted": include_deleted,
            "limit": limit,
            "offset": offset,
        },
    }


@router.get("/{memory_id}")
async def get_memory(memory_id: str):
    memory = _get_memory_service()
    record = memory.get_memory(memory_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"记忆不存在: {memory_id}")
    return record


@router.post("")
async def create_memory(body: MemoryUpsertRequest):
    memory = _get_memory_service()
    result = memory.upsert_memory(
        title=body.title,
        content=body.content,
        category=body.category,
        tags=body.tags,
        schema_type=body.schema_type,
        memory_key=body.memory_key,
        metadata=body.metadata,
        allow_sensitive=body.allow_sensitive,
        merge_strategy=body.merge_strategy,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "写入失败"))
    return {**result, "memory": memory.get_memory(str(result.get("id")))}


@router.put("/{memory_id}")
async def update_memory(memory_id: str, body: MemoryUpdateRequest):
    memory = _get_memory_service()
    result = memory.update_memory(
        identifier=memory_id,
        title=body.title,
        content=body.content,
        category=body.category,
        tags=body.tags,
        schema_type=body.schema_type,
        memory_key=body.memory_key,
        metadata=body.metadata or {},
        allow_sensitive=body.allow_sensitive,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "更新失败"))
    return {**result, "memory": memory.get_memory(memory_id)}


@router.delete("/{memory_id}")
async def remove_memory(
    memory_id: str,
    mode: str = Query(default="delete", pattern="^(delete|forget)$"),
    reason: str = Query(default="deleted"),
):
    memory = _get_memory_service()
    record = memory.get_memory(memory_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"记忆不存在: {memory_id}")

    if mode == "forget":
        result = memory.forget_memories(source=record.get("source_file"), memory_key=record.get("memory_key"))
    else:
        result = memory.delete_memory(identifier=memory_id, reason=reason)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "删除失败"))
    return result


@router.post("/actions/forget")
async def forget_memories(body: MemoryForgetRequest):
    memory = _get_memory_service()
    if not body.query and not body.source and not body.memory_key:
        raise HTTPException(status_code=400, detail="forget 需要 query、source 或 memory_key")

    result = memory.forget_memories(
        query=body.query,
        source=body.source,
        memory_key=body.memory_key,
        all_matches=body.all_matches,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "遗忘失败"))
    return result


@router.post("/actions/merge")
async def merge_memories(body: MemoryMergeRequest):
    memory = _get_memory_service()
    if not body.memory_key and not body.schema_type:
        raise HTTPException(status_code=400, detail="merge 需要至少提供 memory_key 或 schema_type")

    result = memory.merge_conflicts(memory_key=body.memory_key, schema_type=body.schema_type)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "冲突合并失败"))
    return result

# -*- coding: utf-8 -*-
"""
Gene 管理 API 路由
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.agent.memory.three_layer import ThreeLayerMemory
from app.core.di.container import get_container

router = APIRouter(prefix="/api/genes", tags=["gene"])


class GeneResponse(BaseModel):
    id: str
    task_type: str
    title: str
    content: str
    version: int
    usage_count: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_reward: float
    avg_duration_ms: float
    consecutive_success: int
    consecutive_failure: int
    evolution_history: List[Dict[str, Any]]
    recent_results: List[Dict[str, Any]]
    created_at: Optional[str]
    updated_at: Optional[str]


class GeneListResponse(BaseModel):
    items: List[GeneResponse]
    total: int


class GeneUpdateRequest(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None


class GeneStatsResponse(BaseModel):
    total_genes: int
    total_usage: int
    avg_success_rate: float
    evolved_genes: int


def _get_memory_service() -> ThreeLayerMemory:
    container = get_container()
    if not container.has(ThreeLayerMemory):
        raise HTTPException(status_code=503, detail="记忆系统未初始化")
    return container.resolve(ThreeLayerMemory)


def _memory_to_gene(memory: Dict[str, Any]) -> GeneResponse:
    metadata = memory.get("metadata", {})
    return GeneResponse(
        id=memory.get("memory_key", ""),
        task_type=metadata.get("task_type", ""),
        title=memory.get("title", ""),
        content=memory.get("content", ""),
        version=metadata.get("version", 1),
        usage_count=metadata.get("usage_count", 0),
        success_count=metadata.get("success_count", 0),
        failure_count=metadata.get("failure_count", 0),
        success_rate=metadata.get("success_rate", 0.0),
        avg_reward=metadata.get("avg_reward", 0.0),
        avg_duration_ms=metadata.get("avg_duration_ms", 0.0),
        consecutive_success=metadata.get("consecutive_success", 0),
        consecutive_failure=metadata.get("consecutive_failure", 0),
        evolution_history=metadata.get("evolution_history", []),
        recent_results=metadata.get("recent_results", []),
        created_at=memory.get("created_at"),
        updated_at=memory.get("updated_at"),
    )


@router.get("", response_model=GeneListResponse)
async def list_genes(
    query: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    memory = _get_memory_service()
    result = memory.list_memories(
        schema_type="control_gene",
        limit=limit,
        offset=offset,
    )
    items = [_memory_to_gene(item) for item in result.get("items", [])]
    return GeneListResponse(items=items, total=result.get("total", 0))


@router.get("/stats", response_model=GeneStatsResponse)
async def get_gene_stats():
    memory = _get_memory_service()
    result = memory.list_memories(
        schema_type="control_gene",
        limit=1000,
        offset=0,
    )
    items = result.get("items", [])
    
    total_genes = len(items)
    total_usage = sum(item.get("metadata", {}).get("usage_count", 0) for item in items)
    
    success_rates = [
        item.get("metadata", {}).get("success_rate", 0.0) 
        for item in items 
        if item.get("metadata", {}).get("usage_count", 0) > 0
    ]
    avg_success_rate = sum(success_rates) / len(success_rates) if success_rates else 0.0
    
    evolved_genes = sum(
        1 for item in items 
        if item.get("metadata", {}).get("evolved", False)
    )
    
    return GeneStatsResponse(
        total_genes=total_genes,
        total_usage=total_usage,
        avg_success_rate=avg_success_rate,
        evolved_genes=evolved_genes,
    )


@router.get("/{gene_id}", response_model=GeneResponse)
async def get_gene(gene_id: str):
    memory = _get_memory_service()
    results = memory.search_memories(
        query=f"gene:{gene_id}",
        schema_type="control_gene",
        top_k=1,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Gene 不存在")
    return _memory_to_gene(results[0])


@router.put("/{gene_id}", response_model=GeneResponse)
async def update_gene(gene_id: str, request: GeneUpdateRequest):
    memory = _get_memory_service()
    
    results = memory.search_memories(
        query=f"gene:{gene_id}",
        schema_type="control_gene",
        top_k=1,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Gene 不存在")
    
    existing = results[0]
    metadata = existing.get("metadata", {})
    
    current_version = metadata.get("version", 1)
    new_version = current_version + 1
    
    evolution_history = metadata.get("evolution_history", [])
    evolution_history.append({
        "version": new_version,
        "change": "manual edit",
        "at": datetime.now().isoformat(),
    })
    
    updated_metadata = {
        **metadata,
        "version": new_version,
        "evolution_history": evolution_history,
        "evolved": True,
    }
    
    memory.upsert_memory(
        title=request.title or existing.get("title", ""),
        content=request.content or existing.get("content", ""),
        schema_type="control_gene",
        category="task_strategy",
        memory_key=gene_id,
        metadata=updated_metadata,
    )
    
    return _memory_to_gene({
        **existing,
        "title": request.title or existing.get("title", ""),
        "content": request.content or existing.get("content", ""),
        "metadata": updated_metadata,
    })


@router.post("/{gene_id}/evolve")
async def evolve_gene(gene_id: str):
    memory = _get_memory_service()

    results = memory.search_memories(
        query=f"gene:{gene_id}",
        schema_type="control_gene",
        top_k=1,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Gene 不存在")

    existing = results[0]
    content = existing.get("content", "")
    metadata = existing.get("metadata", {})

    recent_results = metadata.get("recent_results", [])
    failure_count = metadata.get("failure_count", 0)
    consecutive_failure = metadata.get("consecutive_failure", 0)

    new_avoid_cues = []

    if consecutive_failure >= 3:
        new_avoid_cues.append(f"DON'T: Repeat patterns causing {consecutive_failure} consecutive failures")

    if failure_count > 0 and metadata.get("usage_count", 0) > 0:
        failure_rate = failure_count / metadata.get("usage_count", 1)
        if failure_rate > 0.5:
            new_avoid_cues.append(f"DON'T: Approaches with {failure_rate:.0%} failure rate")

    failed_results = [r for r in recent_results[-10:] if not r.get("success", True)]
    if failed_results:
        avg_fail_reward = sum(r.get("reward", 0) for r in failed_results) / len(failed_results)
        if avg_fail_reward < 0.3:
            new_avoid_cues.append(f"DON'T: Low-reward strategies (avg {avg_fail_reward:.2f})")

    avoid_section = "[AVOID]"
    updated_content = content

    for cue in new_avoid_cues:
        if cue not in updated_content:
            if avoid_section in updated_content:
                updated_content = updated_content.replace(
                    avoid_section,
                    f"{avoid_section}\n- {cue}"
                )
            else:
                updated_content += f"\n\n{avoid_section}\n- {cue}"

    current_version = metadata.get("version", 1)
    new_version = current_version + 1

    evolution_history = metadata.get("evolution_history", [])
    change_desc = "manual evolution"
    if new_avoid_cues:
        change_desc = f"auto-extract: {len(new_avoid_cues)} avoid cues"

    evolution_history.append({
        "version": new_version,
        "change": change_desc,
        "at": datetime.now().isoformat(),
    })

    updated_metadata = {
        **metadata,
        "version": new_version,
        "evolution_history": evolution_history,
        "evolved": True,
    }

    memory.upsert_memory(
        title=existing.get("title", ""),
        content=updated_content,
        schema_type="control_gene",
        category="task_strategy",
        memory_key=gene_id,
        metadata=updated_metadata,
    )

    return {
        "success": True,
        "new_version": new_version,
        "avoid_cues_added": len(new_avoid_cues),
        "content_changed": updated_content != content,
    }


@router.delete("/{gene_id}")
async def delete_gene(gene_id: str):
    memory = _get_memory_service()

    results = memory.search_memories(
        query=f"gene:{gene_id}",
        schema_type="control_gene",
        top_k=1,
    )
    if not results:
        raise HTTPException(status_code=404, detail="Gene 不存在")

    existing = results[0]
    memory_key = existing.get("memory_key", gene_id)

    memory.delete_memory(memory_key=memory_key)

    return {"success": True, "deleted_id": gene_id}

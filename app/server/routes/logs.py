# -*- coding: utf-8 -*-
"""
日志查询 API — Agent 读取自身运行日志

让 Agent 能通过 HTTP API 查询运行时日志，
用于自我诊断、错误追踪和状态感知。

接口列表:
  GET  /api/logs              → 最近日志（支持多维度过滤）
  GET  /api/logs/stats        → 日志缓冲区统计
  GET  /api/logs/errors       → 仅错误日志
  GET  /api/logs/export       → 纯文本导出
  DELETE /api/logs            → 清空缓冲区
"""

from fastapi import APIRouter, Query
from typing import Optional

from app.core.util.logger import get_buffer

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def query_logs(
    level: Optional[str] = Query(None, description="过滤级别: DEBUG/INFO/WARNING/ERROR/CRITICAL"),
    logger: Optional[str] = Query(None, alias="logger_name", description="Logger 名称过滤（部分匹配）"),
    keyword: Optional[str] = Query(None, description="消息关键词搜索"),
    since: Optional[str] = Query(None, description="时间窗口起点 (如 '5m', '1h', '30s')"),
    until: Optional[str] = Query(None, description="时间窗口终点"),
    limit: int = Query(100, ge=1, le=1000, description="返回条数上限"),
    offset: int = Query(0, ge=0, description="跳过前N条"),
    reverse: bool = Query(True, description="最新在前=True, 最旧在前=False"),
):
    """
    多条件查询运行日志

    示例:
      /api/logs                           → 最近100条
      /api/logs?level=ERROR               → 所有错误日志
      /api/logs?keyword=组件&limit=50     → 含"组件"的最近50条
      /api/logs?since=5m&level=WARNING    → 最近5分钟的警告
      /api/logs?logger=agent&since=1h     → agent logger最近1小时
    """
    buffer = get_buffer()

    entries = buffer.query(
        level=level,
        logger_name=logger,
        keyword=keyword,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
        reverse=reverse,
    )

    stats = buffer.stats()

    return {
        "query": {
            "level": level,
            "logger": logger,
            "keyword": keyword,
            "since": since,
            "until": until,
            "limit": limit,
            "offset": offset,
        },
        "total_matched": len(entries),
        "buffer_stats": {
            "size": stats["size"],
            "capacity": stats["capacity"],
            "utilization_pct": stats["utilization_pct"],
        },
        "logs": [e.to_dict() for e in entries],
    }


@router.get("/stats")
async def log_stats():
    """日志缓冲区统计信息"""
    buffer = get_buffer()
    stats = buffer.stats()
    return stats


@router.get("/status")
async def runtime_status():
    """运行时状态摘要（供 Agent 自我感知）"""
    from app.core.util.logger import get_runtime_status
    rs = get_runtime_status()
    if not rs:
        return {"available": False, "message": "Agent 未在运行"}
    return {
        "available": True,
        "summary": rs.to_summary(),
        "detail": rs.to_dict(),
    }


@router.get("/status/history")
async def runtime_status_history():
    """运行时状态历史快照"""
    from app.core.util.logger import get_status_history
    return {"history": get_status_history()}


@router.get("/errors")
async def error_logs(
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
):
    """获取最近的 ERROR 和 CRITICAL 日志"""
    buffer = get_buffer()

    errors = buffer.query(level="ERROR", limit=limit)
    criticals = buffer.query(level="CRITICAL", limit=limit)

    # 合并去重（按时间戳）
    all_errors = {e.timestamp: e for e in errors + criticals}
    sorted_errors = sorted(all_errors.values(), key=lambda x: x.timestamp, reverse=True)[:limit]

    return {
        "total_error_count": len(errors),
        "total_critical_count": len(criticals),
        "returned": len(sorted_errors),
        "logs": [e.to_dict() for e in sorted_errors],
    }


@router.get("/export")
async def export_logs(
    level: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    format: str = Query("text", description="输出格式: text | json"),
):
    """
    导出日志

    - format=text: 纯文本，Agent 可直接阅读
    - format=json: 结构化 JSON 数组
    """
    buffer = get_buffer()

    if format == "text":
        text = buffer.export_text(level=level, limit=limit)
        return {
            "format": "text",
            "line_count": text.count("\n") + 1 if text else 0,
            "content": text,
        }
    else:
        entries = buffer.query(level=level, limit=limit)
        return {
            "format": "json",
            "count": len(entries),
            "logs": [e.to_dict() for e in entries],
        }


@router.delete("")
async def clear_logs():
    """清空日志缓冲区"""
    buffer = get_buffer()
    size_before = len(buffer._buffer)  # noqa: 直接访问内部状态
    buffer.clear()
    return {
        "status": "ok",
        "message": f"已清空 {size_before} 条日志",
        "cleared_count": size_before,
    }

# -*- coding: utf-8 -*-
"""文件上传 API"""

from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.util import attachment_store as store

router = APIRouter(prefix="/api", tags=["upload"])


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    file_type: str
    file_size: int
    url: str
    local_path: str
    upload_time: str
    thumb_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    vision_local_path: Optional[str] = None


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    vision: Optional[UploadFile] = File(None),
    thumb: Optional[UploadFile] = File(None),
):
    """上传单个文件；图片可同时上传浏览器生成的视觉副本与缩略图"""
    try:
        return UploadResponse(**store.save_stream(file.file, file.filename or "file", vision=vision.file if vision else None, thumb=thumb.file if thumb else None))
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))


@router.get("/files/{file_id}")
async def get_file(file_id: str, download: bool = False, thumb: bool = False):
    """获取上传的文件；图片/文本默认内联，其余下载"""
    if thumb:
        preview = store.find_thumb(file_id)
        if preview:
            return FileResponse(
                preview,
                media_type="image/webp",
                headers={"Cache-Control": "private, max-age=86400"},
            )

    result = store.find_file(file_id)
    if not result:
        raise HTTPException(status_code=404, detail="文件不存在")

    file_path, original_name = result
    inline = store.is_inline_type(original_name) and not download
    from urllib.parse import quote

    disposition = "inline" if inline else "attachment"
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(original_name)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=86400",
    }
    return FileResponse(
        file_path,
        media_type=store.media_type_for(original_name),
        headers=headers,
    )


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """删除文件"""
    result = store.find_file(file_id)
    if not result:
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        import os
        os.remove(result[0])
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}")
    return {"status": "deleted", "file_id": file_id}

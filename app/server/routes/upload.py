# -*- coding: utf-8 -*-
"""文件上传 API"""

import os
import uuid
import shutil
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api", tags=["upload"])

# 使用绝对路径，避免工作目录影响
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "workspace", "uploads"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 文件类型分类
ALLOWED_EXTENSIONS = {
    'image': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico', '.tiff', '.tif'],
    'document': ['.pdf', '.doc', '.docx', '.txt', '.md', '.csv', '.xlsx', '.xls', '.ppt', '.pptx', '.rtf', '.odt', '.ods', '.odp'],
    'code': ['.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.yaml', '.yml', '.xml', '.html', '.css', '.scss', '.sass', '.less', 
             '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala', 
             '.sh', '.bash', '.zsh', '.bat', '.cmd', '.ps1', '.sql', '.vue', '.svelte', '.dart', '.lua', '.r', '.m', '.pl'],
    'archive': ['.zip', '.tar', '.gz', '.rar', '.7z', '.bz2', '.xz', '.tgz', '.tar.gz', '.tar.bz2'],
    'audio': ['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.wma'],
    'video': ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'],
    'data': ['.db', '.sqlite', '.sqlite3', '.jsonl', '.parquet', '.pkl', '.pickle', '.h5', '.hdf5'],
    'config': ['.ini', '.conf', '.cfg', '.env', '.toml', '.properties'],
    'font': ['.ttf', '.otf', '.woff', '.woff2', '.eot'],
    'binary': ['.exe', '.dll', '.so', '.dylib', '.bin', '.dat']
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


class UploadResponse(BaseModel):
    file_id: str
    filename: str
    file_type: str
    file_size: int
    url: str
    local_path: str 
    upload_time: str


class FileInfo(BaseModel):
    file_id: str
    filename: str
    file_type: str
    file_size: int
    upload_time: str


def get_file_type(filename: str) -> str:
    """根据扩展名判断文件类型，未知类型返回 'other'"""
    ext = os.path.splitext(filename)[1].lower()
    for file_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return 'other'  


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """上传单个文件"""
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件大小超过限制")
    
    file_id = str(uuid.uuid4())
    file_type = get_file_type(file.filename or "unknown")
    timestamp = datetime.now().strftime("%Y%m%d")
    
    save_dir = os.path.join(UPLOAD_DIR, timestamp)
    os.makedirs(save_dir, exist_ok=True)
    
    file_path = os.path.join(save_dir, f"{file_id}_{file.filename}")
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    abs_file_path = os.path.abspath(file_path)
    
    return UploadResponse(
        file_id=file_id,
        filename=file.filename or "unknown",
        file_type=file_type,
        file_size=file_size,
        url=f"/api/files/{file_id}",
        local_path=abs_file_path,
        upload_time=datetime.now().isoformat()
    )


def find_file_by_id(file_id: str) -> tuple[str, str] | None:
    """
    高效查找文件
    
    优化策略：
    1. 先检查最近的日期目录（今天、昨天、前天）
    2. 如果找到就返回，避免全局遍历
    3. 如果没找到，再遍历所有日期目录
    
    返回：(file_path, original_name) 或 None
    """
    # 生成最近3天的日期目录
    recent_dates = [
        datetime.now().strftime("%Y%m%d"),
        (datetime.now() - __import__('datetime').timedelta(days=1)).strftime("%Y%m%d"),
        (datetime.now() - __import__('datetime').timedelta(days=2)).strftime("%Y%m%d"),
    ]
    
    # 先检查最近的日期目录
    for date_dir in recent_dates:
        dir_path = os.path.join(UPLOAD_DIR, date_dir)
        if not os.path.exists(dir_path):
            continue
        
        for filename in os.listdir(dir_path):
            if filename.startswith(file_id):
                file_path = os.path.join(dir_path, filename)
                original_name = filename.split('_', 1)[1] if '_' in filename else filename
                return (file_path, original_name)
    
    # 如果最近目录没找到，再遍历所有日期目录
    for date_dir in os.listdir(UPLOAD_DIR):
        dir_path = os.path.join(UPLOAD_DIR, date_dir)
        if not os.path.isdir(dir_path):
            continue
        
        # 跳过已经检查过的最近目录
        if date_dir in recent_dates:
            continue
        
        for filename in os.listdir(dir_path):
            if filename.startswith(file_id):
                file_path = os.path.join(dir_path, filename)
                original_name = filename.split('_', 1)[1] if '_' in filename else filename
                return (file_path, original_name)
    
    return None


@router.get("/files/{file_id}")
async def get_file(file_id: str):
    """获取上传的文件"""
    result = find_file_by_id(file_id)
    if result:
        file_path, original_name = result
        return FileResponse(
            file_path,
            filename=original_name,
            media_type='application/octet-stream'
        )
    
    raise HTTPException(status_code=404, detail="文件不存在")


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """删除文件"""
    result = find_file_by_id(file_id)
    if result:
        file_path, _ = result
        os.remove(file_path)
        return {"status": "deleted", "file_id": file_id}
    
    raise HTTPException(status_code=404, detail="文件不存在")

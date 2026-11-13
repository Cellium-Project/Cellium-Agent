# -*- coding: utf-8 -*-
"""附件存储：上传、查找、内联判定、视觉图片读取"""

import base64
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from typing import Optional, Tuple

from app.core.util.runtime_paths import resolve_dir_writable
from app.core.util.image_info import image_info, mime_for, VISION_EXTS

UPLOAD_DIR = os.path.join(resolve_dir_writable("workspace"), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_VISION_BYTES = 4 * 1024 * 1024

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
    'binary': ['.exe', '.dll', '.so', '.dylib', '.bin', '.dat'],
}

_MEDIA_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
    '.bmp': 'image/bmp', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon',
    '.tif': 'image/tiff', '.tiff': 'image/tiff',
    '.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8', '.json': 'application/json', '.html': 'text/html; charset=utf-8',
    '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.mp4': 'video/mp4', '.webm': 'video/webm',
}

_INLINE_EXT = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico', '.tif', '.tiff',
    '.pdf', '.txt', '.md', '.csv', '.json',
}

_SAFE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_part_cache: dict = {}
_part_lock = threading.Lock()


def get_file_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    for file_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return file_type
    return 'other'


def sanitize_filename(name: str) -> str:
    name = os.path.basename((name or '').replace('\\', '/'))
    name = _SAFE_NAME_RE.sub('_', name).strip(' .')
    if not name:
        name = 'file'
    if len(name) > 120:
        stem, ext = os.path.splitext(name)
        name = stem[:120 - len(ext)] + ext
    return name


def media_type_for(filename: str) -> str:
    return _MEDIA_TYPES.get(os.path.splitext(filename)[1].lower(), 'application/octet-stream')


def is_inline_type(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in _INLINE_EXT


def _date_dir() -> str:
    path = os.path.join(UPLOAD_DIR, datetime.now().strftime("%Y%m%d"))
    os.makedirs(path, exist_ok=True)
    return path


def _preview_dir(base_dir: str) -> str:
    path = os.path.join(base_dir, "_previews")
    os.makedirs(path, exist_ok=True)
    return path


def _write_derivative(data: bytes, base_dir: str, file_id: str, kind: str) -> str:
    path = os.path.join(_preview_dir(base_dir), f"{file_id}_{kind}.webp")
    with open(path, "wb") as f:
        f.write(data)
    return path


def save_stream(fileobj, filename: str, vision=None, thumb=None) -> dict:
    safe_name = sanitize_filename(filename)
    file_id = str(uuid.uuid4())
    base_dir = _date_dir()
    file_path = os.path.join(base_dir, f"{file_id}_{safe_name}")

    size = 0
    with open(file_path, "wb") as out:
        while True:
            chunk = fileobj.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                out.close()
                os.remove(file_path)
                raise ValueError("文件大小超过限制")
            out.write(chunk)

    vision_path = None
    thumb_path = None
    if vision is not None:
        vision_path = _write_derivative(vision.read(), base_dir, file_id, "vision")
    if thumb is not None:
        thumb_path = _write_derivative(thumb.read(), base_dir, file_id, "thumb")

    return _build_attachment(file_id, safe_name, file_path, size, thumb_path, vision_path)


def save_local_file(path: str) -> dict:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB 限制")
    safe_name = sanitize_filename(os.path.basename(path))
    file_id = str(uuid.uuid4())
    target = os.path.join(_date_dir(), f"{file_id}_{safe_name}")
    shutil.copy2(path, target)
    return _build_attachment(file_id, safe_name, target, size)


def _build_attachment(file_id: str, filename: str, file_path: str, size: int,
                      thumb_path: str = None, vision_path: str = None) -> dict:
    file_type = get_file_type(filename)
    width = height = None
    if file_type == "image":
        info = image_info(file_path)
        if info:
            width, height = info["width"], info["height"]

    return {
        "file_id": file_id,
        "filename": filename,
        "file_type": file_type,
        "file_size": size,
        "url": f"/api/files/{file_id}",
        "thumb_url": f"/api/files/{file_id}?thumb=1" if thumb_path else None,
        "local_path": file_path,
        "vision_local_path": vision_path,
        "upload_time": datetime.now().isoformat(),
        "width": width,
        "height": height,
    }


def find_file(file_id: str) -> Optional[Tuple[str, str]]:
    if not file_id or "/" in file_id or "\\" in file_id:
        return None
    prefix = f"{file_id}_"
    for date_dir in sorted(os.listdir(UPLOAD_DIR), reverse=True):
        dir_path = os.path.join(UPLOAD_DIR, date_dir)
        if not os.path.isdir(dir_path):
            continue
        try:
            names = os.listdir(dir_path)
        except OSError:
            continue
        for filename in names:
            if filename.startswith(prefix):
                original = filename[len(prefix):]
                return os.path.join(dir_path, filename), original
    return None


def find_thumb(file_id: str) -> Optional[str]:
    found = find_file(file_id)
    if not found:
        return None
    path = os.path.join(_preview_dir(os.path.dirname(found[0])), f"{file_id}_thumb.webp")
    return path if os.path.exists(path) else None


def find_vision(file_id: str) -> Optional[str]:
    found = find_file(file_id)
    if not found:
        return None
    path = os.path.join(_preview_dir(os.path.dirname(found[0])), f"{file_id}_vision.webp")
    return path if os.path.exists(path) else None


def image_to_part(path: str) -> Optional[dict]:
    """本地图片 → 视觉 part（原图 base64），非支持格式/过大返回 None"""
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in VISION_EXTS or not os.path.isfile(path):
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat.st_size > MAX_VISION_BYTES:
        return None

    key = (path, stat.st_mtime, stat.st_size)
    with _part_lock:
        cached = _part_cache.get(key)
    if cached:
        return cached

    info = image_info(path)
    if not info:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None

    part = {
        "mime": mime_for(path) or info["mime"],
        "b64": base64.b64encode(raw).decode("ascii"),
        "filename": os.path.basename(path),
        "width": info["width"],
        "height": info["height"],
        "bytes": len(raw),
    }
    with _part_lock:
        if len(_part_cache) > 64:
            _part_cache.clear()
        _part_cache[key] = part
    return part

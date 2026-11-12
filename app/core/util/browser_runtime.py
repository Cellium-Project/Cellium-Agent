# -*- coding: utf-8 -*-
"""
内置浏览器 runtime 管理

约定：
  - runtime 目录：<workspace>/.runtime/browser
  - 期望可执行文件位置见 _RUNTIME_EXECUTABLES（安装/下载都应以它为唯一准绳）
  - 下载地址通过环境变量 CELLIUM_BROWSER_DOWNLOAD_URL 提供（不硬编码第三方源）
"""

import os
import platform
from typing import Optional, Dict, Any

from app.core.util.runtime_paths import resolve_dir_writable

ENV_DOWNLOAD_URL = "CELLIUM_BROWSER_DOWNLOAD_URL"
ENV_ARCHIVE_PATH = "CELLIUM_BROWSER_ARCHIVE"


def _runtime_root() -> str:
    return os.path.join(resolve_dir_writable("workspace"), ".runtime", "browser")


_RUNTIME_EXECUTABLES = {
    "Windows": ("win", "chrome.exe"),
    "Darwin": ("mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
    "Linux": ("linux", "chrome"),
}

_RUNTIME_DOWNLOAD_SPECS = {
    "Windows": {"platform": "windows-x64", "archive_name": "chromium-win-x64.zip"},
    "Darwin": {"platform": "macos", "archive_name": "chromium-macos.zip"},
    "Linux": {"platform": "linux-x64", "archive_name": "chromium-linux64.zip"},
}


def get_runtime_base_dir() -> str:
    return _runtime_root()


def get_expected_browser_path() -> Optional[str]:
    """runtime 安装后应有的可执行文件路径（未安装也返回）"""
    parts = _RUNTIME_EXECUTABLES.get(platform.system())
    if not parts:
        return None
    return os.path.join(_runtime_root(), *parts)


def get_runtime_browser_path() -> Optional[str]:
    """当前已安装且可用的 runtime 浏览器路径"""
    path = get_expected_browser_path()
    if path and os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    return None


def get_runtime_download_spec() -> Dict[str, Any]:
    """下载/安装 runtime 所需的全部信息（供前端或安装器使用）"""
    system = platform.system()
    root = _runtime_root()
    expected = get_expected_browser_path()
    installed = get_runtime_browser_path()
    download_url = (os.environ.get(ENV_DOWNLOAD_URL) or "").strip()
    archive_path = (os.environ.get(ENV_ARCHIVE_PATH) or "").strip()

    spec = _RUNTIME_DOWNLOAD_SPECS.get(system, {}).copy()
    spec.update({
        "runtime_base_dir": root,
        "expected_browser_path": expected,
        "browser_path": installed,
        "installed": bool(installed),
        "installed_size": os.path.getsize(installed) if installed else 0,
        "download_url": download_url or None,
        "archive_path": archive_path or None,
        "can_download": bool(download_url),
        "can_install_local": bool(archive_path and os.path.isfile(archive_path)),
        "download_env": ENV_DOWNLOAD_URL,
        "archive_env": ENV_ARCHIVE_PATH,
    })
    if expected:
        spec["target_dir"] = os.path.dirname(expected)
    return spec


def get_runtime_info() -> Dict[str, Any]:
    browser_path = get_runtime_browser_path()
    download_spec = get_runtime_download_spec()
    return {
        "installed": bool(browser_path),
        "browser_path": browser_path,
        "runtime_base_dir": get_runtime_base_dir(),
        "download_spec": download_spec,
    }

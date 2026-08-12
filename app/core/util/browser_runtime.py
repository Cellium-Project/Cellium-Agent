# -*- coding: utf-8 -*-
"""
内置浏览器 runtime 管理
"""

import os
import platform
from typing import Optional, Dict, Any

from app.core.util.runtime_paths import resolve_dir_writable


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


def get_runtime_browser_path() -> Optional[str]:
    system = platform.system()
    parts = _RUNTIME_EXECUTABLES.get(system)
    if parts:
        runtime_path = os.path.join(_runtime_root(), *parts)
        if os.path.exists(runtime_path):
            return runtime_path
    return None


def get_runtime_download_spec() -> Dict[str, Any]:
    system = platform.system()
    root = _runtime_root()
    spec = _RUNTIME_DOWNLOAD_SPECS.get(system, {}).copy()
    runtime_path = get_runtime_browser_path()
    if spec:
        spec["target_dir"] = os.path.join(root, spec["platform"])
    spec.update({
        "runtime_base_dir": root,
        "expected_browser_path": runtime_path,
        "can_download": bool(spec),
    })
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

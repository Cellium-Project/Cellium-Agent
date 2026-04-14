# -*- coding: utf-8 -*-
"""
内置浏览器 runtime 管理
"""

import os
import platform
from typing import Optional, Dict, Any


_RUNTIME_ROOT = os.path.join("workspace", ".runtime", "browser")

_RUNTIME_EXECUTABLES = {
    "Windows": os.path.join(_RUNTIME_ROOT, "win", "chrome.exe"),
    "Darwin": os.path.join(_RUNTIME_ROOT, "mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
    "Linux": os.path.join(_RUNTIME_ROOT, "linux", "chrome"),
}

_RUNTIME_DOWNLOAD_SPECS = {
    "Windows": {
        "platform": "windows-x64",
        "archive_name": "chromium-win-x64.zip",
        "target_dir": os.path.join(_RUNTIME_ROOT, "win"),
    },
    "Darwin": {
        "platform": "macos",
        "archive_name": "chromium-macos.zip",
        "target_dir": os.path.join(_RUNTIME_ROOT, "mac"),
    },
    "Linux": {
        "platform": "linux-x64",
        "archive_name": "chromium-linux-x64.tar.gz",
        "target_dir": os.path.join(_RUNTIME_ROOT, "linux"),
    },
}


def get_runtime_base_dir() -> str:
    return _RUNTIME_ROOT



def get_runtime_browser_path() -> Optional[str]:
    system = platform.system()
    runtime_path = _RUNTIME_EXECUTABLES.get(system)
    if runtime_path and os.path.exists(runtime_path):
        return runtime_path
    return None



def get_runtime_download_spec() -> Dict[str, Any]:
    system = platform.system()
    spec = _RUNTIME_DOWNLOAD_SPECS.get(system, {}).copy()
    runtime_path = _RUNTIME_EXECUTABLES.get(system)
    spec.update({
        "runtime_base_dir": _RUNTIME_ROOT,
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
        "runtime_base_dir": _RUNTIME_ROOT,
        "download_spec": download_spec,
    }

# -*- coding: utf-8 -*-
import os
import tempfile
from typing import Dict, Any

from .patch_applier import PatchApplier


def _atomic_write(path: str, content: str):
    dir_path = os.path.dirname(path) or '.'
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
    try:
        with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.flush()
            if hasattr(os, 'fsync'):
                os.fsync(f.fileno())
        if os.path.exists(path):
            st = os.stat(path)
            os.replace(temp_path, path)
            os.chmod(path, st.st_mode)
        else:
            os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


class EditTransaction:

    @staticmethod
    def apply_edit(file_path: str, content: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        new_content, info = PatchApplier.apply(content, patch)
        if info.get("error"):
            return {"success": False, "error": info["error"]}
        if new_content == content:
            return {"success": False, "error": "内容无变化"}

        _atomic_write(file_path, new_content)
        diff = PatchApplier._generate_diff(content, new_content)

        return {
            "success": True,
            "path": file_path,
            "count": info.get("count", 0),
            "diff": diff,
        }

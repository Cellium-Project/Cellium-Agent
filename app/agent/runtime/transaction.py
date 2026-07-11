# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import shutil
from typing import Dict, Any

from .patch_applier import PatchApplier


def _atomic_write(path: str, content: str, encoding: str = "utf-8"):
    abs_path = os.path.abspath(path)
    dir_path = os.path.dirname(abs_path) or '.'
    is_win = sys.platform == "win32"

    if is_win:
        fd, temp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".tmp")
    else:
        fd, temp_path = tempfile.mkstemp(dir=dir_path, prefix=".tmp_", suffix=".tmp")

    try:
        with os.fdopen(fd, 'w', encoding=encoding, errors='replace') as f:
            f.write(content)
            f.flush()
            if hasattr(os, 'fsync'):
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

        if os.path.exists(abs_path):
            st = os.stat(abs_path)
        else:
            st = None

        try:
            os.replace(temp_path, abs_path)
        except OSError:
            with open(temp_path, 'rb') as src, open(abs_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            os.unlink(temp_path)

        if st is not None and not is_win:
            try:
                os.chmod(abs_path, st.st_mode)
            except OSError:
                pass
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise


class EditTransaction:

    @staticmethod
    def apply_edit(file_path: str, content: str, patch: Dict[str, Any], encoding: str = "utf-8") -> Dict[str, Any]:
        new_content, info = PatchApplier.apply(content, patch)
        if info.get("error"):
            return {"success": False, "error": info["error"]}
        if new_content == content:
            return {"success": False, "error": "内容无变化"}

        _atomic_write(file_path, new_content, encoding=encoding)
        diff = PatchApplier._generate_diff(content, new_content)

        return {
            "success": True,
            "path": file_path,
            "count": info.get("count", 0),
            "diff": diff,
        }

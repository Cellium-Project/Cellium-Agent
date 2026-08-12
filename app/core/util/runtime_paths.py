# -*- coding: utf-8 -*-
import os
import sys

def _find_project_root(cwd: str):
    here = os.path.abspath(cwd)
    for _ in range(8):
        marker = os.path.join(here, "app", "core", "util", "runtime_paths.py")
        if os.path.isfile(marker):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent
    return None

def _detect_install_mode():
    """基于程序加载位置判定运行模式

    - 模块位于 site-packages 内 → 'installed'（pip 安装）
    - 否则以 __file__ 为锚点上溯找项目标识 → 'source'（绿色版/开发）

    Returns:
        (mode, source_root): mode 为 'source' 时 source_root 为项目根
    """
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        import site
        site_paths = [os.path.normcase(p) for p in site.getsitepackages()]
    except Exception:
        site_paths = []
    norm_here = os.path.normcase(here)
    if any(norm_here.startswith(p + os.sep) for p in site_paths):
        return "installed", None

    root = _find_project_root(here)
    if root is not None:
        return "source", root
    return "installed", None

def _find_package_data_dir():
    """查找 pip 安装的数据目录（sys.prefix/cellium 优先）"""
    candidates = [os.path.join(sys.prefix, "cellium")]
    try:
        import site
        candidates += [os.path.join(p, "cellium") for p in site.getsitepackages()]
    except Exception:
        pass
    for cand in candidates:
        if os.path.isdir(cand):
            return cand
    return None

def _find_user_data_dir() -> str:
    """用户数据根目录

    - Windows: %APPDATA%/CelliumAgent
    - Linux/macOS: ~/.local/share/CelliumAgent
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "CelliumAgent")

def resolve_dir(*rel_parts) -> str:
    """解析资源目录路径

    优先级：
    1. source 模式（绿色版/开发）→ CWD / 项目根下同名目录
    2. installed 模式（pip 安装）→ sys.prefix/cellium（打包资源权威位置）
    3. 创建并返回 CWD 下的路径（兜底）

    Args:
        *rel_parts: 相对路径组件，如 ("html",) -> "html"
    """
    rel_path = os.path.join(*rel_parts) if rel_parts else ""

    mode, source_root = _detect_install_mode()
    if mode == "source":
        cwd_path = os.path.join(os.getcwd(), rel_path) if rel_path else os.getcwd()
        if os.path.exists(cwd_path):
            return cwd_path
        if source_root is not None:
            candidate = os.path.join(source_root, rel_path) if rel_path else source_root
            if os.path.exists(candidate):
                return candidate

    data_path = os.path.join(sys.prefix, "cellium", rel_path) if rel_path else os.path.join(sys.prefix, "cellium")
    if os.path.exists(data_path):
        return data_path

    try:
        import site
        for site_path in site.getsitepackages():
            candidate = os.path.join(site_path, "cellium", rel_path) if rel_path else os.path.join(site_path, "cellium")
            if os.path.exists(candidate):
                return candidate
    except Exception:
        pass

    cwd_path = os.path.join(os.getcwd(), rel_path) if rel_path else os.getcwd()
    os.makedirs(cwd_path, exist_ok=True)
    return cwd_path

def resolve_file(*rel_parts) -> str | None:
    """解析资源文件路径（不自动创建目录）

    Returns:
        绝对路径或 None（文件不存在）
    """
    dir_path = resolve_dir(*rel_parts[:-1] if rel_parts else ())
    if not rel_parts:
        return dir_path if os.path.exists(dir_path) else None
    file_path = os.path.join(dir_path, rel_parts[-1])
    return file_path if os.path.exists(file_path) else None


def resolve_dir_writable(*rel_parts) -> str:
    """解析可写数据目录路径（运行时生成数据：memory/workspace/data）

    判定依据程序加载位置（_detect_install_mode），与 CWD 无关：
    1. source 模式（绿色版/开发）→ 项目根，数据就地，解压包不落用户目录
    2. installed 模式（pip 安装）→ 用户数据目录（%APPDATA%/CelliumAgent），
       升级/重装不丢数据，换 Python 环境也不丢
    3. 兜底 → CWD（上述路径不可写等场景）

    Args:
        *rel_parts: 相对路径组件，如 ("data",) -> "<base>/data"
    """
    rel_path = os.path.join(*rel_parts) if rel_parts else ""

    mode, source_root = _detect_install_mode()
    if mode == "source" and source_root is not None:
        base = os.path.join(source_root, rel_path) if rel_path else source_root
        try:
            os.makedirs(base, exist_ok=True)
            return base
        except Exception:
            pass

    user_dir = _find_user_data_dir()
    if user_dir:
        base = os.path.join(user_dir, rel_path) if rel_path else user_dir
        try:
            os.makedirs(base, exist_ok=True)
            return base
        except Exception:
            pass

    cwd_path = os.path.join(os.getcwd(), rel_path) if rel_path else os.getcwd()
    try:
        os.makedirs(cwd_path, exist_ok=True)
    except Exception:
        pass
    return cwd_path

def resolve_config_dir() -> str:
    """解析配置目录（读写层）

    判定依据程序加载位置（_detect_install_mode），与 CWD 无关：
    - source 模式（绿色版/开发）：直接用项目根 config/agent（就地读写，模板与用户配置合一）
    - installed 模式（pip 安装）：用户配置层 %APPDATA%/CelliumAgent/config/agent；
      首次启动从包内模板拷贝，之后读写用户层，升级只覆盖包内模板、用户配置保留
    """
    rel = ("config", "agent")

    mode, source_root = _detect_install_mode()
    if mode == "source" and source_root is not None:
        cfg_dir = os.path.join(source_root, *rel)
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception:
            pass
        return cfg_dir

    user_cfg = os.path.join(_find_user_data_dir(), *rel)
    try:
        os.makedirs(user_cfg, exist_ok=True)
    except Exception:
        pass

    pkg_dir = _find_package_data_dir()
    if pkg_dir:
        tpl_dir = os.path.join(pkg_dir, *rel)
        if os.path.isdir(tpl_dir):
            _copy_config_template(tpl_dir, user_cfg)

    return user_cfg


def _copy_config_template(tpl_dir: str, user_cfg: str):
    """把包内配置模板合并进用户层

    - 用户层缺失的文件：直接拷贝
    - 已存在文件：把模板中新增的键/字段补进用户层
    """
    import shutil
    try:
        import yaml
    except ImportError:
        yaml = None
    try:
        for fname in os.listdir(tpl_dir):
            if not fname.endswith(".yaml"):
                continue
            dst = os.path.join(user_cfg, fname)
            src = os.path.join(tpl_dir, fname)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                continue
            if yaml is None:
                continue
            try:
                with open(src, "r", encoding="utf-8") as f:
                    tpl = yaml.safe_load(f) or {}
                with open(dst, "r", encoding="utf-8") as f:
                    user = yaml.safe_load(f) or {}
                merged = _merge_missing(tpl, user)
                if merged != user:
                    with open(dst, "w", encoding="utf-8") as f:
                        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
            except Exception:
                continue
    except Exception:
        pass

def _merge_missing(tpl: dict, user: dict) -> dict:
    """把模板中用户层缺失的键补进去，用户已有值优先"""
    result = dict(user)
    for key, value in tpl.items():
        if key not in result:
            result[key] = value
        elif isinstance(value, dict) and isinstance(result[key], dict):
            result[key] = _merge_missing(value, result[key])
    return result

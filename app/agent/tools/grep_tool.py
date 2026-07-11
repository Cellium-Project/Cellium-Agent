# -*- coding: utf-8 -*-

import os
import re
import sys
import subprocess
import shutil
import logging
import threading
from typing import Dict, Any, Optional, List

from .base_tool import BaseTool

logger = logging.getLogger(__name__)

# --- vendor ripgrep ---
_VENDOR_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'vendor', 'ripgrep'))
_RG_DOWNLOAD_LOCK = threading.Lock()

# pinned version — update with `pip install ripgrep-update` or manually
RG_VERSION = "14.1.1"
_RG_URL_TEMPLATE = (
    "https://github.com/BurntSushi/ripgrep/releases/download/"
    "ripgrep-{version}-{triple}.tar.gz"
)

RG_VERSION_FILE = "VERSION"


def _detect_platform_triple() -> Optional[str]:
    """Return the rust target triple for the current platform, or None."""
    import platform
    machine = platform.machine().lower()
    arch_map = {
        'amd64': 'x86_64',
        'x86_64': 'x86_64',
        'arm64': 'aarch64',
        'aarch64': 'aarch64',
        'i386': 'i686',
        'i686': 'i686',
    }
    arch = arch_map.get(machine)
    if not arch and sys.platform == 'win32':
        proc_arch = os.environ.get('PROCESSOR_ARCHITECTURE', '').lower()
        arch = arch_map.get(proc_arch)
    if not arch:
        return None

    os_map = {
        'win32': 'pc-windows-msvc',
        'cygwin': 'pc-windows-msvc',
        'darwin': 'apple-darwin',
        'linux': 'unknown-linux-musl',
    }
    plat = os_map.get(sys.platform)
    if not plat:
        return None

    return f"{arch}-{plat}"


def _vendor_rg_path() -> Optional[str]:
    """Return path to vendored ripgrep binary, or None."""
    triple = _detect_platform_triple()
    if not triple:
        return None
    binary = 'rg.exe' if sys.platform == 'win32' else 'rg'
    candidate = os.path.join(_VENDOR_DIR, triple, binary)
    if os.path.isfile(candidate):
        return candidate
    # flat layout: vendor/ripgrep/rg
    flat = os.path.join(_VENDOR_DIR, binary)
    if os.path.isfile(flat):
        return flat
    # vendor/ripgrep/{arch}-{os}/rg
    for root, dirs, files in os.walk(_VENDOR_DIR):
        if binary in files and 'VERSION' not in files:
            return os.path.join(root, binary)
    return None


def _download_rg() -> Optional[str]:
    """Download ripgrep to vendor/ripgrep/. Returns path to binary or None."""
    triple = _detect_platform_triple()
    if not triple:
        logger.warning("Cannot detect platform for ripgrep download")
        return None

    url = _RG_URL_TEMPLATE.format(version=RG_VERSION, triple=triple)
    binary = 'rg.exe' if sys.platform == 'win32' else 'rg'
    target_dir = os.path.join(_VENDOR_DIR, triple)

    with _RG_DOWNLOAD_LOCK:
        # double-check after acquiring lock
        existing = _vendor_rg_path()
        if existing:
            return existing

        try:
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, binary)
        except OSError:
            return None

        logger.info("Downloading ripgrep %s from %s", RG_VERSION, url)

        import urllib.request
        import tarfile
        import io

        tmp_path = target_path + '.download'
        try:
            # download
            req = urllib.request.Request(url, headers={
                'Accept': 'application/octet-stream',
                'User-Agent': 'Cellium-Agent/1.0',
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()

            # extract
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tar:
                for member in tar.getmembers():
                    if member.name.endswith(f'/{binary}') or member.name == binary:
                        extracted = tar.extractfile(member)
                        if extracted:
                            with open(tmp_path, 'wb') as f:
                                f.write(extracted.read())
                            break
                else:
                    logger.error("Binary not found in ripgrep archive")
                    return None

            os.replace(tmp_path, target_path)
            os.chmod(target_path, 0o755)

            # write version marker
            with open(os.path.join(_VENDOR_DIR, RG_VERSION_FILE), 'w') as f:
                f.write(RG_VERSION)

            logger.info("ripgrep %s installed at %s", RG_VERSION, target_path)
            return target_path

        except Exception as exc:
            logger.error("Failed to download ripgrep: %s", exc)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return None


VCS_DIRS = ('.git', '.svn', '.hg', '.bzr', '.jj', '.sl')
DEFAULT_MAX_RESULTS = 250
LINE_WIDTH_CAP = 500
SEARCH_TIMEOUT_SEC = 20


def _find_rg() -> Optional[str]:
    rg = _vendor_rg_path()
    if rg:
        return rg
    rg = shutil.which("rg")
    if rg:
        return rg
    rg = _download_rg()
    if rg:
        return rg
    return None


def _run_rg(args: List[str], search_path: str) -> List[str]:
    full_cmd = ['rg', *args, '--path-separator', '/', search_path]
    try:
        proc = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT_SEC,
            encoding='utf-8',
            errors='replace',
        )
    except subprocess.TimeoutExpired:
        return []
    except FileNotFoundError:
        return []
    except Exception:
        return []

    if proc.returncode == 1:
        return []
    if proc.returncode != 0:
        return []

    lines = proc.stdout.strip().split('\n')
    return [l.rstrip('\r') for l in lines if l.strip()]


def _make_rel(filepath: str) -> str:
    cwd = os.getcwd()
    try:
        return os.path.relpath(filepath, cwd)
    except ValueError:
        return filepath


def _get_mtime_sort_key(filepath: str) -> float:
    try:
        return -os.path.getmtime(filepath)
    except OSError:
        return 0.0


def _apply_limit(items: list, head_limit: Optional[int], offset: int = 0):
    limit = head_limit if head_limit and head_limit > 0 else DEFAULT_MAX_RESULTS
    if offset >= len(items):
        return [], None
    end = min(offset + limit, len(items))
    sliced = items[offset:end]
    truncated = end < len(items)
    return sliced, limit if truncated else None


def _fallback_search(
    query: str,
    path: str,
    ext: Optional[str],
    pattern: Optional[str],
    offset: int,
    head_limit: Optional[int],
) -> Dict[str, Any]:
    hits = []
    use_regex = any(c in query for c in '*+?^$[]|(){}')

    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fname in filenames:
            if ext and not fname.endswith(ext):
                continue
            if pattern:
                import fnmatch
                if not fnmatch.fnmatch(fname, pattern):
                    continue
            filepath = os.path.join(dirpath, fname)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for i, line in enumerate(f):
                        if use_regex:
                            m = re.search(query, line, re.IGNORECASE)
                            if m:
                                hits.append({
                                    "file": _make_rel(filepath),
                                    "line": i + 1,
                                    "content": line.strip()[:100],
                                    "match": m.group(),
                                })
                        else:
                            if query.lower() in line.lower():
                                hits.append({
                                    "file": _make_rel(filepath),
                                    "line": i + 1,
                                    "content": line.strip()[:100],
                                })
            except Exception:
                continue
            if len(hits) >= 5000:
                break
        if len(hits) >= 5000:
            break

    page, applied_limit = _apply_limit(hits, head_limit, offset)
    return {
        "success": True,
        "mode": "content",
        "query": query,
        "hits": page,
        "total": len(hits),
        "offset": offset,
        "has_more": offset + len(page) < len(hits),
        "applied_limit": applied_limit,
        "engine": "fallback",
    }


class GrepTool(BaseTool):

    name = "grep"
    description = (
        "A powerful search tool built on ripgrep\n\n"
        "Usage:\n"
        "- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command.\n"
        "- Supports full regex syntax (e.g. \"log.*Error\", \"function\\s+\\w+\")\n"
        "- Filter files with glob parameter (e.g. \"*.js\", \"**/*.tsx\") or type parameter (e.g. \"js\", \"py\", \"rust\")\n"
        "- Output modes: \"content\" shows matching lines (supports -A/-B/-C context, -n line numbers, head_limit), \"files_with_matches\" shows file paths (supports head_limit), \"count\" shows match counts (supports head_limit)\n"
        "- Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`"
    )

    def __init__(self, allowed_roots=None):
        super().__init__()
        self._allowed_roots = allowed_roots or []

    @property
    def tool_name(self) -> str:
        return "grep"

    def _cmd_grep(
        self,
        query: str = "",
        path: str = ".",
        glob: str = None,
        output_mode: str = "files_with_matches",
        ext: str = None,
        pattern: str = "",
        type: str = None,
        offset: int = 0,
        head_limit: int = None,
        **kwargs,
    ) -> Dict[str, Any]:
        keyword = pattern or query
        if not keyword:
            return {"success": False, "error": "pattern is required"}

        abs_path = self._resolve_path(path)

        rg = _find_rg()
        if not rg:
            return _fallback_search(keyword, abs_path, ext, glob, offset, head_limit)

        result = self._rg_search(
            query=keyword,
            search_path=abs_path,
            glob=glob,
            output_mode=output_mode,
            type=type,
            context_before=kwargs.get('-B'),
            context_after=kwargs.get('-A'),
            context_around=kwargs.get('-C'),
            show_line_numbers=kwargs.get('-n', True),
            ignore_case=kwargs.get('-i', False),
            head_limit=head_limit,
            offset=offset,
            multiline=kwargs.get('multiline', False),
        )
        result["engine"] = "ripgrep"
        return result

    def _rg_search(
        self,
        query: str,
        search_path: str,
        glob: Optional[str],
        output_mode: str,
        type: Optional[str],
        context_before: Optional[int],
        context_after: Optional[int],
        context_around: Optional[int],
        show_line_numbers: bool,
        ignore_case: bool,
        head_limit: Optional[int],
        offset: int,
        multiline: bool,
    ) -> Dict[str, Any]:
        args = ['--hidden']

        for d in VCS_DIRS:
            args.extend(['--glob', f'!{d}'])

        args.extend(['--max-columns', str(LINE_WIDTH_CAP)])

        if multiline:
            args.extend(['-U', '--multiline-dotall'])

        if ignore_case:
            args.append('-i')

        if output_mode == 'files_with_matches':
            args.append('-l')
        elif output_mode == 'count':
            args.append('-c')

        if show_line_numbers and output_mode == 'content':
            args.append('-n')

        if output_mode == 'content':
            if context_around is not None:
                args.extend(['-C', str(context_around)])
            else:
                if context_before is not None:
                    args.extend(['-B', str(context_before)])
                if context_after is not None:
                    args.extend(['-A', str(context_after)])

        if query.startswith('-'):
            args.extend(['-e', query])
        else:
            args.append(query)

        if type:
            args.extend(['--type', type])

        if glob:
            for g in glob.replace(',', ' ').split():
                g = g.strip()
                if g:
                    args.extend(['--glob', g])

        lines = _run_rg(args, search_path)

        if output_mode == 'content':
            return self._format_content(lines, head_limit, offset)

        if output_mode == 'count':
            return self._format_count(lines, head_limit, offset)

        return self._format_files(lines, head_limit, offset)

    def _format_content(self, lines: List[str], head_limit: Optional[int], offset: int) -> Dict[str, Any]:
        page, applied_limit = _apply_limit(lines, head_limit, offset)
        final = [_make_rel_line(l) for l in page]
        return {
            "success": True,
            "mode": "content",
            "num_lines": len(final),
            "total": len(lines),
            "content": '\n'.join(final),
            "offset": offset,
            "applied_limit": applied_limit,
            "has_more": applied_limit is not None,
        }

    def _format_count(self, lines: List[str], head_limit: Optional[int], offset: int) -> Dict[str, Any]:
        page, applied_limit = _apply_limit(lines, head_limit, offset)
        final = [_make_rel_line(l) for l in page]
        total = 0
        files = 0
        for l in page:
            try:
                _, count_str = l.rsplit(':', 1)
                total += int(count_str)
                files += 1
            except ValueError:
                pass
        return {
            "success": True,
            "mode": "count",
            "content": '\n'.join(final),
            "num_files": files,
            "num_matches": total,
            "offset": offset,
            "applied_limit": applied_limit,
        }

    def _format_files(self, lines: List[str], head_limit: Optional[int], offset: int) -> Dict[str, Any]:
        rel_files = [_make_rel(p) for p in lines]
        sorted_files = sorted(rel_files, key=_get_mtime_sort_key)
        page, applied_limit = _apply_limit(sorted_files, head_limit, offset)
        return {
            "success": True,
            "mode": "files_with_matches",
            "filenames": page,
            "num_files": len(page),
            "offset": offset,
            "applied_limit": applied_limit,
        }

    def _resolve_path(self, path: str) -> str:
        if not path:
            return os.getcwd()
        if os.path.isabs(path):
            return path
        return os.path.abspath(path)


def _make_rel_line(line: str) -> str:
    idx = line.find(':')
    if idx <= 0:
        return line
    abs_path = line[:idx]
    rest = line[idx:]
    return _make_rel(abs_path) + rest

# -*- coding: utf-8 -*-
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class ReadRecord:
    path: str
    offset: int
    limit: int
    timestamp: float

class ReadTracker:
    def __init__(self):
        self._reads: List[ReadRecord] = []

    def record(self, path: str, offset: int = 0, limit: int = 500) -> Dict[str, Any]:
        import time
        record = ReadRecord(
            path=path,
            offset=offset,
            limit=limit,
            timestamp=time.time(),
        )
        self._reads.append(record)

        duplicates = self._detect_duplicates(path)
        return {
            "recorded": True,
            "path": path,
            "read_count": len([r for r in self._reads if r.path == path]),
            "duplicate_detected": len(duplicates) > 1,
        }

    def _detect_duplicates(self, path: str) -> List[ReadRecord]:
        return [r for r in self._reads if r.path == path]

    def get_duplicates(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._reads:
            counts[r.path] = counts.get(r.path, 0) + 1
        return {k: v for k, v in counts.items() if v > 1}

    def clear(self):
        self._reads.clear()

class ContextCompact:
    @staticmethod
    def compact(content: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        options = options or {}
        collapse_imports = options.get("collapse_imports", True)
        collapse_docstrings = options.get("collapse_docstrings", True)
        collapse_blank_lines = options.get("collapse_blank_lines", True)
        max_docstring_len = options.get("max_docstring_len", 50)

        lines = content.split('\n')
        result_lines = []
        import_lines = []
        in_import_block = False
        consecutive_blank = 0
        imports_collapsed = 0
        docstrings_collapsed = 0
        blank_lines_removed = 0

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if collapse_blank_lines:
                if stripped == '':
                    consecutive_blank += 1
                    if consecutive_blank <= 1:
                        result_lines.append(line)
                    else:
                        blank_lines_removed += 1
                    i += 1
                    continue
                else:
                    consecutive_blank = 0

            if collapse_imports:
                if stripped.startswith('import ') or stripped.startswith('from '):
                    if not in_import_block and import_lines:
                        result_lines.append(f"# ... {len(import_lines)} imports collapsed ...")
                        imports_collapsed += len(import_lines)
                        import_lines = []
                    import_lines.append(line)
                    in_import_block = True
                    i += 1
                    continue
                elif in_import_block and import_lines:
                    result_lines.append(f"# ... {len(import_lines)} imports collapsed ...")
                    imports_collapsed += len(import_lines)
                    import_lines = []
                    in_import_block = False

            if collapse_docstrings:
                docstring_result = ContextCompact._handle_docstring(
                    lines, i, max_docstring_len
                )
                if docstring_result:
                    result_lines.append(docstring_result["line"])
                    i = docstring_result["next_i"]
                    if docstring_result["collapsed"]:
                        docstrings_collapsed += 1
                    continue

            result_lines.append(line)
            i += 1

        if import_lines:
            result_lines.append(f"# ... {len(import_lines)} imports collapsed ...")
            imports_collapsed += len(import_lines)

        new_content = '\n'.join(result_lines)
        original_tokens = len(content.split())
        new_tokens = len(new_content.split())

        return {
            "content": new_content,
            "original_lines": len(lines),
            "new_lines": len(result_lines),
            "imports_collapsed": imports_collapsed,
            "docstrings_collapsed": docstrings_collapsed,
            "blank_lines_removed": blank_lines_removed,
            "compression_ratio": 1 - (new_tokens / original_tokens) if original_tokens > 0 else 0,
        }

    @staticmethod
    def _handle_docstring(lines: List[str], start_i: int, max_len: int) -> Optional[Dict[str, Any]]:
        line = lines[start_i]
        stripped = line.strip()

        if '"""' in stripped or "'''" in stripped:
            quote = '"""' if '"""' in stripped else "'''"

            if stripped.count(quote) >= 2:
                return None

            docstring_lines = [line]
            j = start_i + 1
            while j < len(lines):
                docstring_lines.append(lines[j])
                if quote in lines[j] and j > start_i:
                    break
                j += 1

            full_docstring = '\n'.join(docstring_lines)
            content_match = re.search(r'["\']{3}(.+?)["\']{3}', full_docstring, re.DOTALL)

            if content_match:
                doc_content = content_match.group(1).strip()
                indent = len(line) - len(line.lstrip())
                indent_str = ' ' * indent

                if len(doc_content) > max_len:
                    truncated = doc_content[:max_len] + "..."
                    return {
                        "line": f'{indent_str}"""{truncated}"""',
                        "next_i": j + 1,
                        "collapsed": True,
                    }

            return None

        return None

class SymbolSummary:
    @staticmethod
    def extract(content: str, ext: str = ".py") -> Dict[str, Any]:
        if ext in ('.py',):
            return SymbolSummary._extract_python(content)
        elif ext in ('.js', '.ts', '.tsx'):
            return SymbolSummary._extract_javascript(content)
        elif ext in ('.go',):
            return SymbolSummary._extract_go(content)
        return {"symbols": [], "raw": content[:500]}

    @staticmethod
    def _extract_python(content: str) -> Dict[str, Any]:
        symbols = []
        lines = content.split('\n')
        current_class = None
        class_indent = 0

        class_pattern = re.compile(r'^class\s+(\w+)')
        func_pattern = re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)')
        arg_pattern = re.compile(r'(\w+)')

        for i, line in enumerate(lines):
            stripped = line.strip()

            if stripped.startswith('#'):
                continue

            class_match = class_pattern.match(stripped)
            if class_match:
                current_class = class_match.group(1)
                class_indent = len(line) - len(line.lstrip())
                symbols.append({
                    "type": "class",
                    "name": current_class,
                    "line": i + 1,
                    "methods": [],
                })
                continue

            func_match = func_pattern.match(stripped)
            if func_match:
                func_name = func_match.group(1)
                args_str = func_match.group(2)
                args = arg_pattern.findall(args_str)
                args = [a for a in args if a not in ('self', 'cls')]

                line_indent = len(line) - len(line.lstrip())

                if current_class and line_indent > class_indent:
                    if symbols:
                        symbols[-1]["methods"].append({
                            "name": func_name,
                            "args": args,
                            "line": i + 1,
                        })
                else:
                    current_class = None
                    symbols.append({
                        "type": "function",
                        "name": func_name,
                        "args": args,
                        "line": i + 1,
                    })

        summary_lines = []
        for sym in symbols:
            if sym["type"] == "class":
                summary_lines.append(f"class {sym['name']}")
                for m in sym.get("methods", []):
                    args_str = ", ".join(m["args"]) if m["args"] else ""
                    summary_lines.append(f"  - {m['name']}({args_str})")
            else:
                args_str = ", ".join(sym.get("args", []))
                summary_lines.append(f"function {sym['name']}({args_str})")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

    @staticmethod
    def _extract_javascript(content: str) -> Dict[str, Any]:
        symbols = []
        lines = content.split('\n')

        class_pattern = re.compile(r'(?:export\s+)?class\s+(\w+)')
        func_pattern = re.compile(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)')
        method_pattern = re.compile(r'(\w+)\s*\(([^)]*)\)\s*\{')
        arrow_pattern = re.compile(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>')

        current_class = None

        for i, line in enumerate(lines):
            class_match = class_pattern.search(line)
            if class_match:
                current_class = class_match.group(1)
                symbols.append({
                    "type": "class",
                    "name": current_class,
                    "line": i + 1,
                    "methods": [],
                })
                continue

            func_match = func_pattern.search(line)
            if func_match:
                symbols.append({
                    "type": "function",
                    "name": func_match.group(1),
                    "line": i + 1,
                })

        summary_lines = []
        for sym in symbols:
            if sym["type"] == "class":
                summary_lines.append(f"class {sym['name']}")
            else:
                summary_lines.append(f"function {sym['name']}")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

    @staticmethod
    def _extract_go(content: str) -> Dict[str, Any]:
        symbols = []
        lines = content.split('\n')

        type_pattern = re.compile(r'type\s+(\w+)\s+struct')
        func_pattern = re.compile(r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(')

        for i, line in enumerate(lines):
            type_match = type_pattern.search(line)
            if type_match:
                symbols.append({
                    "type": "struct",
                    "name": type_match.group(1),
                    "line": i + 1,
                })
                continue

            func_match = func_pattern.search(line)
            if func_match:
                symbols.append({
                    "type": "function",
                    "name": func_match.group(1),
                    "line": i + 1,
                })

        summary_lines = []
        for sym in symbols:
            summary_lines.append(f"{sym['type']} {sym['name']}")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

class OutputCompactor:
    @staticmethod
    def compact_search_results(results: List[Dict[str, Any]], max_lines: int = 50) -> Dict[str, Any]:
        if len(results) <= max_lines:
            return {"results": results, "compacted": False}

        files: Dict[str, int] = {}
        for r in results:
            file_path = r.get("file", r.get("path", "unknown"))
            files[file_path] = files.get(file_path, 0) + 1

        return {
            "results": results[:max_lines],
            "compacted": True,
            "total_matches": len(results),
            "files_summary": [
                {"file": f, "matches": c} for f, c in sorted(files.items(), key=lambda x: -x[1])
            ],
            "hint": f"Showing {max_lines} of {len(results)} matches across {len(files)} files",
        }

    @staticmethod
    def compact_grep_output(output: str, max_lines: int = 20) -> Dict[str, Any]:
        lines = output.strip().split('\n')
        if len(lines) <= max_lines:
            return {"output": output, "compacted": False}

        files: Dict[str, int] = {}
        for line in lines:
            if ':' in line:
                file_path = line.split(':')[0]
                files[file_path] = files.get(file_path, 0) + 1

        return {
            "output": '\n'.join(lines[:max_lines]),
            "compacted": True,
            "total_lines": len(lines),
            "files": list(files.keys())[:10],
            "hint": f"Found matches in {len(files)} files",
        }

    @staticmethod
    def compact_file_list(files: List[str], max_display: int = 50) -> Dict[str, Any]:
        if len(files) <= max_display:
            return {"files": files, "compacted": False}

        by_ext: Dict[str, List[str]] = {}
        for f in files:
            ext = f.rsplit('.', 1)[-1] if '.' in f else 'no_ext'
            by_ext.setdefault(ext, []).append(f)

        ext_summary = {ext: len(lst) for ext, lst in by_ext.items()}

        return {
            "files": files[:max_display],
            "compacted": True,
            "total": len(files),
            "by_extension": ext_summary,
            "hint": f"Showing {max_display} of {len(files)} files",
        }
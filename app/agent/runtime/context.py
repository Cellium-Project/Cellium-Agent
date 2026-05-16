# -*- coding: utf-8 -*-
import re
from typing import List, Dict, Any, Optional, Tuple
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
        elif ext in ('.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.java', '.rs', '.cs', '.swift', '.kt'):
            return SymbolSummary._extract_c_style(content)
        return {"symbols": [], "raw": content[:500]}

    @staticmethod
    def _extract_python(content: str) -> Dict[str, Any]:
        lines = content.split('\n')
        n = len(lines)

        class_pattern = re.compile(r'^class\s+(\w+)(?:\([^)]*\))?:')
        func_start_pattern = re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(')

        def get_indent(line: str) -> int:
            return len(line) - len(line.lstrip())

        def collect_params(start_i: int) -> Tuple[str, int]:
            line = lines[start_i]
            stripped = line.strip()
            m = func_start_pattern.match(stripped)
            if not m:
                return '', start_i + 1

            func_name = m.group(1)
            paren_pos = stripped.find('(')
            if paren_pos == -1:
                return func_name, start_i + 1

            depth = 0
            close_pos = None
            for pos, ch in enumerate(stripped[paren_pos:], start=paren_pos):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                    if depth == 0:
                        close_pos = pos + 1
                        break

            if close_pos:
                params_str = stripped[paren_pos:close_pos]
                return func_name, params_str

            params_lines = [stripped[paren_pos:]]
            i = start_i + 1
            while i < n and depth > 0:
                next_stripped = lines[i].strip()
                for pos, ch in enumerate(next_stripped):
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                        if depth == 0:
                            params_lines.append(next_stripped[:pos + 1])
                            break
                if depth > 0:
                    params_lines.append(next_stripped)
                i += 1

            params_str = ' '.join(params_lines)
            return func_name, params_str

        def parse_params(params_str: str) -> List[str]:
            if not params_str or params_str.strip() in ('()', ''):
                return []
            content = params_str.strip()
            if content.startswith('('):
                content = content[1:]
            if content.endswith(')'):
                content = content[:-1]
            if content.endswith(':'):
                content = content[:-1]

            args = []
            depth = 0
            current = ''
            for ch in content:
                if ch in '([{<':
                    depth += 1
                    current += ch
                elif ch in ')]}>':
                    depth -= 1
                    current += ch
                elif ch == ',' and depth == 0:
                    param = current.strip()
                    if param:
                        name = param.split(':')[0].split('=')[0].strip()
                        if name and name not in ('self', 'cls'):
                            if name.startswith('**'):
                                name = name[2:]
                            elif name.startswith('*'):
                                name = name[1:]
                            args.append(name)
                    current = ''
                else:
                    current += ch
            if current.strip():
                param = current.strip()
                name = param.split(':')[0].split('=')[0].strip()
                if name and name not in ('self', 'cls'):
                    if name.startswith('**'):
                        name = name[2:]
                    elif name.startswith('*'):
                        name = name[1:]
                    args.append(name)
            return args

        def find_end(start_i: int, start_indent: int) -> int:
            j = start_i + 1
            while j < n:
                next_line = lines[j]
                next_stripped = next_line.strip()
                if next_stripped == '' or next_stripped.startswith('#'):
                    j += 1
                    continue
                if get_indent(next_line) <= start_indent:
                    break
                j += 1
            return j

        def extract_methods(start_i: int, end_i: int, class_indent: int) -> List[Dict]:
            methods = []
            method_indent = class_indent + 4
            i = start_i + 1
            while i < end_i:
                stripped = lines[i].strip()
                if stripped.startswith('#') or stripped == '':
                    i += 1
                    continue
                line_indent = get_indent(lines[i])
                if line_indent < method_indent:
                    i += 1
                    continue
                func_match = func_start_pattern.match(stripped)
                if func_match:
                    func_name, params_str = collect_params(i)
                    args = parse_params(params_str)
                    method_end = find_end(i, line_indent)
                    methods.append({
                        "name": func_name,
                        "args": args,
                        "line": i + 1,
                        "end_line": method_end,
                    })
                    i = method_end
                else:
                    i += 1
            return methods

        symbols = []
        i = 0
        while i < n:
            stripped = lines[i].strip()
            if stripped.startswith('#') or stripped == '':
                i += 1
                continue

            class_match = class_pattern.match(stripped)
            func_match = func_start_pattern.match(stripped)

            if class_match:
                start_indent = get_indent(lines[i])
                end_line = find_end(i, start_indent)
                methods = extract_methods(i, end_line, start_indent)
                symbols.append({
                    "type": "class",
                    "name": class_match.group(1),
                    "line": i + 1,
                    "end_line": end_line,
                    "methods": methods,
                })
                i = end_line

            elif func_match:
                start_indent = get_indent(lines[i])
                end_line = find_end(i, start_indent)
                func_name, params_str = collect_params(i)
                args = parse_params(params_str)
                symbols.append({
                    "type": "function",
                    "name": func_name,
                    "args": args,
                    "line": i + 1,
                    "end_line": end_line,
                })
                i = end_line

            else:
                i += 1

        summary_lines = []
        for sym in symbols:
            if sym["type"] == "class":
                summary_lines.append(f"class {sym['name']} (lines {sym['line']}-{sym['end_line']})")
                for m in sym.get("methods", []):
                    args_str = ", ".join(m["args"]) if m["args"] else ""
                    summary_lines.append(f"  - {m['name']}({args_str}) (lines {m['line']}-{m['end_line']})")
            else:
                args_str = ", ".join(sym.get("args", []))
                summary_lines.append(f"function {sym['name']}({args_str}) (lines {sym['line']}-{sym['end_line']})")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

    @staticmethod
    def _extract_javascript(content: str) -> Dict[str, Any]:
        symbols = []
        lines = content.split('\n')
        n = len(lines)

        class_pattern = re.compile(r'(?:export\s+)?class\s+(\w+)')
        func_pattern = re.compile(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)')

        def get_indent(line: str) -> int:
            return len(line) - len(line.lstrip())

        i = 0
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith('//') or stripped == '':
                i += 1
                continue

            class_match = class_pattern.search(stripped)
            func_match = func_pattern.search(stripped)

            if class_match or func_match:
                start_line = i + 1
                start_indent = get_indent(line)

                sym_type = "class" if class_match else "function"
                sym_name = class_match.group(1) if class_match else func_match.group(1)

                j = i + 1
                brace_count = line.count('{') - line.count('}')
                started = brace_count > 0
                while j < n:
                    next_line = lines[j]
                    next_stripped = next_line.strip()
                    if next_stripped.startswith('//'):
                        j += 1
                        continue
                    brace_count += next_line.count('{')
                    brace_count -= next_line.count('}')
                    if started and brace_count <= 0:
                        j += 1
                        break
                    if brace_count > 0:
                        started = True
                        next_indent = get_indent(next_line)
                        if next_indent <= start_indent and next_stripped != '':
                            break
                    j += 1
                end_line = j

                symbols.append({
                    "type": sym_type,
                    "name": sym_name,
                    "line": start_line,
                    "end_line": end_line,
                })
                i = j
            else:
                i += 1

        summary_lines = []
        for sym in symbols:
            if sym["type"] == "class":
                summary_lines.append(f"class {sym['name']} (lines {sym['line']}-{sym['end_line']})")
            else:
                summary_lines.append(f"function {sym['name']} (lines {sym['line']}-{sym['end_line']})")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

    @staticmethod
    def _extract_go(content: str) -> Dict[str, Any]:
        symbols = []
        lines = content.split('\n')
        n = len(lines)

        type_pattern = re.compile(r'type\s+(\w+)\s+struct')
        func_pattern = re.compile(r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(')

        i = 0
        while i < n:
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith('//') or stripped == '':
                i += 1
                continue

            type_match = type_pattern.search(stripped)
            func_match = func_pattern.search(stripped)

            if type_match or func_match:
                start_line = i + 1

                sym_type = "struct" if type_match else "function"
                sym_name = type_match.group(1) if type_match else func_match.group(1)

                j = i + 1
                brace_count = stripped.count('{') - stripped.count('}')
                while j < n and brace_count > 0:
                    brace_count += lines[j].count('{') - lines[j].count('}')
                    j += 1
                end_line = j

                symbols.append({
                    "type": sym_type,
                    "name": sym_name,
                    "line": start_line,
                    "end_line": end_line,
                })
                i = j
            else:
                i += 1

        summary_lines = []
        for sym in symbols:
            summary_lines.append(f"{sym['type']} {sym['name']} (lines {sym['line']}-{sym['end_line']})")

        return {
            "symbols": symbols,
            "summary": "\n".join(summary_lines),
            "total_symbols": len(symbols),
        }

    @staticmethod
    def _extract_c_style(content: str) -> Dict[str, Any]:
        lines = content.split('\n')
        n = len(lines)

        type_pattern = re.compile(r'\b(class|struct|interface|protocol|enum|impl|object)\s+(?:class\s+)?(\w+)')

        skip_keywords = {'if', 'else', 'while', 'for', 'switch', 'catch', 'try',
                         'return', 'sizeof', 'typeof', 'delete', 'throw'}

        def find_brace_end(start_i: int) -> int:
            j = start_i
            count = 0
            while j < n:
                for ch in lines[j]:
                    if ch == '{':
                        count += 1
                    elif ch == '}':
                        count -= 1
                        if count == 0:
                            return j + 1
                j += 1
            return n

        def is_func_start(stripped: str, i: int) -> str:
            if not stripped or stripped.startswith('//') or stripped.startswith('#'):
                return ''
            if stripped.startswith('public:') or stripped.startswith('private:') or stripped.startswith('protected:'):
                return ''

            brace_pos = stripped.find('{')
            if brace_pos >= 0:
                before = stripped[:brace_pos].strip()
            elif i + 1 < n:
                next_s = lines[i + 1].strip()
                if next_s == '{' or next_s.startswith('{'):
                    before = stripped
                else:
                    return ''
            else:
                return ''

            paren_open = before.find('(')
            paren_close = before.find(')')
            if paren_open < 0 or paren_close < 0 or paren_close <= paren_open:
                return ''

            before_paren = before[:paren_open].strip()
            if not before_paren:
                return ''

            parts = before_paren.split()
            name = parts[-1] if parts else ''

            if name.startswith('~'):
                name = name[1:]
            if name in skip_keywords:
                return ''

            return name

        def extract_members(start: int, end: int) -> List[Dict]:
            members = []
            i = start + 1
            while i < end:
                stripped = lines[i].strip()
                name = is_func_start(stripped, i)
                if name:
                    if '{' in stripped:
                        member_end = find_brace_end(i)
                    elif i + 1 < end and ('{' in lines[i + 1] or lines[i + 1].strip() == '{'):
                        member_end = find_brace_end(i + 1)
                    else:
                        member_end = min(i + 2, end)
                    members.append({"name": name, "line": i + 1, "end_line": member_end})
                    i = member_end
                else:
                    i += 1
            return members

        symbols = []
        class_ranges = []
        i = 0
        while i < n:
            stripped = lines[i].strip()

            if stripped.startswith('//') or stripped.startswith('#') or stripped == '' or stripped.startswith('using') or stripped.startswith('import') or stripped.startswith('package') or stripped.startswith('include'):
                i += 1
                continue

            m = type_pattern.search(stripped)
            if m:
                sym_type = m.group(1)
                name = m.group(2)
                if '{' in stripped:
                    end_line = find_brace_end(i)
                elif i + 1 < n and '{' in lines[i + 1]:
                    end_line = find_brace_end(i + 1)
                else:
                    end_line = min(i + 5, n)
                class_ranges.append((i, end_line))
                members = extract_members(i, end_line)
                symbols.append({
                    "type": sym_type,
                    "name": name,
                    "line": i + 1,
                    "end_line": end_line,
                    "methods": members,
                })
                i = end_line
                continue

            i += 1

        i = 0
        while i < n:
            in_class = any(s <= i < e for s, e in class_ranges)
            if in_class:
                i += 1
                continue

            stripped = lines[i].strip()
            name = is_func_start(stripped, i)
            if name:
                if '{' in stripped:
                    end_line = find_brace_end(i)
                elif i + 1 < n and ('{' in lines[i + 1] or lines[i + 1].strip() == '{'):
                    end_line = find_brace_end(i + 1)
                else:
                    end_line = min(i + 2, n)
                symbols.append({
                    "type": "function",
                    "name": name,
                    "line": i + 1,
                    "end_line": end_line,
                })
                i = end_line
            else:
                i += 1

        symbols.sort(key=lambda x: x["line"])

        summary_lines = []
        for sym in symbols:
            if sym["type"] in ("class", "struct", "interface", "enum"):
                summary_lines.append(f"{sym['type']} {sym['name']} (lines {sym['line']}-{sym['end_line']})")
                for m in sym.get("methods", []):
                    summary_lines.append(f"  - {m['name']}() (lines {m['line']}-{m['end_line']})")
            else:
                summary_lines.append(f"function {sym['name']}() (lines {sym['line']}-{sym['end_line']})")

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
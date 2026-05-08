# -*- coding: utf-8 -*-
import os
import json
import subprocess
import asyncio
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class Diagnostic:
    file: str
    line: int
    column: int
    severity: str
    message: str
    code: Optional[str] = None
    source: Optional[str] = None

class BuiltInDiagnostics:
    @staticmethod
    def check_python(code: str, filepath: str = "<string>") -> List[Diagnostic]:
        diagnostics = []
        try:
            compile(code, filepath, "exec")
        except SyntaxError as e:
            diagnostics.append(Diagnostic(
                file=filepath,
                line=e.lineno or 1,
                column=e.offset or 1,
                severity="error",
                message=e.msg or str(e),
                code="SyntaxError",
                source="compile",
            ))
        except Exception as e:
            diagnostics.append(Diagnostic(
                file=filepath,
                line=1,
                column=1,
                severity="error",
                message=str(e),
                source="compile",
            ))
        return diagnostics

    @staticmethod
    def check_json(code: str, filepath: str = "<string>") -> List[Diagnostic]:
        diagnostics = []
        try:
            json.loads(code)
        except json.JSONDecodeError as e:
            diagnostics.append(Diagnostic(
                file=filepath,
                line=e.lineno or 1,
                column=e.colno or 1,
                severity="error",
                message=e.msg or str(e),
                code="JSONDecodeError",
                source="json",
            ))
        return diagnostics

    @staticmethod
    def check_yaml(code: str, filepath: str = "<string>") -> List[Diagnostic]:
        diagnostics = []
        try:
            import yaml
            yaml.safe_load(code)
        except yaml.YAMLError as e:
            line = 1
            if hasattr(e, 'problem_mark') and e.problem_mark:
                line = e.problem_mark.line + 1
            diagnostics.append(Diagnostic(
                file=filepath,
                line=line,
                column=1,
                severity="error",
                message=str(e),
                code="YAMLError",
                source="yaml",
            ))
        except ImportError:
            pass
        return diagnostics

class ExternalDiagnostics:
    @staticmethod
    def check_python_py_compile(filepath: str) -> List[Diagnostic]:
        try:
            result = subprocess.run(
                ["python", "-m", "py_compile", filepath],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return []
            return ExternalDiagnostics._parse_py_compile_output(result.stderr, filepath)
        except Exception:
            return []

    @staticmethod
    def _parse_py_compile_output(output: str, filepath: str) -> List[Diagnostic]:
        import re
        diagnostics = []
        file_pattern = r'File "([^"]+)", line (\d+)'
        error_pattern = r'(SyntaxError|IndentationError|TabError): (.+)'

        file_match = re.search(file_pattern, output)
        error_match = re.search(error_pattern, output)

        if file_match and error_match:
            diagnostics.append(Diagnostic(
                file=file_match.group(1),
                line=int(file_match.group(2)),
                column=1,
                severity="error",
                message=f"{error_match.group(1)}: {error_match.group(2)}",
                source="py_compile",
            ))
        return diagnostics

    @staticmethod
    def check_typescript(filepath: str) -> List[Diagnostic]:
        try:
            cmd = ["npx", "tsc", "--noEmit"]
            if not os.path.exists("tsconfig.json"):
                cmd.append(filepath)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return []
            return ExternalDiagnostics._parse_tsc_output(result.stdout + result.stderr, filepath)
        except Exception:
            return []

    @staticmethod
    def _parse_tsc_output(output: str, filepath: str) -> List[Diagnostic]:
        import re
        diagnostics = []
        pattern = r'(.+?)\((\d+),(\d+)\):\s*error\s*(\w+):\s*(.+)'
        for match in re.finditer(pattern, output):
            diagnostics.append(Diagnostic(
                file=match.group(1),
                line=int(match.group(2)),
                column=int(match.group(3)),
                severity="error",
                code=match.group(4),
                message=match.group(5),
                source="tsc",
            ))
        return diagnostics

    @staticmethod
    def check_go(filepath: str) -> List[Diagnostic]:
        try:
            result = subprocess.run(
                ["go", "build", "-o", os.devnull, filepath],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return []
            return ExternalDiagnostics._parse_go_output(result.stderr, filepath)
        except Exception:
            return []

    @staticmethod
    def _parse_go_output(output: str, filepath: str) -> List[Diagnostic]:
        import re
        diagnostics = []
        pattern = r'(.+?):(\d+):(\d+):\s*(.+)'
        for match in re.finditer(pattern, output):
            diagnostics.append(Diagnostic(
                file=match.group(1),
                line=int(match.group(2)),
                column=int(match.group(3)),
                severity="error",
                message=match.group(4),
                source="go",
            ))
        return diagnostics

class DiagnosticEngine:
    def __init__(self, use_external: bool = True):
        self.use_external = use_external

    def _detect_encoding(self, filepath: str) -> str:
        """检测文件编码"""
        try:
            with open(filepath, 'rb') as f:
                raw = f.read(4)

            if raw[:3] == b'\xef\xbb\xbf':
                return 'utf-8-sig'
            if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
                return 'utf-16'

            with open(filepath, 'rb') as f:
                content = f.read(65536)
            try:
                content.decode('utf-8')
                return 'utf-8'
            except UnicodeDecodeError:
                pass
            for enc in ('gbk', 'gb2312', 'big5'):
                try:
                    content.decode(enc)
                    return enc
                except UnicodeDecodeError:
                    continue
        except Exception:
            pass
        return 'utf-8'

    def check(self, filepath: str, content: Optional[str] = None) -> List[Diagnostic]:
        ext = os.path.splitext(filepath)[1].lower()

        if content is None:
            try:
                encoding = self._detect_encoding(filepath)
                with open(filepath, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
            except Exception:
                return []

        diagnostics = self._check_builtin(filepath, content, ext)

        if not diagnostics and self.use_external:
            external = self._check_external(filepath, ext)
            diagnostics.extend(external)

        return diagnostics

    def _check_builtin(self, filepath: str, content: str, ext: str) -> List[Diagnostic]:
        if ext == '.py':
            return BuiltInDiagnostics.check_python(content, filepath)
        elif ext == '.json':
            return BuiltInDiagnostics.check_json(content, filepath)
        elif ext in ('.yaml', '.yml'):
            return BuiltInDiagnostics.check_yaml(content, filepath)
        return []

    def _check_external(self, filepath: str, ext: str) -> List[Diagnostic]:
        if ext == '.py':
            return ExternalDiagnostics.check_python_py_compile(filepath)
        elif ext in ('.ts', '.tsx'):
            return ExternalDiagnostics.check_typescript(filepath)
        elif ext == '.go':
            return ExternalDiagnostics.check_go(filepath)
        return []

    def has_errors(self, diagnostics: List[Diagnostic]) -> bool:
        return any(d.severity == "error" for d in diagnostics)

class DiagnosticLoop:
    def __init__(self):
        self.engine = DiagnosticEngine()
        self._snapshots: Dict[str, str] = {}

    def snapshot(self, filepath: str) -> str:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            import uuid
            snap_id = str(uuid.uuid4())[:8]
            self._snapshots[snap_id] = content
            return snap_id
        except Exception:
            return ""

    def rollback(self, snap_id: str, filepath: str) -> bool:
        if snap_id not in self._snapshots:
            return False
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(self._snapshots[snap_id])
            del self._snapshots[snap_id]
            return True
        except Exception:
            return False

    def edit_with_validation(
        self,
        filepath: str,
        old_string: str,
        new_string: str,
        max_retries: int = 0,
    ) -> Dict[str, Any]:
        snap_id = self.snapshot(filepath)
        if not snap_id:
            return {"error": "Failed to create snapshot"}

        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            if old_string not in content:
                return {"error": f"Old string not found", "preview": old_string[:50]}

            new_content = content.replace(old_string, new_string, 1)

            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)

            diagnostics = self.engine.check(filepath, new_content)

            if not self.engine.has_errors(diagnostics):
                if snap_id in self._snapshots:
                    del self._snapshots[snap_id]
                return {
                    "success": True,
                    "path": filepath,
                    "bytes_changed": len(new_string) - len(old_string),
                }

            self.rollback(snap_id, filepath)

            return {
                "error": "Diagnostics failed",
                "rollback": True,
                "diagnostics": [
                    {"line": d.line, "message": d.message, "severity": d.severity}
                    for d in diagnostics[:5]
                ],
            }
        except Exception as e:
            self.rollback(snap_id, filepath)
            return {"error": str(e), "rollback": True}

    def edit_range_with_validation(
        self,
        filepath: str,
        start_byte: int,
        end_byte: int,
        new_text: str,
    ) -> Dict[str, Any]:
        snap_id = self.snapshot(filepath)
        if not snap_id:
            return {"error": "Failed to create snapshot"}

        try:
            with open(filepath, 'rb') as f:
                content = f.read()

            new_content = content[:start_byte] + new_text.encode('utf-8') + content[end_byte:]

            with open(filepath, 'wb') as f:
                f.write(new_content)

            diagnostics = self.engine.check(filepath, new_content.decode('utf-8', errors='replace'))

            if not self.engine.has_errors(diagnostics):
                if snap_id in self._snapshots:
                    del self._snapshots[snap_id]
                return {
                    "success": True,
                    "path": filepath,
                    "bytes_changed": end_byte - start_byte,
                }

            self.rollback(snap_id, filepath)

            return {
                "error": "Diagnostics failed",
                "rollback": True,
                "diagnostics": [
                    {"line": d.line, "message": d.message, "severity": d.severity}
                    for d in diagnostics[:5]
                ],
            }
        except Exception as e:
            self.rollback(snap_id, filepath)
            return {"error": str(e), "rollback": True}
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class Patch:
    path: str
    start_byte: int
    end_byte: int
    old_text: str
    new_text: str

class PatchEngine:
    @staticmethod
    def generate_unified_diff(old_content: str, new_content: str, path: str, context_lines: int = 3) -> str:
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')

        diff_lines = [f"--- {path}", f"+++ {path}"]

        old_idx = 0
        new_idx = 0
        changes = []

        while old_idx < len(old_lines) or new_idx < len(new_lines):
            old_line = old_lines[old_idx] if old_idx < len(old_lines) else None
            new_line = new_lines[new_idx] if new_idx < len(new_lines) else None

            if old_line == new_line:
                changes.append((' ', old_line))
                old_idx += 1
                new_idx += 1
            elif old_line is None:
                changes.append(('+', new_line))
                new_idx += 1
            elif new_line is None:
                changes.append(('-', old_line))
                old_idx += 1
            else:
                changes.append(('-', old_line))
                changes.append(('+', new_line))
                old_idx += 1
                new_idx += 1

        for prefix, line in changes[:50]:
            diff_lines.append(f"{prefix}{line}")

        if len(changes) > 50:
            diff_lines.append("... (truncated)")

        return '\n'.join(diff_lines)

    @staticmethod
    def apply_patch(content: str, patch: Patch) -> str:
        content_bytes = content.encode('utf-8')
        new_text_bytes = patch.new_text.encode('utf-8')
        new_content_bytes = content_bytes[:patch.start_byte] + new_text_bytes + content_bytes[patch.end_byte:]
        return new_content_bytes.decode('utf-8', errors='replace')

    @staticmethod
    def create_patch_from_string(content: str, old_string: str, new_string: str) -> Optional[Patch]:
        old_bytes = old_string.encode('utf-8')
        content_bytes = content.encode('utf-8')

        idx = content_bytes.find(old_bytes)
        if idx == -1:
            return None

        return Patch(
            path="",
            start_byte=idx,
            end_byte=idx + len(old_bytes),
            old_text=old_string,
            new_text=new_string,
        )

    @staticmethod
    def find_all_patches(content: str, old_string: str, new_string: str) -> List[Patch]:
        patches = []
        old_bytes = old_string.encode('utf-8')
        content_bytes = content.encode('utf-8')

        idx = 0
        while True:
            pos = content_bytes.find(old_bytes, idx)
            if pos == -1:
                break
            patches.append(Patch(
                path="",
                start_byte=pos,
                end_byte=pos + len(old_bytes),
                old_text=old_string,
                new_text=new_string,
            ))
            idx = pos + 1

        return patches

    @staticmethod
    def validate_patch(content: str, patch: Patch) -> Tuple[bool, str]:
        content_bytes = content.encode('utf-8')

        if patch.start_byte < 0:
            return False, "start_byte < 0"

        if patch.end_byte > len(content_bytes):
            return False, f"end_byte {patch.end_byte} > content length {len(content_bytes)}"

        if patch.start_byte > patch.end_byte:
            return False, "start_byte > end_byte"

        actual_old = content_bytes[patch.start_byte:patch.end_byte].decode('utf-8', errors='replace')
        if actual_old != patch.old_text:
            return False, f"old_text mismatch at [{patch.start_byte}:{patch.end_byte}]"

        return True, ""

    @staticmethod
    def reverse_patch(patch: Patch) -> Patch:
        return Patch(
            path=patch.path,
            start_byte=patch.start_byte,
            end_byte=patch.start_byte + len(patch.new_text.encode('utf-8')),
            old_text=patch.new_text,
            new_text=patch.old_text,
        )
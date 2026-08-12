# -*- coding: utf-8 -*-
from rich.text import Text

CELL_FRAMES = ["◐", "◓", "◉", "◎", "◉", "◒"]

_PULSE_TOP = {2, 3}


def cell_spinner_text(frame: int, state_text: str, color: str) -> Text:
    glyph = CELL_FRAMES[frame % len(CELL_FRAMES)]
    t = Text(f"{glyph} ", style=f"bold {color}")
    t.append(state_text, style="dim")
    return t


def cell_phase(frame: int) -> str:
    i = frame % len(CELL_FRAMES)
    if i in _PULSE_TOP:
        return "working"
    return "thinking"

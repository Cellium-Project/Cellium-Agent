# -*- coding: utf-8 -*-
"""
Cellium TUI 组件
"""
import json
import re
import sys
import time

from rich import box as rich_box
from rich.console import Console, ConsoleOptions
from rich.markdown import TableElement, Heading
from rich.console import RenderResult
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.containers import Vertical, VerticalScroll
from textual import on, events
from textual.suggester import Suggester
from textual.visual import RichVisual
from textual.widgets import OptionList, Static, TextArea
from textual.widgets._option_list import Option

_TIME_FMT = "%H:%M:%S"


def _patch_rich_emoji_width():

    try:
        from typing import Callable
        from functools import lru_cache
        from rich import cells as rich_cells
        orig_char = rich_cells.get_character_cell_size

        @lru_cache(maxsize=8192)
        def _fixed(character: str) -> int:
            return orig_char(character)

        def _fixed_cell_size(character: str, unicode_version: str = "auto") -> int:
            return _fixed(character)

        rich_cells.get_character_cell_size = _fixed_cell_size

        def _cached_len(text: str, unicode_version: str = "auto") -> int:
            if len(text) < 512 and rich_cells._is_single_cell_widths(text):
                return len(text)
            total = 0
            i = 0
            n = len(text)
            while i < n:
                ch = text[i]
                if (
                    i + 1 < n
                    and text[i + 1] == "\ufe0f"
                    and ch != "\ufe0f"
                ):
                    total += 2  
                    i += 2
                else:
                    total += orig_char(ch)
                    i += 1
            return total

        rich_cells.cached_cell_len = _cached_len

        def _cell_len(text: str, unicode_version: str = "auto") -> int:
            if len(text) < 512:
                return _cached_len(text)
            if rich_cells._is_single_cell_widths(text):
                return len(text)
            return _cached_len(text)

        rich_cells.cell_len = _cell_len

        def _chop_cells(text: str, width: int, unicode_version: str = "auto") -> list:
            lines: list[list[str]] = [[]]
            total_width = 0
            i = 0
            n = len(text)
            while i < n:
                ch = text[i]
                if (
                    i + 1 < n
                    and text[i + 1] == "\ufe0f"
                    and ch != "\ufe0f"
                ):
                    unit = text[i:i + 2]
                    cell_width = 2
                    i += 2
                else:
                    unit = ch
                    cell_width = orig_char(ch)
                    i += 1
                if total_width + cell_width > width:
                    lines.append([unit])
                    total_width = cell_width
                else:
                    lines[-1].append(unit)
                    total_width += cell_width
            return ["".join(line) for line in lines]

        rich_cells.chop_cells = _chop_cells

        import importlib
        for _name in ("rich.text", "rich.segment"):
            try:
                _m = importlib.import_module(_name)
                _m.cell_len = _cell_len
                _m.cached_cell_len = _cached_len
                _m.chop_cells = _chop_cells
                _m.get_character_cell_size = _fixed_cell_size
                _m.set_cell_size = rich_cells.set_cell_size
            except Exception:
                pass
    except Exception:
        pass


_patch_rich_emoji_width()

FRAMES = "-\\|/"

_COMMANDS = ("help", "clear", "theme", "models", "session", "new", "settings", "exit")

class ChatScroll(VerticalScroll):
    """聊天滚动容器"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._following = True

    @property
    def following(self) -> bool:
        return self._following

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        try:
            self._update_following(new_value)
            if new_value <= 1 and old_value > new_value:
                app = self.app
                if hasattr(app, "_on_chat_scrolled_top"):
                    app._on_chat_scrolled_top()
        except Exception:
            pass

    def _update_following(self, new_value: float) -> None:
        try:
            self._following = new_value >= self.max_scroll_y - 1.0
        except Exception:
            pass

    def scroll_to_follow(self, animate: bool = False) -> None:
        if self._following:
            self.scroll_end(animate=animate)

    def force_scroll_bottom(self, animate: bool = False) -> None:
        self._following = True
        self.scroll_end(animate=animate)


class CommandInput(TextArea):

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("soft_wrap", True)
        kwargs.setdefault("tab_behavior", "focus")
        super().__init__(*args, **kwargs)
        self._last_placeholder = ""

    @on(events.Focus)
    def _on_command_focus(self, event):
        self._last_placeholder = self.placeholder
        self.placeholder = ""

    @on(events.Blur)
    def _on_command_blur(self, event):
        if self.placeholder == "" and self._last_placeholder:
            self.placeholder = self._last_placeholder

    def set_placeholder_aware(self, text: str):
        if getattr(self, "has_focus", False):
            self._last_placeholder = text
            self.placeholder = ""
        else:
            self.placeholder = text

    # ---- Input 兼容层 ----

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, v: str):
        self.text = v or ""
        self.move_cursor(self.document.end, select=False)

    @property
    def cursor_position(self) -> int:
        return self.cursor_location[1]

    @cursor_position.setter
    def cursor_position(self, pos: int):
        try:
            loc = self.document.get_location_from_index(min(pos, len(self.text)))
            self.move_cursor(loc)
        except Exception:
            pass

    def insert_text_at_cursor(self, text: str):
        self.insert(text)

    # ---- 键盘 ----

    async def _on_key(self, event):
        app = self.app
        if getattr(app, "_palette_active", False):
            key = event.key
            if key == "up":
                app._palette_move(-1)
                event.stop()
                return
            if key == "down":
                app._palette_move(1)
                event.stop()
                return
            if key in ("enter", "tab"):
                app._palette_accept()
                try:
                    event.prevent_default()
                except Exception:
                    pass
                event.stop()
                return
            if key == "escape":
                app._palette_hide()
                event.stop()
                return
        if event.key == "enter":
            app._submit_input()
            try:
                event.prevent_default()
            except Exception:
                pass
            event.stop()
            return
        # 换行组合键：Shift/Ctrl/Alt+Enter、Ctrl+J 插入 \n
        if event.key in ("shift+enter", "ctrl+enter", "alt+enter", "ctrl+j"):
            self.insert("\n")
            try:
                event.prevent_default()
            except Exception:
                pass
            event.stop()
            return
        # 输入栏展开/收起全部折叠项：Ctrl+E
        if event.key == "ctrl+e":
            try:
                cards = list(app.query(ToolCallCard))
                any_folded = any(
                    cid not in card._expanded_diffs
                    for card in cards
                    for cid in card._foldable
                )
                for card in cards:
                    if not card._foldable:
                        continue
                    if any_folded:
                        card._expanded_diffs.update(card._foldable)
                    else:
                        card._expanded_diffs.difference_update(card._foldable)
                    card.update(card._build(done=card._done))
            except Exception:
                pass
            event.stop()
            return
        if event.key in ("pageup", "pagedown"):
            chat = getattr(app, "chat", None)
            if chat is not None:
                try:
                    if event.key == "pageup":
                        chat.scroll_page_up()
                    else:
                        chat.scroll_page_down()
                except Exception:
                    pass
                event.stop()
                return
        if event.key == "escape" and not app._palette_active:
            return
        await super()._on_key(event)

    def on_paste(self, event) -> None:
        text = getattr(event, "text", None)
        if not text:
            return
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not normalized:
            return
        line_count = normalized.count("\n") + 1
        # 折叠判定：≥3 行 或 >150 字符
        if line_count >= 3 or len(normalized) > 150:
            self._paste_pending = normalized
            marker = f"[Pasted ~{line_count} lines]"
            self.value = marker
        else:
            self.insert_text_at_cursor(normalized)
            self.app._update_palette(self.value)
        event.stop()


class CommandPalette(Vertical):
    """/ 命令补全面板"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._commands = []
        self._selected = 0
        self._list = None

    def compose(self):
        tr = self.app.tr if self.app else (lambda k, *a: k)
        yield Static(tr("cmd.palette_title"), id="palette-title")
        yield OptionList(id="palette-list")
        yield Static(tr("cmd.palette_footer"), id="palette-footer")

    def on_mount(self):
        self._list = self.query_one("#palette-list", OptionList)

    def set_commands(self, commands):
        self._commands = commands
        self._selected = 0
        lst = self._list
        if lst is None:
            return
        tr = self.app.tr if self.app else (lambda k, *a: k)
        lst.clear_options()
        opts = []
        for cmd in commands:
            name = "/" + cmd["slash"]
            desc = tr(cmd["desc_key"])
            opts.append(Option(f"{name}  [dim]{desc}[/]", id=cmd["slash"]))
        lst.add_options(opts)
        if opts:
            lst.highlighted = 0

    def move(self, delta):
        lst = self._list
        if not lst or not self._commands:
            return
        total = len(self._commands)
        idx = (lst.highlighted or 0) + delta
        lst.highlighted = idx % total

    def current(self):
        lst = self._list
        if lst is None:
            return None
        idx = lst.highlighted
        if idx is not None and 0 <= idx < len(self._commands):
            return self._commands[idx]
        return None


def _fmt_time():
    return time.strftime(_TIME_FMT, time.localtime())


def _preview(value, max_len=200):
    if isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, default=str)
    elif not isinstance(value, str):
        value = str(value)
    value = " ".join(line.strip() for line in value.splitlines())
    if len(value) > max_len:
        return value[:max_len] + "..."
    return value


class UserMessage(Static):
    """用户消息"""

    def __init__(self, content):
        super().__init__(content, markup=False)
        self.add_class("user-msg")


LOGO_RENDER_WIDTH = 18
LOGO_RENDER_HEIGHT = 10
LOGO_PALETTE = (
    (0, 0, 0, 0), (0, 170, 255, 3), (0, 255, 255, 1), (0, 161, 255, 30), (0, 163, 255, 131), (0, 160, 255, 54),
    (0, 161, 255, 178), (0, 161, 255, 164), (0, 163, 255, 103), (0, 160, 255, 103), (0, 162, 255, 55), (0, 162, 255, 132),
    (0, 127, 255, 2), (0, 163, 255, 89), (0, 170, 255, 15), (0, 162, 255, 176), (0, 162, 255, 113), (0, 161, 255, 148),
    (0, 162, 255, 182), (0, 158, 255, 53), (0, 161, 255, 131), (0, 141, 255, 9), (0, 156, 255, 39), (0, 139, 255, 11),
    (0, 145, 255, 7), (0, 157, 255, 42), (0, 162, 255, 130), (0, 161, 255, 63), (0, 162, 255, 190), (0, 162, 255, 160),
    (0, 161, 255, 106), (0, 162, 255, 165), (0, 156, 255, 13), (0, 161, 255, 71), (0, 153, 255, 10), (0, 157, 255, 21),
    (0, 137, 255, 13), (0, 150, 255, 22), (0, 155, 255, 23), (0, 166, 243, 23), (15, 165, 240, 17), (0, 0, 255, 3),
    (4, 163, 250, 53), (0, 120, 255, 34), (0, 165, 240, 17), (0, 159, 255, 48), (0, 159, 255, 59), (0, 148, 255, 12),
    (0, 145, 255, 14), (0, 157, 242, 21), (0, 153, 255, 20), (0, 85, 255, 12), (0, 69, 255, 11), (50, 245, 170, 136),
    (47, 240, 171, 70), (70, 255, 167, 246), (41, 229, 186, 149), (68, 241, 185, 247), (35, 227, 184, 149), (62, 241, 182, 247),
    (36, 244, 167, 70), (54, 255, 159, 246), (33, 245, 161, 136), (0, 161, 255, 57), (10, 170, 244, 24), (10, 163, 234, 25),
    (0, 31, 255, 8), (0, 0, 255, 6), (36, 236, 170, 84), (41, 248, 162, 152), (63, 251, 167, 250), (68, 253, 168, 245),
    (99, 255, 184, 229), (108, 255, 186, 227), (101, 240, 203, 231), (103, 235, 213, 236), (89, 240, 196, 231), (88, 235, 205, 236),
    (77, 255, 173, 229), (85, 255, 172, 227), (40, 251, 156, 250), (44, 255, 156, 245), (18, 236, 160, 84), (18, 248, 150, 152),
    (0, 170, 244, 24), (0, 163, 234, 25), (0, 162, 255, 11), (9, 166, 235, 26), (0, 161, 241, 19), (0, 0, 255, 1),
    (0, 132, 255, 27), (34, 251, 154, 147), (11, 208, 201, 114), (54, 247, 171, 247), (29, 237, 170, 252), (70, 235, 195, 232),
    (54, 247, 171, 231), (77, 235, 199, 236), (70, 255, 168, 227), (71, 235, 196, 236), (66, 255, 166, 227), (56, 235, 187, 232),
    (43, 247, 164, 231), (35, 247, 160, 247), (16, 237, 161, 252), (13, 251, 143, 146), (2, 206, 195, 115), (0, 166, 235, 26),
    (0, 160, 255, 46), (0, 161, 255, 19), (0, 160, 255, 65), (0, 160, 255, 35), (0, 163, 250, 61), (0, 144, 255, 23),
    (0, 150, 255, 17), (20, 251, 145, 135), (0, 46, 255, 11), (26, 255, 146, 246), (3, 234, 160, 73), (30, 252, 150, 247),
    (7, 247, 145, 135), (27, 252, 148, 247), (3, 247, 143, 135), (17, 255, 140, 246), (0, 234, 157, 73), (5, 251, 137, 135),
    (0, 133, 255, 23), (0, 163, 255, 189), (0, 163, 255, 106), (0, 162, 255, 22), (0, 162, 243, 22), (0, 28, 255, 9),
    (0, 159, 233, 24), (0, 0, 255, 4), (0, 170, 233, 24), (0, 159, 255, 8), (0, 153, 255, 15), (0, 161, 255, 38),
    (0, 165, 255, 54),
)
LOGO_INDEX_B85 = (
    "00000009C30RR9400adF2M7lV3I+fR00RL40ssI3000003;+ND01XZg5D^j+6crX17#SED78e>75)%*+"
    "4i5kg009gD8yp=TA0Qzj7b7GkCMPH<Dk~~0EGZ@@BqbLkAtE0j9UdDT4+bqRFEAz}F(WcFG&MFiI5|2y"
    "JUui%GBYtFCL=E}EiMlR1_nPrARs_NLPJDFMn_0VN=r;lPESx#Qd2@hR8=4#KR*Tr1_nPrAXZmcSz23M"
    "U0z>cVPa!sWoBn+X=+(oYgiywKR*Tr1`j`MGHq^ea4>Olb98lfcX)YvdwhL#esXXyZf`PeKWqjMe;a@u"
    "FCQ2oCKrJuf`NmCg@%QOgM@;Cfg~mu7$GkofE^qg01N>D4FC@g5fBp+hZKkwfro*Ih>3?36A}>+4-O3g"
    "0RR9D000000RR93009F41ONt#2?q%W1_}!R0{{R400IF300000"
)

class LogoImage(Static):
    """终端 Logo"""

    def __init__(self, width=18):
        super().__init__("")
        self.add_class("logo")
        self._width = width
        self._cached = None

    def _render_pixels(self):
        try:
            import base64

            bg = "#f5f5f5" if getattr(self.app, "theme", "cellium-dark") == "cellium-light" else "#0d1117"
            bg = bg.lstrip("#")
            self._bg_rgb = (
                int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
            )

            index = base64.b85decode("".join(LOGO_INDEX_B85))
            w = LOGO_RENDER_WIDTH
            h = LOGO_RENDER_HEIGHT
            cells = []
            off = 0
            for _y in range(h):
                row = []
                for _x in range(w):
                    top = LOGO_PALETTE[index[off]]
                    bot = LOGO_PALETTE[index[off + 1]]
                    off += 2
                    row.append((top, bot))
                cells.append(row)
            return cells
        except Exception:
            return None

    def _build_text(self):
        cells = self._render_pixels()
        if not cells:
            return None
        from rich.text import Text as RichText
        result = RichText()
        bg = getattr(self, "_bg_rgb", (13, 17, 23))

        def composite(pixel):
            r, g, b, alpha = pixel
            if alpha >= 250:
                return r, g, b
            k = alpha / 255
            return (
                round(r * k + bg[0] * (1 - k)),
                round(g * k + bg[1] * (1 - k)),
                round(b * k + bg[2] * (1 - k)),
            )

        for row in cells:
            if result:
                result.append("\n")
            for top, bot in row:
                if top[3] < 16 and bot[3] < 16:
                    result.append(" ")
                    continue
                tr, tg, tb = composite(top)
                br, bgc, bb = composite(bot)
                result.append(
                    "▀",
                    style=f"#{tr:02x}{tg:02x}{tb:02x} on #{br:02x}{bgc:02x}{bb:02x}",
                )
        return result

    def refresh_theme(self):
        self._cached = None
        self.refresh()

    def render(self):
        if self._cached is None:
            self._cached = self._build_text()
        return self._cached or ""


class _TextualStyleTable(TableElement):

    # 细线网格字符
    _GRID_BOX = rich_box.Box(
        "┌─┬┐\n"
        "│ ││\n"
        "├─┼┤\n"
        "│ ││\n"
        "├─┼┤\n"
        "├─┼┤\n"
        "│ ││\n"
        "└─┴┘\n"
    )

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        from rich.style import Style
        from rich.cells import cell_len
        def _style(name: str) -> Style:
            try:
                return console.get_style(name)
            except Exception:
                return Style()

        header_style = _style("markdown.table.header") + Style(bold=True)
        border_style = _style("markdown.table.border")

        # 收集表头 + 所有行，计算各列最大内容宽度（cell_len 正确处理中文/emoji 宽字符）
        cols = []
        rows = []
        if self.header is not None and self.header.row is not None:
            cols = [str(c.content) for c in self.header.row.cells]
        if self.body is not None:
            rows = [[str(e.content) for e in r.cells] for r in self.body.rows]

        widths = []
        for ci in range(len(cols)):
            m = cell_len(cols[ci])
            for r in rows:
                if ci < len(r):
                    m = max(m, cell_len(r[ci]))
            widths.append(m)
        total = max(1, sum(widths))

        table = Table(
            box=self._GRID_BOX,
            border_style=border_style,
            header_style=header_style,
            pad_edge=False,
            padding=(0, 1),
            expand=True,
            show_lines=True,
            collapse_padding=True,
        )

        min_widths = [max(6, cell_len(col) + 2) for col in cols]
        for ci, col in enumerate(cols):
            table.add_column(
                col,
                ratio=max(1, widths[ci]),
                min_width=min_widths[ci],
            )

        for r in rows:
            table.add_row(*r)

        yield table

class _LeftAlignHeading(Heading):

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        text = self.text
        text.justify = "left"
        yield text

_FENCE_START = re.compile(r"^[ \t]*`{3,}")
_FENCE_MISJOIN = re.compile(
    r"^([ \t]*(?:#{1,6}[ \t]+[^\n`]*|[^\n`][^\n`]*)[^`\s])```[^\n`]*$"
)


def _normalize_md(markdown: str) -> str:
    out = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE_START.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        m = _FENCE_MISJOIN.match(line)
        if m:
            out.append(m.group(1))
            out.append(line[len(m.group(1)):])
        else:
            out.append(line)
    return "\n".join(out)

def _build_rich_md(markdown: str):
    from rich.markdown import Markdown as RichMarkdown

    class CelliumRichMarkdown(RichMarkdown):
        elements = {
            **RichMarkdown.elements,
            "table_open": _TextualStyleTable,
            "heading_open": _LeftAlignHeading,
        }

    return CelliumRichMarkdown(_normalize_md(markdown))

class _SelectableRichVisual(RichVisual):

    def __init__(self, widget, md):
        super().__init__(widget, md)
        self._render_cache_key = None
        self._cached_strips = None

    def _theme_key(self):
        try:
            theme = self._widget.app.theme
        except Exception:
            return ""
        return theme if isinstance(theme, str) else str(theme)

    def render_strips(
        self, width: int, height: int | None, style: object, options
    ) -> list:
        key = (id(self._renderable), width, height, self._theme_key())
        if options.selection is None and self._render_cache_key == key:
            return self._cached_strips
        strips = super().render_strips(width, height, style, options)
        strips = [
            strip.apply_offsets(0, y) for y, strip in enumerate(strips)
        ]
        if options.selection is None:
            self._render_cache_key = key
            self._cached_strips = strips
            return strips
        selection = options.selection
        selection_style = options.selection_style
        get_span = selection.get_span
        result = []
        for y, strip in enumerate(strips):
            span = get_span(y)
            if span is None:
                result.append(strip)
                continue
            start, end = span
            if end == -1:
                end = strip.cell_length
            result.append(_stylize_strip_range(strip, start, end, selection_style))
        return result


def _stylize_strip_range(strip, start: int, end: int, style) -> object:
    segments = list(strip)
    if not segments:
        return strip
    from textual.strip import Strip
    sel_rich = getattr(style, "rich_style", style)
    output = []
    x = 0
    for text, seg_style, control in segments:
        seg_end = x + len(text)
        if seg_end <= start or x >= end:
            output.append(Segment(text, seg_style, control))
        else:
            sel_start = max(start, x)
            sel_end = min(end, seg_end)
            if sel_start > x:
                output.append(Segment(text[: sel_start - x], seg_style, control))
            if sel_end > sel_start:
                merged = (seg_style or Style()) + sel_rich
                output.append(Segment(text[sel_start - x : sel_end - x], merged, control))
            if sel_end < seg_end:
                output.append(Segment(text[sel_end - x :], seg_style, control))
        x = seg_end
    return Strip(output, strip.cell_length)

class HistoryMarkdown(Static):

    def __init__(self, text=""):
        super().__init__("", markup=False)
        self.add_class("assistant-msg")
        self._source = text or ""
        self._md = None
        self._visual = None
        if text:
            self._md = _build_rich_md(text)

    def update(self, markdown=""):
        from textual.await_complete import AwaitComplete
        if markdown:
            self._source = markdown
            try:
                self._md = _build_rich_md(markdown)
            except Exception:
                self._md = None
            self._visual = None
        self.refresh(layout=True)
        return AwaitComplete.nothing()

    def set_content(self, text):
        return self.update(text or "")

    def render(self):
        if self._md is None:
            if self._source:
                try:
                    return self._source
                except Exception:
                    return ""
            return ""
        if self._visual is None:
            self._visual = _SelectableRichVisual(self, self._md)
        return self._visual

    def get_selection(self, selection) -> tuple[str, str] | None:
        try:
            from textual.visual import Visual as _V
            from textual.geometry import Region
            if self._md is None or self.size.height <= 0:
                return None
            v = self._render()
            strips = _V.to_strips(self, v, self.size.width, None, self.visual_style)
            text = "\n".join(strip.text for strip in strips)
            return selection.extract(text), "\n"
        except Exception:
            return None

    def append(self, markdown):
        # 历史消息只读，不支持流式追加
        return self.update(self._source)

    def refresh_theme(self):
        """主题切换后清理渲染缓存并重绘"""
        self._visual = None
        self._md = None
        if self._source:
            try:
                self._md = _build_rich_md(self._source)
            except Exception:
                self._md = None
        self.refresh(layout=False)


class AssistantMessage(HistoryMarkdown):

    def append(self, markdown):
        # 流式追加：以完整当前文本重建渲染
        return self.update(markdown)


class ThinkingBlock(Static):
    """思考块 — 流式展示 + 思考中 spinner

    - 思考中：spinner + "正在思考"
    - 内容到达：实时显示 reasoning
    - 思考结束：停止动画
    """

    def __init__(self, text=""):
        super().__init__("")
        self.add_class("thinking")
        self._buf = text
        self._frame = 0
        self._timer = None

    def on_mount(self):
        self._start_clock()
        self._refresh()

    def on_unmount(self):
        self._stop_clock()

    def _start_clock(self):
        if self._timer is None:
            self._timer = self.set_interval(1 / 12, self._tick)

    def _stop_clock(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self):
        self._frame += 1
        if not self._buf:
            self._refresh()

    def append_text(self, text):
        self._buf += text
        self._refresh()

    def finish(self):
        """思考结束：停止动画，定格最终内容"""
        self._stop_clock()
        self._refresh()

    def _display_text(self):
        """JSON thinking 只显示 reasoning 字段，避免暴露原始协议格式"""
        text = self._buf.strip()
        if not text:
            return ""
        if text.startswith("{"):
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("reasoning"):
                    return self._flatten(str(data["reasoning"]))
            except (json.JSONDecodeError, ValueError):
                pass
        return self._flatten(text)

    @staticmethod
    def _flatten(text):
        return " ".join(line.strip() for line in text.splitlines())

    def _refresh(self):
        display = self._display_text()
        from rich.text import Text
        tr = self.app.tr if self.app else (lambda k, *a: k)
        label = tr("thinking")
        if display:
            t = Text(f"{label}: ", style="bold")
            t.append(display)
            self.update(t)
        else:
            spinner = FRAMES[self._frame % len(FRAMES)]
            t = Text(f"{label} {spinner} ", style="bold")
            t.append(tr("thinking.in_progress"))
            self.update(t)


_hunk_re = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class ToolCallCard(Static):
    def __init__(self):
        super().__init__("")
        self.add_class("tool-card", "pending")
        self._calls = {}   # call_id -> call dict
        self._order = []   # 保持调用顺序
        self._frame = 0
        self._timer = None
        self._done = False
        self._expanded_diffs = set()
        self._foldable = set()
        self._folded_rows = []
        self._folded_index = 0
        self._diff_colors_cache = None

    def on_mount(self):
        self._start_clock()

    def on_resize(self):
        self._diff_colors_cache = None
        self.update(self._build(done=self._done))

    def refresh_theme(self):
        self._diff_colors_cache = None
        self.update(self._build(done=self._done))

    def _start_clock(self):
        if self._timer is None and not self._done:
            self._timer = self.set_interval(1 / 15, self._tick)

    def _stop_clock(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def add_call(self, call_id, tool, description="", arguments=None):
        if call_id in self._calls:
            return
        self._calls[call_id] = {
            "tool": tool,
            "description": description or "",
            "arguments": arguments or {},
            "status": "pending",   # pending / success / error
            "result": None,
            "duration": None,
        }
        self._order.append(call_id)
        self._start_clock()
        self._tick()

    def update_call(self, call_id, result, duration_ms):
        call = self._calls.get(call_id)
        if not call:
            return
        ok = not (isinstance(result, dict) and (result.get("error") or result.get("success") is False))
        call["status"] = "success" if ok else "error"
        call["result"] = result
        call["duration"] = duration_ms or 0
        self._tick()

    def finish(self):
        self._stop_clock()
        self._done = True
        self._frame = 0
        self.update(self._build(done=True))
        self.remove_class("pending")

    def _tick(self):
        self._frame += 1
        if not self._done:
            self.update(self._build(done=False))

    def _preview_args(self, arguments, prefix_len=0):
        if not arguments:
            return ""
        try:
            if isinstance(arguments, dict):
                text = json.dumps(arguments, ensure_ascii=False, default=str)
            else:
                text = str(arguments)
        except Exception:
            text = str(arguments)
        text = " ".join(text.split())
        width = self.size.width if self.size else 0
        max_len = max(10, width - prefix_len - 1)
        if len(text) > max_len:
            return text[:max_len] + "…"
        return text

    def _pad_row(self, t, bg):
        try:
            from rich.cells import cell_len
            width = self.size.width if self.size else 0
            if width <= 0:
                return
            lines = t.split()
            cur = lines[-1].cell_len if lines else 0
            fill = width - cur
            if fill > 0:
                t.append(" " * fill, style=f"on {bg}")
        except Exception:
            pass

    def _build(self, done):
        from rich.text import Text
        tr = self.app.tr if self.app else (lambda k, *a: k)
        total = len(self._order)
        ok = sum(1 for c in self._calls.values() if c["status"] == "success")
        t = Text()
        self._folded_rows = []
        diff_colors = self._diff_colors()
        code_bg = diff_colors[0]
        # 摘要行
        if done:
            if ok == total:
                t.append(tr("tool.finished", total), style="bold green")
            else:
                t.append(tr("tool.finished_partial", ok, total - ok), style="bold")
        else:
            t.append(tr("tool.running_n", total), style="bold cyan")
            t.append("…")
        # 每个工具一行
        for cid in self._order:
            c = self._calls[cid]
            diff = None
            if c["status"] == "success" and c["tool"] == "edit":
                result = c["result"]
                if isinstance(result, dict):
                    diff = result.get("diff")
            t.append("\n")
            if diff:
                dot = "green" if c["status"] == "success" else "red"
                t.append("● ", style=f"{dot} on {code_bg}")
                name = c["tool"]
                if len(name) > 30:
                    name = name[:30] + "…"
                t.append(name, style=f"bold on {code_bg}")
                path = (c["arguments"] or {}).get("file_path") or ""
                if path:
                    import os as _os
                    abs_p = _os.path.abspath(str(path)).replace("\\", "/")
                    t.append(f"  {self._ellipsize_path(abs_p)}", style=f"dim on {code_bg}")
                self._pad_row(t, code_bg)
            else:
                if c["status"] == "pending":
                    style = "dim" if self._frame % 2 == 0 else "bold dark_cyan"
                    t.append("● ", style=style)
                elif c["status"] == "success":
                    t.append("● ", style="green")
                else:
                    t.append("● ", style="red")
                name = c["tool"]
                if len(name) > 30:
                    name = name[:30] + "…"
                t.append(name, style="bold")
                if c["tool"] == "edit":
                    path = (c["arguments"] or {}).get("file_path") or ""
                    if path:
                        t.append(f"  {self._rel_path(path)}", style="dim")
                elif c["tool"] != "shell":
                    prefix_len = 3 + len(name)
                    args = self._preview_args(c["arguments"], prefix_len)
                    if args:
                        t.append(f"  {args}", style="dim")
                if c["tool"] == "shell":
                    self._append_shell(t, cid, c, code_bg, diff_colors)
            if diff:
                self._append_diff(t, cid, c, diff, diff_colors)
        return t

    def _rel_path(self, path: str) -> str:
        try:
            import os
            cwd = getattr(self.app, "cwd", None) or os.getcwd()
            rp = os.path.relpath(str(path), cwd)
            if not rp.startswith(".."):
                return rp.replace("\\", "/")
        except Exception:
            pass
        return os.path.abspath(str(path)).replace("\\", "/")

    @staticmethod
    def _ellipsize_path(path: str, max_len: int = 40) -> str:
        if len(path) <= max_len:
            return path
        if max_len <= 4:
            return path[:max_len]
        keep = max_len - 3
        return f"...{path[-keep:]}"

    def _diff_colors(self):
        try:
            theme_name = str(getattr(self.app, "theme", ""))
        except Exception:
            theme_name = ""
        if self._diff_colors_cache is not None and self._diff_colors_cache[0] == theme_name:
            return self._diff_colors_cache[1]
        dark = theme_name.endswith("dark")
        if dark:
            code_bg = "#0d1117"
            add_bg, del_bg = "#123f24", "#40161a"
            add_fg, del_fg = "#7ee787", "#ff7b72"
        else:
            code_bg = "#f6f8fa"
            add_bg, del_bg = "#d9f2e2", "#fadfe0"
            add_fg, del_fg = "#1a7f37", "#cf222e"
        colors = (code_bg, add_bg, del_bg, add_fg, del_fg)
        self._diff_colors_cache = (theme_name, colors)
        return colors

    def _append_shell(self, t, cid, c, code_bg, colors):
        _code_bg, add_bg, _del_bg, add_fg, _del_fg = colors
        args = c["arguments"] or {}
        cmd = args.get("command") or ""
        if not cmd and isinstance(args.get("argv"), list):
            cmd = " ".join(str(a) for a in args["argv"])
        result = c["result"] if isinstance(c["result"], dict) else {}
        output = result.get("output") or ""
        exit_code = result.get("exit_code")
        elapsed = result.get("elapsed_ms") or c.get("duration") or 0
        t.append("\n")
        if cmd:
            t.append(f"    $ {cmd}", style=f"bold on {code_bg}")
        if output:
            lines = output.rstrip("\n").split("\n")
            self._foldable.add(cid)
            folded = len(lines) > 10 and cid not in self._expanded_diffs
            if folded:
                idx = len(self._folded_rows)
                self._folded_rows.append((len(t.split()), cid))
                for line in lines[:5]:
                    t.append("\n")
                    t.append(f"      {line}", style=f"dim on {code_bg}")
                t.append("\n")
                tr = self.app.tr if self.app else (lambda k, *a: k.format(*a) if a else k)
                t.append(f"      {tr('tool.expand', len(lines))}", style=f"dim on {code_bg}")
            else:
                for line in lines:
                    t.append("\n")
                    t.append(f"      {line}", style=f"dim on {code_bg}")
        t.append("\n")
        if exit_code == 0:
            t.append("    shell completed", style=f"bold green on {code_bg}")
        elif exit_code is not None:
            t.append(f"    shell failed (exit {exit_code})", style=f"bold red on {code_bg}")
        else:
            t.append("    shell completed", style=f"bold on {code_bg}")
        if elapsed:
            t.append(f" · {int(elapsed)}ms", style=f"dim on {code_bg}")

    def _append_diff(self, t, cid, c, diff, colors):
        code_bg, add_bg, del_bg, add_fg, del_fg = colors
        path = self._rel_path((c["arguments"] or {}).get("file_path") or "")
        old_ln = new_ln = 0
        rows = []
        for raw in diff.splitlines():
            if raw.startswith("+++") or raw.startswith("---"):
                continue
            m = _hunk_re.match(raw)
            if m:
                old_ln = int(m.group(1))
                new_ln = int(m.group(2))
                rows.append(("@@", None, None, raw))
                continue
            if raw.startswith("+"):
                rows.append(("+", None, new_ln, raw[1:]))
                new_ln += 1
            elif raw.startswith("-"):
                rows.append(("-", old_ln, None, raw[1:]))
                old_ln += 1
            else:
                rows.append((" ", old_ln, new_ln, raw[1:] if raw.startswith(" ") else raw))
                old_ln += 1
                new_ln += 1
        adds = sum(1 for k, *_ in rows if k == "+")
        dels = sum(1 for k, *_ in rows if k == "-")
        self._foldable.add(cid)
        folded = len(rows) > 20 and cid not in self._expanded_diffs
        max_ln = max((o or 0) for _, o, n, _ in rows + [(" ", new_ln, new_ln, "")])
        w = max(1, len(str(max_ln)))
        import os as _os
        fname = _os.path.basename((c["arguments"] or {}).get("file_path") or "") or path
        if folded:
            t.append("\n")
            t.append(f"  # Edited {fname}", style=f"dim on {code_bg}")
            for kind, ol, nl, content in rows[:5]:
                t.append("\n")
                t.append(f"    {content}", style=f"dim on {code_bg}")
            t.append("\n")
            idx = len(self._folded_rows)
            self._folded_rows.append((len(t.split()), cid))
            tr = self.app.tr if self.app else (lambda k, *a: k.format(*a) if a else k)
            t.append(f"    {tr('tool.expand', len(rows))}", style=f"dim on {code_bg}")
            return
        t.append("\n")
        t.append(f"  # Edited {fname}", style=f"dim on {code_bg}")
        sep = "  "
        if adds:
            t.append(f"{sep}+{adds}", style=f"bold {add_fg} on {code_bg}")
            sep = " "
        if dels:
            t.append(f"{sep}−{dels}", style=f"bold {del_fg} on {code_bg}")
        for kind, ol, nl, content in rows:
            t.append("\n")
            if kind == "@@":
                t.append(f"    {content}", style=f"bold cyan on {code_bg}")
                continue
            lno = f"{ol:>{w}}" if ol is not None else " " * w
            rno = f"{nl:>{w}}" if nl is not None else " " * w
            if kind == "+":
                t.append(f"    {rno} + {content}", style=f"{add_fg} on {add_bg}")
            elif kind == "-":
                t.append(f"    {lno} − {content}", style=f"{del_fg} on {del_bg}")
            else:
                t.append(f"    {rno}   {content}", style=f"dim on {code_bg}")
        t.append("\n")
        t.append(" ", style=f"on {code_bg}")

    def can_focus(self):
        return bool(self._folded_rows)

    def on_key(self, event):
        if not self._foldable:
            return
        if event.key == "ctrl+e":
            self._toggle_all_folds()
            event.stop()

    def on_click(self, event):
        if not self._folded_rows:
            return
        best = None
        best_d = 10
        for i, (y, cid) in enumerate(self._folded_rows):
            d = abs(y - event.y)
            if d < best_d:
                best_d, best = d, (i, cid)
        if best is not None and best_d <= 1:
            self._folded_index = best[0]
            self._expand_diff(best[1])
            event.stop()

    def _expand_diff(self, cid):
        if cid in self._expanded_diffs:
            self._expanded_diffs.discard(cid)
        else:
            self._expanded_diffs.add(cid)
        self._folded_index = 0
        self.update(self._build(done=self._done))
        try:
            if hasattr(self.app, "input"):
                self.app.input.focus()
        except Exception:
            pass

    def _toggle_all_folds(self):
        if not self._foldable:
            return
        any_folded = any(cid not in self._expanded_diffs for cid in self._foldable)
        if any_folded:
            self._expanded_diffs.update(self._foldable)
        else:
            self._expanded_diffs.difference_update(self._foldable)
        self._folded_index = 0
        self.update(self._build(done=self._done))
        try:
            if hasattr(self.app, "input"):
                self.app.input.focus()
        except Exception:
            pass


class StatusBar(Static):
    """底部状态栏"""

class CollapsibleStatic(Static):

    def __init__(self, count: int, message: str, on_expand=None):
        super().__init__(message, markup=False)
        self.add_class("system-msg")
        self._count = count
        self._on_expand = on_expand

    def can_focus(self):
        return True

    def on_key(self, event):
        if event.key == "enter" and self._on_expand:
            self._on_expand()
            event.stop()

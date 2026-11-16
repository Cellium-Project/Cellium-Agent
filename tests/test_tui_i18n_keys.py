# -*- coding: utf-8 -*-
"""TUI 里 tr("...") 引用的 key 必须在三种语言表里都存在"""
import pathlib
import re

from app.tui.i18n import STRINGS

TUI_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "tui"
KEY_RE = re.compile(r'tr\(\s*["\']([a-zA-Z][a-zA-Z0-9_.]+)["\']')


def _used_keys():
    keys = set()
    for f in TUI_DIR.glob("*.py"):
        keys.update(KEY_RE.findall(f.read_text(encoding="utf-8")))
    # 拼接出来的前缀（如 tr("sidebar." + x)）不参与校验
    return {k for k in keys if not k.endswith(".")}


def test_all_tr_keys_defined():
    keys = _used_keys()
    assert keys
    for lang in STRINGS:
        missing = sorted(k for k in keys if k not in STRINGS[lang])
        assert not missing, f"{lang} 缺少 i18n key: {missing}"


def test_languages_have_same_keys():
    base = set(STRINGS["zh-CN"])
    for lang, table in STRINGS.items():
        diff = base ^ set(table)
        assert not diff, f"{lang} 与 zh-CN 的 key 不一致: {sorted(diff)}"

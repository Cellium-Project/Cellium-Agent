# -*- coding: utf-8 -*-
from typing import List, Dict

COMMANDS: List[Dict] = [
    {"slash": "help",    "desc_key": "cmd.desc.help",    "aliases": ["h"]},
    {"slash": "clear",   "desc_key": "cmd.desc.clear",   "aliases": []},
    {"slash": "theme",   "desc_key": "cmd.desc.theme",   "aliases": []},
    {"slash": "models",  "desc_key": "cmd.desc.models",  "aliases": ["model"]},
    {"slash": "session", "desc_key": "cmd.desc.session", "aliases": []},
    {"slash": "delete",  "desc_key": "cmd.desc.delete",  "aliases": ["rm"]},
    {"slash": "new",     "desc_key": "cmd.desc.new",     "aliases": ["n"]},
    {"slash": "settings","desc_key": "cmd.desc.settings","aliases": ["s"]},
    {"slash": "exit",    "desc_key": "cmd.desc.exit",    "aliases": ["quit", "q"]},
]


def fuzzy_score(query: str, text: str) -> int:
    query = (query or "").lower().strip()
    text = (text or "").lower()
    if not query:
        return 0
    if text.startswith(query):
        return 1000 - len(text)
    ti = 0
    for ch in query:
        idx = text.find(ch, ti)
        if idx == -1:
            return 0
        ti = idx + 1
    return 500 - ti

def match_commands(query: str, limit: int = 10) -> List[Dict]:
    if not query:
        return [c for c in COMMANDS[:limit]]
    scored = []
    for cmd in COMMANDS:
        best = fuzzy_score(query, cmd["slash"])
        for alias in cmd.get("aliases", []):
            s = fuzzy_score(query, alias)
            if s > best:
                best = s
        if best > 0:
            scored.append((best, cmd))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]

# -*- coding: utf-8 -*-
import multiprocessing
import sys

_context = None


def get_process_context():
    global _context
    if _context is None:
        if sys.platform == "win32":
            _context = multiprocessing.get_context("spawn")
        else:
            try:
                _context = multiprocessing.get_context("forkserver")
            except ValueError:
                _context = multiprocessing.get_context("fork")
    return _context

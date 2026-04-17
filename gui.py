"""
Legacy Tkinter GUI compatibility entrypoint.

The maintained implementation now lives in `legacy/gui.py`.
"""

from __future__ import annotations

import sys

from legacy import gui as _legacy_gui

sys.modules[__name__] = _legacy_gui

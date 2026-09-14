"""Every module must import cleanly.

A cheap guard with real value: the UI package has no unit tests of its own
(Streamlit calls need a script context), so a broken import there would
otherwise only surface when someone loads the page. That is exactly how
`charts.py` shipped importing HistoryResponse from the wrong module.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import marketpulse

MODULES = sorted(
    m.name
    for m in pkgutil.walk_packages(marketpulse.__path__, prefix="marketpulse.")
    if not m.ispkg
)


def test_the_walk_found_modules():
    assert len(MODULES) > 15, f"expected the whole package, found {MODULES}"


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)

"""Guards on the files that make the project installable.

These exist because the encoding bug they check for shipped, was declared
fixed, and survived three commits. The original `requirements.txt` was
UTF-16LE, so `pip install -r requirements.txt` failed outright. The
"fix" overwrote the file through a path that preserved its encoding, and
the verification asserted `data.decode("ascii")` — which SUCCEEDS on
UTF-16LE-encoded ASCII text, because NUL is a valid ASCII codepoint.

So the check here is for NUL bytes specifically, not for decodability.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
PYPROJECT = ROOT / "pyproject.toml"


def _direct_dependency_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = set()
    for spec in data["project"]["dependencies"]:
        name = spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


@pytest.mark.parametrize("path", [REQUIREMENTS, PYPROJECT])
def test_packaging_files_contain_no_nul_bytes(path: Path):
    """A UTF-16 requirements file breaks `pip install -r` on Linux and macOS.

    Checking for NUL is the assertion that actually distinguishes UTF-8 from
    UTF-16 here; `.decode("ascii")` does not.
    """
    data = path.read_bytes()
    assert data.count(0) == 0, f"{path.name} contains NUL bytes — it is not UTF-8"


@pytest.mark.parametrize("path", [REQUIREMENTS, PYPROJECT])
def test_packaging_files_have_no_byte_order_mark(path: Path):
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name} has a UTF-8 BOM"
    assert not data.startswith(b"\xff\xfe"), f"{path.name} has a UTF-16 LE BOM"
    assert not data.startswith(b"\xfe\xff"), f"{path.name} has a UTF-16 BE BOM"


def test_requirements_decodes_as_utf8_and_is_parseable():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    requirements = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert requirements, "requirements.txt has no requirement lines"
    for line in requirements:
        assert "==" in line or ">=" in line, f"unparseable requirement line: {line!r}"


def test_requirements_covers_every_direct_dependency():
    """Catches the other half of the bug: requirements.txt silently drifting
    behind pyproject.toml as dependencies are added."""
    text = REQUIREMENTS.read_text(encoding="utf-8").lower()
    pinned = {
        line.split("==")[0].strip()
        for line in text.splitlines()
        if "==" in line and not line.startswith(" ")
    }
    missing = _direct_dependency_names() - pinned
    assert not missing, f"in pyproject.toml but not requirements.txt: {sorted(missing)}"


def test_the_retired_google_sdk_is_gone():
    """google-generativeai is end-of-life; the project moved to google-genai."""
    for path in (REQUIREMENTS, PYPROJECT):
        assert "google-generativeai" not in path.read_text(encoding="utf-8")

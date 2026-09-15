#!/usr/bin/env python
"""Reject files that are not UTF-8, by checking for NUL bytes.

    python scripts/check_encoding.py requirements.txt pyproject.toml

Exists because `requirements.txt` shipped as UTF-16LE, so
`pip install -r requirements.txt` failed outright — and because the fix for
it was verified with `data.decode("ascii")`, which **succeeds** on
UTF-16-encoded ASCII text: NUL is a valid ASCII codepoint. The broken file
then survived three more commits.

Checking for NUL is the assertion that actually distinguishes UTF-8 from
UTF-16 here. Decodability does not.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BOMS = {
    b"\xef\xbb\xbf": "UTF-8 BOM",
    b"\xff\xfe": "UTF-16 LE BOM",
    b"\xfe\xff": "UTF-16 BE BOM",
}


def check(path: Path) -> str | None:
    """Return a problem description, or None when the file is fine."""
    if not path.exists():
        return None
    data = path.read_bytes()

    nul_count = data.count(0)
    if nul_count:
        return f"{nul_count} NUL byte(s) — this is UTF-16 or binary, not UTF-8. pip cannot read it."
    for bom, label in _BOMS.items():
        if data.startswith(bom):
            return f"starts with a {label}"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"is not valid UTF-8: {exc}"
    return None


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [Path("requirements.txt"), Path("pyproject.toml")]
    failures = 0
    for path in paths:
        problem = check(path)
        if problem:
            print(f"{path}: {problem}")
            failures += 1
    if failures:
        print(f"\n{failures} file(s) rejected.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

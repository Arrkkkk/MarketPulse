"""Cache value serialization.

Deliberately avoids `pickle`. A cache is a deserialization surface, and
unpickling is arbitrary code execution — even for a local SQLite file, it is
the kind of choice that is hard to defend later. Instead each value carries a
one-byte tag saying how to read it back:

    b"J"  JSON          dicts, lists, scalars
    b"P"  Parquet       pandas DataFrame (preserves dtypes and the index)

Anything else raises rather than silently degrading, so an unsupported type
fails at the write, where the stack trace still points at the caller.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

_TAG_JSON = b"J"
_TAG_PARQUET = b"P"


class SerdeError(TypeError):
    """A value could not be serialized for, or restored from, the cache."""


def serialize(value: Any) -> bytes:
    """Encode a cache value to bytes."""
    if isinstance(value, pd.DataFrame):
        buf = io.BytesIO()
        # index=True so a DatetimeIndex survives the round trip.
        value.to_parquet(buf, index=True)
        return _TAG_PARQUET + buf.getvalue()
    try:
        return _TAG_JSON + json.dumps(value, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SerdeError(f"cannot serialize {type(value).__name__} for the cache") from exc


def deserialize(blob: bytes) -> Any:
    """Decode bytes produced by `serialize`."""
    if not blob:
        raise SerdeError("empty cache payload")
    tag, payload = blob[:1], blob[1:]
    if tag == _TAG_PARQUET:
        return pd.read_parquet(io.BytesIO(payload))
    if tag == _TAG_JSON:
        return json.loads(payload.decode("utf-8"))
    raise SerdeError(f"unknown cache payload tag {tag!r}")

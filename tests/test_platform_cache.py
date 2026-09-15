"""Cache: key stability, tiering, TTL, and the no-caching-failures rule."""

from __future__ import annotations

import time

import pandas as pd
import pytest

from marketpulse.platform.cache import CACHE_VERSION, Cache, make_key
from marketpulse.platform.serde import SerdeError, deserialize, serialize
from tests.fakes import make_frame

# --- key generation --------------------------------------------------------


def test_key_is_independent_of_kwarg_order():
    assert make_key("h", symbol="AAPL", period="1y") == make_key("h", period="1y", symbol="AAPL")


def test_different_params_give_different_keys():
    assert make_key("h", symbol="AAPL") != make_key("h", symbol="MSFT")


def test_long_keys_collapse_to_a_digest_but_stay_unique():
    a = make_key("h", q="x" * 500)
    b = make_key("h", q="y" * 500)
    assert len(a) < 300 and a != b


# --- serde -----------------------------------------------------------------


def test_dataframe_round_trips_with_index_and_dtypes():
    frame = make_frame(5)
    restored = deserialize(serialize(frame))
    # check_freq=False: parquet does not carry DatetimeIndex.freq, which only
    # exists here because the fixture builds its index with pd.date_range.
    # The real provider path goes through pd.to_datetime().sort_index() and
    # has freq=None on both sides, so nothing downstream can observe this.
    pd.testing.assert_frame_equal(frame, restored, check_freq=False)
    assert list(frame.index) == list(restored.index)


@pytest.mark.parametrize("value", [{"a": 1}, [1, 2, 3], "text", 42, 3.14, True])
def test_json_values_round_trip(value):
    assert deserialize(serialize(value)) == value


def test_unknown_payload_tag_raises():
    with pytest.raises(SerdeError):
        deserialize(b"Zgarbage")


def test_serde_never_uses_pickle():
    """A cache is a deserialization surface; pickle there is code execution."""
    import marketpulse.platform.serde as serde_mod

    source = open(serde_mod.__file__).read()
    assert "import pickle" not in source
    assert "pickle.loads" not in source


# --- cache behaviour -------------------------------------------------------


def test_miss_then_hit(cache: Cache):
    assert cache.get("k") is None
    cache.set("k", {"v": 1}, ttl=60)
    assert cache.get("k") == {"v": 1}


def test_entries_expire(cache: Cache):
    cache.set("k", {"v": 1}, ttl=0.05)
    assert cache.get("k") == {"v": 1}
    time.sleep(0.06)
    assert cache.get("k") is None


def test_disk_tier_survives_a_new_cache_instance(tmp_path):
    path = tmp_path / "c.sqlite3"
    Cache(db_path=path).set("k", {"v": 1}, ttl=60)
    assert Cache(db_path=path).get("k") == {"v": 1}, "SQLite tier did not persist"


def test_memory_tier_is_backfilled_from_disk(tmp_path):
    path = tmp_path / "c.sqlite3"
    Cache(db_path=path).set("k", {"v": 1}, ttl=60)

    fresh = Cache(db_path=path)
    assert fresh.get("k") == {"v": 1}  # disk hit, back-fills memory
    assert fresh._memory.get(f"{CACHE_VERSION}:k") is not None


def test_hit_rate_tracks_hits_and_misses(cache: Cache):
    cache.get("nope")
    cache.set("k", 1, ttl=60)
    cache.get("k")
    assert cache.stats() == {"hits": 1, "misses": 1, "hit_rate": 0.5}


def test_version_prefix_invalidates_old_entries(cache: Cache, monkeypatch):
    cache.set("k", {"v": 1}, ttl=60)
    monkeypatch.setattr("marketpulse.platform.cache.CACHE_VERSION", "v2")
    assert cache.get("k") is None, "a version bump should invalidate old entries"


# --- get_or_set: the important one ----------------------------------------


def test_get_or_set_calls_the_producer_only_on_miss(cache: Cache):
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"v": 1}

    assert cache.get_or_set("k", 60, producer) == {"v": 1}
    assert cache.get_or_set("k", 60, producer) == {"v": 1}
    assert calls["n"] == 1


def test_get_or_set_does_not_cache_a_failure(cache: Cache):
    """A thirty-second outage must not become a twelve-hour one."""

    class Boom(Exception):
        pass

    def failing():
        raise Boom()

    with pytest.raises(Boom):
        cache.get_or_set("k", 60, failing)

    assert cache.get("k") is None, "a failure was written to the cache"

    # And the next call must reach the producer, not serve a cached failure.
    assert cache.get_or_set("k", 60, lambda: {"v": "recovered"}) == {"v": "recovered"}

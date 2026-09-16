import hashlib
import json
import time

import pytest

from tradingagents.research.cache import EvidenceCache


def test_roundtrip_and_caller_mutation_is_detached(tmp_path):
    cache = EvidenceCache(tmp_path / "evidence")
    payload = {"ticker": "OUST", "values": [1, {"ok": True}]}
    digest = cache.put_json("provider/OUST?window=1", payload)
    payload["values"][1]["ok"] = False

    assert digest == hashlib.sha256(
        json.dumps(payload | {"values": [1, {"ok": True}]}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    result = cache.get_json("provider/OUST?window=1")
    assert result == {"ticker": "OUST", "values": [1, {"ok": True}]}
    result["values"].append(2)
    assert cache.get_json("provider/OUST?window=1") == {"ticker": "OUST", "values": [1, {"ok": True}]}


def test_snapshots_are_immutable_and_keys_are_distinct(tmp_path):
    cache = EvidenceCache(tmp_path / "evidence")
    first = cache.put_json("same", {"version": 1})
    second = cache.put_json("same", {"version": 2})
    assert first != second
    assert cache.get_json("same") == {"version": 2}
    assert json.loads((tmp_path / "evidence" / "snapshots" / f"{first}.json").read_text()) == {"version": 1}
    cache.put_json("other", {"version": 2})
    assert cache.get_json("same") == cache.get_json("other")


def test_expiry_uses_index_timestamp_without_sleep(tmp_path):
    cache = EvidenceCache(tmp_path / "evidence")
    cache.put_json("old", [1, 2])
    index_path = next((tmp_path / "evidence" / "index").glob("*.json"))
    index = json.loads(index_path.read_text())
    index["wrote_at"] = time.time() - 10
    index_path.write_text(json.dumps(index))
    assert cache.get_json("old", max_age_seconds=5) is None
    assert cache.get_json("old", max_age_seconds=20) == [1, 2]


def test_corrupt_cache_is_a_miss(tmp_path):
    cache = EvidenceCache(tmp_path / "evidence")
    cache.put_json("bad", {"x": 1})
    index_path = next((tmp_path / "evidence" / "index").glob("*.json"))
    index_path.write_text("not json")
    assert cache.get_json("bad") is None


def test_put_recovers_a_corrupted_existing_snapshot(tmp_path):
    cache = EvidenceCache(tmp_path / "evidence")
    digest = cache.put_json("recover", {"x": 1})
    snapshot_path = tmp_path / "evidence" / "snapshots" / f"{digest}.json"
    snapshot_path.write_text("corrupted")
    assert cache.get_json("recover") is None
    assert cache.put_json("recover", {"x": 1}) == digest
    assert cache.get_json("recover") == {"x": 1}


def test_malicious_key_stays_inside_root_and_nan_is_rejected(tmp_path):
    root = tmp_path / "evidence"
    cache = EvidenceCache(root)
    cache.put_json("../../outside/../../secret", {"safe": True})
    assert cache.get_json("../../outside/../../secret") == {"safe": True}
    assert not (tmp_path / "secret").exists()
    with pytest.raises(ValueError):
        cache.put_json("nan", {"value": float("nan")})

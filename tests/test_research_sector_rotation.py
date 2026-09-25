from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import tradingagents.research.sector_rotation as sector_rotation
from tradingagents.research.sector_rotation import SECTORS, build_sector_rotation


def histories(weeks: int = 31, latest: date = date(2026, 8, 7)) -> tuple[dict[str, pd.DataFrame], list[date]]:
    dates = [latest - timedelta(weeks=weeks - 1 - index) for index in range(weeks)]
    index = pd.DatetimeIndex(dates)
    benchmark = pd.DataFrame({"Close": np.full(weeks, 100.0)}, index=index)
    # At the latest week the sector's 13-week return is 13%; four weeks ago
    # its 13-week return was 10%, so the change is 3 percentage points.
    values = np.full(weeks, 100.0)
    values[-5] = 110.0
    values[-1] = 113.0
    sector = pd.DataFrame({"Close": values}, index=index)
    return {"SPY": benchmark, "XLB": sector}, dates


def build(data: dict[str, pd.DataFrame], as_of: date = date(2026, 8, 10)) -> dict:
    return build_sector_rotation(
        data,
        as_of=f"{as_of.isoformat()}T23:59:59Z",
        retrieved_at="2026-08-11T12:00:00Z",
    )


def test_known_coordinates_trail_and_replay_warning() -> None:
    payload = build(histories()[0])
    point = next(item for item in payload["points"] if item["symbol"] == "XLB")
    assert point["x"] == pytest.approx(13.0)
    assert point["y"] == pytest.approx(3.0)
    assert point["absolute_return_13w"] == pytest.approx(13.0)
    assert point["relative_return_13w"] == pytest.approx(point["x"])
    assert point["relative_change_4w"] == pytest.approx(point["y"])
    assert len(point["trail"]) == 8
    assert payload["benchmark"] == "SPY"
    assert any("Replay limitation" in warning for warning in payload["warnings"])
    json.dumps(payload, allow_nan=False)


def test_excludes_incomplete_current_week_and_future_bars() -> None:
    data, dates = histories()
    for symbol, frame in data.items():
        extra = pd.DataFrame(
            {"Close": [999.0, 2000.0]},
            index=pd.to_datetime([dates[-1] + timedelta(days=3), dates[-1] + timedelta(days=11)]),
        )
        data[symbol] = pd.concat([frame, extra])
    payload = build(data)
    point = next(item for item in payload["points"] if item["symbol"] == "XLB")
    assert point["as_of"] == dates[-1].isoformat()
    assert point["x"] == pytest.approx(13.0)
    assert any("future daily bar" in warning for warning in payload["warnings"])


def test_missing_common_week_withholds_sector_without_compressing_time() -> None:
    data, dates = histories()
    missing_date = dates[-5]
    data["XLB"] = data["XLB"].drop(pd.Timestamp(missing_date))
    payload = build(data)
    assert {item["symbol"]: item["reason"] for item in payload["excluded"]}["XLB"] == "missing_required_weekly_observation"
    assert all(item["symbol"] != "XLB" for item in payload["points"])


def test_stale_latest_week_is_withheld() -> None:
    data, dates = histories()
    data["XLB"] = data["XLB"].drop(pd.Timestamp(dates[-1]))
    payload = build(data)
    assert {item["symbol"]: item["reason"] for item in payload["excluded"]}["XLB"] == "stale_latest_week"


def test_duplicate_and_nonpositive_values_are_diagnosed() -> None:
    data, dates = histories()
    duplicate = data["XLB"].iloc[[-1]].copy()
    data["XLB"] = pd.concat([data["XLB"], duplicate])
    data["XLC"] = data["XLB"].iloc[:-1].copy()
    data["XLC"].iloc[0, 0] = 0.0
    payload = build(data)
    assert {item["symbol"]: item["reason"] for item in payload["excluded"]}["XLB"] == "invalid_or_duplicate_history"
    assert any("duplicate daily date" in warning for warning in payload["warnings"])
    assert any("nonfinite/nonpositive" in warning for warning in payload["warnings"])
    assert not any(item["symbol"] == "XLB" for item in payload["points"])


def test_boolean_close_is_rejected() -> None:
    data, dates = histories()
    data["XLB"]["Close"] = data["XLB"]["Close"].astype(object)
    data["XLB"].loc[pd.Timestamp(dates[-1]), "Close"] = True
    payload = build(data)
    assert {item["symbol"]: item["reason"] for item in payload["excluded"]}["XLB"] == "stale_latest_week"
    assert any("malformed or nonfinite/nonpositive" in warning for warning in payload["warnings"])


def test_weekend_daily_bars_are_ignored_with_diagnostic() -> None:
    data, dates = histories()
    sunday = dates[-1] + timedelta(days=2)
    for symbol, frame in data.items():
        data[symbol] = pd.concat([frame, pd.DataFrame({"Close": [1_000.0]}, index=[pd.Timestamp(sunday)])])
    payload = build(data)
    assert any("ignored 1 weekend daily bar" in warning for warning in payload["warnings"])


def test_stale_benchmark_cannot_be_relabelled_as_current() -> None:
    data, dates = histories()
    data["SPY"] = data["SPY"].drop(pd.Timestamp(dates[-1]))
    with pytest.raises(ValueError, match="benchmark SPY is stale"):
        build(data)


def test_missing_sector_histories_are_listed_without_zero_filling() -> None:
    payload = build({"SPY": histories()[0]["SPY"]})
    excluded = {item["symbol"]: item["reason"] for item in payload["excluded"]}
    assert len(excluded) == len(SECTORS)
    assert excluded["XLY"] == "missing_history"
    assert payload["points"] == []


def test_benchmark_requires_timezone_aware_cutoff_and_complete_history() -> None:
    data, _ = histories()
    with pytest.raises(ValueError, match="timezone-aware"):
        build_sector_rotation(data, as_of="2026-08-10", retrieved_at="2026-08-11T12:00:00Z")
    with pytest.raises(ValueError, match="insufficient complete weekly history"):
        build_sector_rotation(
            {"SPY": data["SPY"].iloc[:10]},
            as_of="2026-08-10T23:59:59Z",
            retrieved_at="2026-08-11T12:00:00Z",
        )


def test_cli_default_cutoff_is_captured_with_post_download_retrieval_time(monkeypatch, tmp_path) -> None:
    now_date = datetime.now(timezone.utc).date()
    days_since_friday = (now_date.weekday() - 4) % 7 or 7
    expected_week = now_date - timedelta(days=days_since_friday)
    data, _ = histories(latest=expected_week)
    captured: dict = {}
    cache_paths: list = []
    monkeypatch.setattr(sector_rotation, "_download_histories", lambda cache_dir: (cache_paths.append(cache_dir) or data))
    monkeypatch.setattr(sector_rotation, "_atomic_json", lambda _path, payload: captured.update(payload))

    assert sector_rotation.main(["--output", str(tmp_path / "sector-rotation.json")]) == 0
    assert captured["as_of"] == captured["retrieved_at"]
    assert cache_paths == [tmp_path / "cache" / "yfinance"]

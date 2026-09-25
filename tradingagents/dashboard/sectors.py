"""Read a precomputed sector snapshot without implicit network refreshes."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Missing snapshot timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Snapshot timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def load_sector_snapshot(path: Path | None, *, now: datetime | None = None) -> dict:
    """Invalid/missing snapshots become visible data gaps, never fabricated points."""
    empty = {"status": "unavailable", "points": [], "excluded": [], "warnings": []}
    if path is None or not path.is_file():
        return {**empty, "warnings": ["No sector snapshot collected. Run the sector refresh command."]}
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError("Sector snapshot exceeds 2 MB")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Sector snapshot must be an object")
        as_of = _timestamp(raw.get("as_of"))
        retrieved = _timestamp(raw.get("retrieved_at"))
        instant = now or datetime.now(timezone.utc)
        if as_of > instant + timedelta(minutes=5) or retrieved > instant + timedelta(minutes=5):
            raise ValueError("Sector snapshot timestamp is in the future")
        if not isinstance(raw.get("points"), list) or len(raw["points"]) > 11:
            raise ValueError("Invalid sector point list")
        from tradingagents.research.sector_rotation import SECTORS

        points = []
        seen = set()
        for point in raw["points"]:
            if not isinstance(point, dict) or point.get("symbol") not in SECTORS:
                raise ValueError("Unknown sector point")
            if point["symbol"] in seen:
                raise ValueError("Duplicate sector point")
            seen.add(point["symbol"])
            for key in ("x", "y", "absolute_return_13w", "relative_return_13w", "relative_change_4w"):
                if not _number(point.get(key)):
                    raise ValueError("Sector coordinates must be finite numbers")
            point_date = datetime.fromisoformat(point["as_of"]).date()
            if point_date > as_of.date():
                raise ValueError("Sector point exceeds snapshot cutoff")
            trail = point.get("trail")
            if not isinstance(trail, list) or not 1 <= len(trail) <= 8:
                raise ValueError("Invalid sector trail")
            dates = []
            for item in trail:
                if not isinstance(item, dict) or not _number(item.get("x")) or not _number(item.get("y")):
                    raise ValueError("Invalid trail coordinate")
                dates.append(datetime.fromisoformat(item["date"]).date())
            if dates != sorted(set(dates)) or dates[-1] != point_date:
                raise ValueError("Sector trail dates are not aligned")
            points.append({
                "symbol": point["symbol"], "sector": SECTORS[point["symbol"]],
                **{key: point[key] for key in (
                    "x", "y", "absolute_return_13w", "relative_return_13w",
                    "relative_change_4w", "as_of",
                )},
                "trail": [{"date": item["date"], "x": item["x"], "y": item["y"]} for item in trail],
            })
        warnings = [str(w)[:1000] for w in raw.get("warnings", [])][:30]
        latest = max((datetime.fromisoformat(p["as_of"]).date() for p in points), default=None)
        stale = (instant - retrieved > timedelta(days=8)
                 or latest is not None and (instant.date() - latest).days > 10)
        if stale:
            warnings.append("Stored sector snapshot is stale; refresh before interpreting current conditions.")
        return {
            "status": "stale" if stale else "available" if points else "unavailable",
            "as_of": raw["as_of"], "retrieved_at": raw["retrieved_at"],
            "benchmark": str(raw.get("benchmark", "SPY"))[:40],
            "methodology": {str(k)[:100]: str(v)[:1000] for k, v in raw.get("methodology", {}).items()
                            if isinstance(v, str)} if isinstance(raw.get("methodology"), dict) else {},
            "source": str(raw.get("source", "Yahoo Finance"))[:200],
            "points": points,
            "excluded": [{"symbol": str(e.get("symbol", ""))[:20],
                          "reason": str(e.get("reason", "unknown"))[:400]}
                         for e in raw.get("excluded", []) if isinstance(e, dict)][:20],
            "warnings": warnings,
        }
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return {**empty, "warnings": [f"Sector snapshot withheld: {type(exc).__name__}: {exc}"]}

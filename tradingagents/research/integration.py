"""Load a dated evidence packet without silently fetching replacement data."""

import hashlib
import json
from datetime import datetime, time, timezone
from pathlib import Path

from .models import ResearchPacket


def _cutoff(value: str) -> datetime:
    if len(value) == 10:
        return datetime.combine(datetime.fromisoformat(value).date(), time.max, timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Research timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_packet(path: str | Path, ticker: str, as_of: str) -> tuple[ResearchPacket, str]:
    """Validate identity/cutoff and hash exact bytes for checkpoint isolation."""
    raw = Path(path).read_bytes()
    packet = ResearchPacket.from_dict(json.loads(raw))
    if packet.ticker.upper() != ticker.strip().upper():
        raise ValueError("Research packet ticker does not match requested instrument")
    if _cutoff(packet.as_of) != _cutoff(str(as_of)):
        raise ValueError("Research packet cutoff does not match requested analysis date")
    return packet, hashlib.sha256(raw).hexdigest()

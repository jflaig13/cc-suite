# SPDX-License-Identifier: MPL-2.0
"""Evidence validators for the M3 parity-window diversity controls."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


CHICAGO = ZoneInfo("America/Chicago")


def chicago_midnight_reset_proven(evidence: dict[str, Any]) -> bool:
    required = {"before_ts", "after_ts", "before_daily_count", "after_daily_count"}
    if set(evidence) != required:
        raise ValueError("midnight evidence has unexpected fields")
    before = datetime.fromisoformat(str(evidence["before_ts"]))
    after = datetime.fromisoformat(str(evidence["after_ts"]))
    if before.tzinfo is None or after.tzinfo is None:
        raise ValueError("midnight evidence timestamps must be timezone-aware")
    before_count = int(evidence["before_daily_count"])
    after_count = int(evidence["after_daily_count"])
    if before_count < 1 or after_count < 0:
        raise ValueError("daily counts are outside the valid range")
    return (
        after > before
        and after.astimezone(CHICAGO).date() > before.astimezone(CHICAGO).date()
        and after_count <= 1
    )


def quota_park_resume_proven(*, parked: bool, resumed: bool) -> bool:
    return parked is True and resumed is True

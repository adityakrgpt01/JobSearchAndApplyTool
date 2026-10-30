#!/usr/bin/env python3
"""
Job Age Filter Utility.
Enforces the 7-day age threshold rule:
Do not apply to job openings older than 7 days.
"""
import re
from datetime import datetime, timezone

def get_job_age_days(posted_at, discovered_at=None) -> int:
    """
    Returns the age of a job posting in days.
    Calculates exact elapsed age using compute_job_exact_age_hours.
    """
    try:
        from database import compute_job_exact_age_hours
        hours = compute_job_exact_age_hours(posted_at, discovered_at)
        if hours < 999900:
            return max(0, int(hours // 24))
    except Exception:
        pass

    if not posted_at:
        if discovered_at:
            try:
                dt = datetime.fromisoformat(str(discovered_at).split('.')[0].replace('Z', ''))
                now = datetime.now()
                return max(0, (now - dt).days)
            except Exception:
                pass
        return 0

    p = str(posted_at).strip().lower()
    if "today" in p or "just posted" in p or "hours ago" in p or "minute" in p:
        return 0
    if "yesterday" in p:
        return 1
    m = re.search(r"posted\s+(\d+)\s+days?\s+ago", p)
    if m:
        return int(m.group(1))
    if "30+" in p:
        return 35

    # Try standard date string formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%B %d, %Y"
    ]:
        try:
            cleaned = str(posted_at).strip()
            if cleaned.endswith("Z") and not fmt.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            dt = datetime.strptime(cleaned, fmt)
            now = datetime.now(timezone.utc) if dt.tzinfo else datetime.now()
            return max(0, (now - dt).days)
        except Exception:
            pass

    return 0

def is_within_7_days(posted_at, discovered_at=None, max_days: int = 7) -> bool:
    """Returns True if the job was posted within max_days (default 7 days)."""
    return get_job_age_days(posted_at, discovered_at) <= max_days

if __name__ == "__main__":
    test_cases = [
        ("Posted Today", 0),
        ("Posted Yesterday", 1),
        ("Posted 4 Days Ago", 4),
        ("Posted 10 Days Ago", 10),
        ("Posted 30+ Days Ago", 35),
        ("2026-10-26T08:21:30.416Z", 1),
        ("2026-10-15T00:00:00+00:00", 12),
        ("October 25, 2026", 2)
    ]
    for text, expected in test_cases:
        age = get_job_age_days(text)
        within = is_within_7_days(text)
        print(f"'{text}' -> age={age}d, within_7_days={within}")

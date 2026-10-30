#!/usr/bin/env python3
"""
db_normalize_posted_dates.py
Normalizes all relative and missing posted_at timestamps in jobs.db (job_postings table)
into standardized ISO datetimes (YYYY-MM-DD HH:MM:SS) calculated against discovered_at.
"""

import sqlite3
import shutil
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

DB_PATH = "jobs.db"
BACKUP_PATH = "jobs.db.backup"

def parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s_clean = str(s).strip()
    # Try ISO formats
    iso_clean = s_clean.replace("T", " ").replace("Z", "").split("+")[0].split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso_clean, fmt)
        except ValueError:
            pass
    return None

def normalize_posted_at(posted_at: Optional[str], discovered_at: Optional[str]) -> str:
    # 1. Base reference time: discovered_at or current UTC
    base_dt = parse_iso_dt(discovered_at) or datetime.now(timezone.utc).replace(tzinfo=None)

    if not posted_at or str(posted_at).strip().lower() in ("", "none", "null"):
        # Default to discovered_at if posted_at is null or empty
        return base_dt.strftime("%Y-%m-%d %H:%M:%S")

    p = str(posted_at).strip()
    p_lower = p.lower()

    # 2. Check if already ISO
    iso_dt = parse_iso_dt(p)
    if iso_dt:
        return iso_dt.strftime("%Y-%m-%d %H:%M:%S")

    # 3. Check for format like 'October 13, 2026' or 'Oct 13, 2026'
    for mfmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            parsed = datetime.strptime(p, mfmt)
            return parsed.strftime("%Y-%m-%d 00:00:00")
        except ValueError:
            pass

    # 4. Relative string matching
    if any(w in p_lower for w in ["just now", "minute"]):
        return base_dt.strftime("%Y-%m-%d %H:%M:%S")

    m_hr = re.search(r"(\d+)\s+hour", p_lower)
    if m_hr:
        hrs = int(m_hr.group(1))
        return (base_dt - timedelta(hours=hrs)).strftime("%Y-%m-%d %H:%M:%S")

    if "today" in p_lower:
        # Posted earlier on the day of discovery (~4 hours prior)
        return (base_dt - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")

    if "yesterday" in p_lower or "1 day ago" in p_lower:
        return (base_dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    if "2 days ago" in p_lower:
        return (base_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")

    m_days = re.search(r"(\d+)\s*\+?\s*days?\s*ago", p_lower)
    if m_days:
        days = int(m_days.group(1))
        return (base_dt - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    if "30+ days" in p_lower:
        return (base_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    m_weeks = re.search(r"(\d+)\s*\+?\s*weeks?\s*ago", p_lower)
    if m_weeks:
        wks = int(m_weeks.group(1))
        return (base_dt - timedelta(weeks=wks)).strftime("%Y-%m-%d %H:%M:%S")

    if "week" in p_lower:
        return (base_dt - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    m_months = re.search(r"(\d+)\s*\+?\s*months?\s*ago", p_lower)
    if m_months:
        months = int(m_months.group(1))
        return (base_dt - timedelta(days=months * 30)).strftime("%Y-%m-%d %H:%M:%S")

    if "month" in p_lower:
        return (base_dt - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    # Fallback: keep discovered_at
    return base_dt.strftime("%Y-%m-%d %H:%M:%S")

def run_normalization():
    print(f"Creating backup of {DB_PATH} to {BACKUP_PATH}...")
    shutil.copyfile(DB_PATH, BACKUP_PATH)
    print("Backup created successfully.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT id, posted_at, discovered_at FROM job_postings")
    rows = cursor.fetchall()
    total_jobs = len(rows)
    print(f"Found {total_jobs} total records in job_postings.")

    updates = []
    already_iso_count = 0
    relative_count = 0
    null_count = 0

    for jid, p_at, d_at in rows:
        norm_dt_str = normalize_posted_at(p_at, d_at)
        if not p_at or str(p_at).strip().lower() in ("", "none", "null"):
            null_count += 1
            updates.append((norm_dt_str, jid))
        elif str(p_at).strip() == norm_dt_str:
            already_iso_count += 1
        elif re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", str(p_at).strip()):
            # Already valid ISO format, standardize representation
            already_iso_count += 1
            if str(p_at).strip() != norm_dt_str:
                updates.append((norm_dt_str, jid))
        else:
            relative_count += 1
            updates.append((norm_dt_str, jid))

    print(f"Summary of changes needed:")
    print(f"  - Already valid ISO: {already_iso_count}")
    print(f"  - Relative strings to normalize: {relative_count}")
    print(f"  - NULL/empty dates to populate from discovered_at: {null_count}")
    print(f"  - Total updates to execute: {len(updates)}")

    if updates:
        cursor.executemany("UPDATE job_postings SET posted_at = ? WHERE id = ?", updates)
        conn.commit()
        print("Database updated and committed successfully.")
    else:
        print("No updates needed.")

    conn.close()

if __name__ == "__main__":
    run_normalization()

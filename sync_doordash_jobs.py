#!/usr/bin/env python3
"""
DoorDash Job Postings Synchronizer & Database Auditor.
1. Connects to DoorDash Greenhouse APIs:
   - doordashindia (https://boards-api.greenhouse.io/v1/boards/doordashindia/jobs)
   - doordashusa (https://boards-api.greenhouse.io/v1/boards/doordashusa/jobs)
2. Filters strictly for qualified India Software Engineering / Data Engineering roles.
3. Audits all existing DoorDash records in jobs.db:
   - Marks closed / removed postings as EXPIRED.
   - Marks out-of-scope roles (Sales, Managers, Support, etc.) as INVALID.
   - Saves new qualified engineering openings as DISCOVERED for immediate application.
"""

import asyncio
import json
import sqlite3
import re
import os
import urllib.request
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from database import get_db_connection, DB_PATH
from experience_filter import is_qualified_job, is_qualified_location


def fetch_live_doordash_jobs() -> List[Dict[str, Any]]:
    """Fetches all live DoorDash jobs from Greenhouse boards."""
    all_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    boards = ["doordashindia", "doordashusa"]
    for b in boards:
        url = f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                jobs = data.get("jobs", [])
                print(f"📊 Fetched {len(jobs)} total jobs from DoorDash Greenhouse board '{b}'")
                for j in jobs:
                    j["board"] = b
                    all_jobs.append(j)
        except Exception as e:
            print(f"⚠️ Error fetching from DoorDash board '{b}': {e}")

    # Deduplicate by job ID
    unique = {}
    for j in all_jobs:
        jid = str(j.get("id"))
        if jid not in unique:
            unique[jid] = j
    return list(unique.values())


def sync_and_audit_doordash_database(live_postings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Audits existing DoorDash records and synchronizes with live Greenhouse postings."""
    if live_postings is None:
        live_postings = fetch_live_doordash_jobs()

    conn = get_db_connection()
    c = conn.cursor()

    print("\n🔍 Step 1: Auditing Existing DoorDash Records in jobs.db...")
    c.execute("""
        SELECT id, job_id, title, location, ats_platform, apply_url, status, screenshot_path, applied_at, apply_log
        FROM job_postings
        WHERE LOWER(company_name) LIKE '%doordash%' OR apply_url LIKE '%doordash%'
    """)
    existing_records = [dict(zip([col[0] for col in c.description], row)) for row in c.fetchall()]
    print(f"Total existing DoorDash records in DB: {len(existing_records)}")

    # Index live postings by string job ID
    live_by_id: Dict[str, Dict[str, Any]] = {str(p.get("id")): p for p in live_postings if p.get("id")}

    counts = {
        "applied": 0,
        "expired": 0,
        "invalid": 0,
        "discovered": 0,
        "new_inserted": 0
    }

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Audit existing
    for r in existing_records:
        rec_id = r["id"]
        job_id = r["job_id"]
        title = r["title"] or ""
        loc = r["location"] or ""
        platform = r["ats_platform"] or ""
        apply_url = r["apply_url"] or ""
        curr_status = r["status"]
        screenshot = r["screenshot_path"]

        # Preserve APPLIED
        if curr_status == 'APPLIED' and screenshot and os.path.exists(screenshot):
            counts["applied"] += 1
            continue

        raw_id = job_id.replace("gh_doordash_", "").replace("gh_", "")
        if platform == "greenhouse" and raw_id not in live_by_id:
            c.execute("""
                UPDATE job_postings
                SET status = 'EXPIRED',
                    apply_log = 'Requisition no longer active on DoorDash Greenhouse portal'
                WHERE id = ?
            """, (rec_id,))
            counts["expired"] += 1
            continue

        if not is_qualified_job(title, loc):
            c.execute("""
                UPDATE job_postings
                SET status = 'INVALID',
                    apply_log = 'Role out of qualification scope (Non-SWE / Management / Staff+)'
                WHERE id = ?
            """, (rec_id,))
            counts["invalid"] += 1
            continue

        counts["discovered"] += 1

    # Step 2: Insert new live postings
    print("\n✨ Step 2: Ingesting New Live Openings from DoorDash Greenhouse...")
    existing_ids = {str(r.get("job_id")).replace("gh_doordash_", "").replace("gh_", "") for r in existing_records}
    existing_urls = {r["apply_url"] for r in existing_records if r.get("apply_url")}

    for p in live_postings:
        pid = str(p.get("id"))
        title = p.get("title", "").strip()
        loc_obj = p.get("location") or {}
        loc = loc_obj.get("name") if isinstance(loc_obj, dict) else str(loc_obj)
        board = p.get("board", "doordashindia")
        abs_url = p.get("absolute_url") or f"https://job-boards.greenhouse.io/{board}/jobs/{pid}"

        if pid in existing_ids or abs_url in existing_urls:
            continue

        # Check location (DoorDash India or Remote with India applicability)
        is_india = "india" in loc.lower() or "hyderabad" in loc.lower() or "pune" in loc.lower() or "bengaluru" in loc.lower() or "bangalore" in loc.lower() or board == "doordashindia"
        if not is_india:
            continue

        qualified = is_qualified_job(title, loc)
        status = "DISCOVERED" if qualified else "INVALID"
        apply_log = "Discovered via DoorDash Greenhouse sync" if qualified else "Role out of qualification scope"

        job_db_id = f"gh_doordash_{pid}"
        c.execute("""
            INSERT OR IGNORE INTO job_postings (
                job_id, company_name, title, location, ats_platform, apply_url,
                status, discovered_at, apply_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_db_id,
            "DoorDash",
            title,
            loc,
            "greenhouse",
            abs_url,
            status,
            now_iso,
            apply_log
        ))
        if c.rowcount > 0:
            counts["new_inserted"] += 1
            if qualified:
                counts["discovered"] += 1
            else:
                counts["invalid"] += 1

    conn.commit()
    conn.close()

    print("\n📊 DoorDash Synchronization Summary:")
    print(f"   • Active Applied:   {counts['applied']}")
    print(f"   • Expired/Closed:   {counts['expired']}")
    print(f"   • Out-of-Scope:     {counts['invalid']}")
    print(f"   • Newly Ingested:   {counts['new_inserted']}")
    print(f"   • Open & Ready:     {counts['discovered']}")
    print("=" * 60)
    return counts


if __name__ == "__main__":
    sync_and_audit_doordash_database()

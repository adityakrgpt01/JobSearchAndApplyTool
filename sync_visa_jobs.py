#!/usr/bin/env python3
"""
Visa Job Postings Synchronizer & Database Auditor.
1. Connects to Visa Workday CXS API via Playwright request context to discover all live postings for India.
2. Cross-references against candidate's active Workday applications.
3. Audits all existing Visa records in jobs.db:
   - Marks verified applied requisitions as APPLIED.
   - Marks closed / 404 postings as EXPIRED.
   - Marks out-of-scope roles (Directors, Managers, Legal, Non-SWE) as INVALID.
   - Saves any new qualified software engineering openings as DISCOVERED for immediate application.
"""

import asyncio
import json
import sqlite3
import re
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set

from playwright.async_api import async_playwright

from database import get_db_connection, DB_PATH
from experience_filter import is_qualified_job


def extract_req_id(text: Optional[str]) -> Optional[str]:
    """Extracts REFXXXXXXW requisition ID from URL or string."""
    if not text:
        return None
    m = re.search(r'REF\d+W(?:-\d+)?', text, re.IGNORECASE)
    return m.group(0).upper() if m else None


async def fetch_live_visa_india_jobs() -> List[Dict[str, Any]]:
    """Fetches all live India postings from Visa Workday CXS API using Playwright context."""
    all_postings = []
    url = "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/Visa/jobs"
    
    print("🌐 Fetching all live India postings from Visa Workday CXS API via Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        try:
            # Initialize session cookies
            await page.goto("https://visa.wd5.myworkdayjobs.com/en-US/Visa", timeout=25000)
            await asyncio.sleep(1.5)
            
            offset = 0
            limit = 20
            max_total = 9999
            
            while offset < max_total:
                payload = {
                    "appliedFacets": {},
                    "limit": limit,
                    "offset": offset,
                    "searchText": "India"
                }
                res = await page.request.post(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                if not res.ok:
                    print(f"⚠️ Error response from Visa CXS at offset {offset}: status {res.status}")
                    break
                data = await res.json()
                if offset == 0:
                    max_total = data.get("total", 0)
                    print(f"📊 Total reported live Visa postings matching India: {max_total}")
                
                postings = data.get("jobPostings", [])
                if not postings:
                    break
                all_postings.extend(postings)
                offset += len(postings)
        except Exception as e:
            print(f"⚠️ Exception during Visa CXS fetch: {e}")
        finally:
            await browser.close()
            
    print(f"✅ Successfully fetched {len(all_postings)} live Visa India postings.")
    return all_postings


def sync_and_audit_visa_database(live_postings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Audits existing Visa records and synchronizes with live Workday CXS postings."""
    if live_postings is None:
        live_postings = asyncio.run(fetch_live_visa_india_jobs())

    conn = get_db_connection()
    c = conn.cursor()

    print("\n🔍 Step 1: Auditing Existing Visa Records in jobs.db...")
    c.execute("""
        SELECT id, job_id, title, location, ats_platform, apply_url, status, screenshot_path, applied_at, apply_log
        FROM job_postings
        WHERE LOWER(company_name) LIKE '%visa%' OR apply_url LIKE '%visa%'
    """)
    existing_records = [dict(zip([col[0] for col in c.description], row)) for row in c.fetchall()]
    print(f"Total existing Visa records in DB: {len(existing_records)}")

    # Index live postings by req_id
    live_by_req: Dict[str, Dict[str, Any]] = {}
    for p in live_postings:
        ext_path = p.get("externalPath", "")
        req = extract_req_id(ext_path)
        if not req and p.get("bulletFields"):
            for bf in p["bulletFields"]:
                req = extract_req_id(bf)
                if req:
                    break
        if req:
            live_by_req[req] = p

    # Load portal applied applications if file exists
    portal_file = os.path.join(os.path.dirname(__file__), "all_visa_portal_applications.json")
    portal_applied_reqs = set()
    if os.path.exists(portal_file):
        try:
            with open(portal_file) as f:
                apps = json.load(f)
                for a in apps:
                    raw = ' '.join([a.get('title', ''), a.get('req_id', ''), a.get('status', '')])
                    req = extract_req_id(raw)
                    if req:
                        portal_applied_reqs.add(req)
        except Exception as e:
            print("Error loading Visa portal apps:", e)

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
        applied_at = r["applied_at"]

        req_id = extract_req_id(job_id) or extract_req_id(apply_url)

        # 1. Active application check
        if req_id and req_id in portal_applied_reqs:
            sc_path = screenshot or f"screenshots/visa_{job_id}.png"
            c.execute("""
                UPDATE job_postings
                SET status = 'APPLIED',
                    applied_at = ?,
                    screenshot_path = ?,
                    apply_log = ?
                WHERE id = ?
            """, (applied_at or now_iso, sc_path, f"Validated: Active application on Visa Workday (Req: {req_id})", rec_id))
            counts["applied"] += 1
            continue

        # If already applied with screenshot proof, preserve
        if curr_status == 'APPLIED' and screenshot and os.path.exists(screenshot):
            counts["applied"] += 1
            continue

        # 2. Check if expired / removed from live Workday
        if platform == 'workday' and req_id and req_id not in live_by_req:
            c.execute("""
                UPDATE job_postings
                SET status = 'EXPIRED',
                    apply_log = 'Requisition no longer active on Visa Workday portal'
                WHERE id = ?
            """, (rec_id,))
            counts["expired"] += 1
            continue

        # 3. Qualification check
        if not is_qualified_job(title, loc):
            c.execute("""
                UPDATE job_postings
                SET status = 'INVALID',
                    apply_log = 'Role out of qualification scope (Non-SWE / Management / Staff+)'
                WHERE id = ?
            """, (rec_id,))
            counts["invalid"] += 1
            continue

        # Otherwise DISCOVERED
        counts["discovered"] += 1

    # Step 2: Insert new live postings
    print("\n✨ Step 2: Ingesting New Live Openings from Visa Workday...")
    existing_urls = {r["apply_url"] for r in existing_records if r.get("apply_url")}
    existing_reqs = {extract_req_id(r.get("job_id")) for r in existing_records if extract_req_id(r.get("job_id"))}

    for p in live_postings:
        title = p.get("title", "").strip()
        ext_path = p.get("externalPath", "")
        loc = p.get("locationsText", "IN - Bengaluru, India")
        req_id = extract_req_id(ext_path)
        if not req_id and p.get("bulletFields"):
            for bf in p["bulletFields"]:
                req_id = extract_req_id(bf)
                if req_id:
                    break

        if not req_id:
            continue

        full_url = f"https://visa.wd5.myworkdayjobs.com/en-US/Visa{ext_path}"
        if full_url in existing_urls or req_id in existing_reqs:
            continue

        # Qualification filter
        qualified = is_qualified_job(title, loc)
        status = "DISCOVERED" if qualified else "INVALID"
        apply_log = "Discovered via Visa Workday CXS sync" if qualified else "Role out of qualification scope"

        job_db_id = f"wd_visa_{req_id}"
        c.execute("""
            INSERT OR IGNORE INTO job_postings (
                job_id, company_name, title, location, ats_platform, apply_url,
                status, discovered_at, apply_log
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_db_id,
            "Visa",
            title,
            loc,
            "workday",
            full_url,
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

    print("\n📊 Visa Synchronization Summary:")
    print(f"   • Active Applied:   {counts['applied']}")
    print(f"   • Expired/Closed:   {counts['expired']}")
    print(f"   • Out-of-Scope:     {counts['invalid']}")
    print(f"   • Newly Ingested:   {counts['new_inserted']}")
    print(f"   • Open & Ready:     {counts['discovered']}")
    print("=" * 60)
    return counts


if __name__ == "__main__":
    sync_and_audit_visa_database()

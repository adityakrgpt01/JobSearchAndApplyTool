#!/usr/bin/env python3
"""
DoorDash Batch Auto-Applier.
- Sequentially processes all pending qualified DoorDash openings.
- Captures pre-submission Review screenshot -> screenshots/doordash_{job_id}.png.
- Verifies post-submission confirmation banner -> screenshots/doordash_{job_id}_submitted.png.
- Updates jobs.db status strictly upon verified submission.
"""

import asyncio
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

from doordash_applier import DoorDashApplier, capture_clean_screenshot
from database import get_db_connection, update_job_status
from experience_filter import is_qualified_job
from job_age_filter import is_within_7_days, get_job_age_days


def get_pending_doordash_jobs(limit: Optional[int] = None, enforce_7_day_limit: bool = True, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if job_id:
        c.execute("""
            SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
            FROM job_postings
            WHERE job_id = ?
        """, (job_id,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    c.execute("""
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
        FROM job_postings
        WHERE (LOWER(company_name) LIKE '%doordash%' OR apply_url LIKE '%doordash%')
          AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')
        ORDER BY CASE WHEN apply_url LIKE '%greenhouse.io%' THEN 0 ELSE 1 END, id ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    qualified = [r for r in rows if is_qualified_job(r["title"], r.get("location", ""))]
    if enforce_7_day_limit:
        qualified = [r for r in qualified if is_within_7_days(r.get("posted_at"), r.get("discovered_at"))]
    if limit:
        return qualified[:limit]
    return qualified


async def process_doordash_job(
    applier: DoorDashApplier,
    job: Dict[str, Any],
    context: BrowserContext,
    idx: int,
    total: int
) -> bool:
    job_id = job["job_id"]
    title = job["title"]
    sc_path = f"screenshots/doordash_{job_id}.png"

    # Strict 7-day age filter for live applications
    age_days = get_job_age_days(job.get("posted_at"), job.get("discovered_at"))
    if not applier.dry_run and not is_within_7_days(job.get("posted_at"), job.get("discovered_at")):
        print(f"⚠️ Strict 7-day rule: Skipping live application for {job_id} ({age_days} days old > 7 days threshold).")
        return False

    print(f"\n{'='*75}")
    print(f"[{idx}/{total}] 🚪 Processing DoorDash Job: {title}")
    print(f"     Job ID:    {job_id}")
    print(f"     Apply URL: {job['apply_url']}")
    print(f"{'='*75}")

    page = await context.new_page()
    await applier.stealth.apply_stealth_async(page)

    try:
        success, msg = await applier.fill_form(page, job)
        print(f"📋 Form fill result: success={success}, msg={msg}")

        if success:
            if applier.dry_run:
                print(f"🏁 [{job_id}] Dry-run completed! Pre-submission Review proof: {sc_path}")
                return True

            update_job_status(
                job_id=job_id,
                status="APPLIED",
                apply_log=f"DoorDash auto-applied successfully. {msg}",
                screenshot_path=sc_path
            )
            print(f"✅ [{job_id}] Successfully APPLIED and recorded in jobs.db!")
            return True
        else:
            print(f"⚠️ [{job_id}] Did not complete application: {msg}")
            if any(k in msg.lower() for k in ["closed", "expired", "not available"]):
                update_job_status(job_id, "EXPIRED", msg, sc_path)
            else:
                update_job_status(job_id, "FAILED", msg, sc_path)
            return False
    except Exception as e:
        print(f"❌ [{job_id}] Exception during DoorDash apply: {e}")
        try:
            await capture_clean_screenshot(page, sc_path)
        except Exception:
            pass
        update_job_status(job_id, "FAILED", f"Unhandled error: {e}", sc_path)
        return False
    finally:
        await page.close()


async def run_batch_applier(limit: Optional[int] = None, headless: bool = True, dry_run: bool = False, job_id: Optional[str] = None):
    jobs = get_pending_doordash_jobs(limit=limit, job_id=job_id, enforce_7_day_limit=not dry_run if not job_id else False)
    total = len(jobs)
    print(f"\n🚀 Launching DoorDash Auto-Applier for {total} pending qualified jobs (headless={headless}, dry_run={dry_run})...")
    if not jobs:
        print("✅ No pending qualified DoorDash jobs found.")
        return

    applier = DoorDashApplier(headless=headless, dry_run=dry_run, keep_open=False)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )

        successful = 0
        failed = 0
        for idx, job in enumerate(jobs, 1):
            ok = await process_doordash_job(applier, job, context, idx, total)
            if ok:
                successful += 1
            else:
                failed += 1
            await asyncio.sleep(2.0)

        await browser.close()

    print(f"\n{'='*75}")
    print(f"🏁 DoorDash Batch Application Run Finished. Total: {total} | Successful: {successful} | Failed: {failed}")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DoorDash Auto-Applier Batch Runner")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to process")
    parser.add_argument("--job-id", type=str, default=None, help="Target a specific job ID")
    parser.add_argument("--dry-run", action="store_true", help="Fill form and park on Review step without submitting")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser")
    args = parser.parse_args()

    asyncio.run(run_batch_applier(
        limit=args.limit,
        job_id=args.job_id,
        headless=not args.headed,
        dry_run=args.dry_run
    ))

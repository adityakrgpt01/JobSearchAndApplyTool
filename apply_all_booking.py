#!/usr/bin/env python3
"""
Batch Runner for Booking Holdings Jobs.
Reads pending jobs from jobs.db, processes applications sequentially,
and records review / submission proofs.
"""
import os
import sys
import sqlite3
import logging
import argparse
from datetime import datetime
from booking_applier import apply_to_booking_job
from job_age_filter import is_within_7_days, get_job_age_days

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply_all_booking")

DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")
SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")

def get_pending_booking_jobs(limit=None, enforce_7_day_limit=True):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = """
        SELECT job_id, title, location, apply_url, status, posted_at, discovered_at
        FROM job_postings
        WHERE company_name = 'Booking Holdings'
          AND status IN ('DISCOVERED', 'NEW')
        ORDER BY id ASC
    """
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    if enforce_7_day_limit:
        rows = [r for r in rows if is_within_7_days(r[5], r[6])]
    if limit:
        rows = rows[:int(limit)]
    return rows

def update_job_status(job_id, status, screenshot_path=None, error_msg=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if status == "APPLIED":
        c.execute("""
            UPDATE job_postings
            SET status = 'APPLIED',
                applied_at = ?,
                screenshot_path = ?,
                apply_attempts = apply_attempts + 1,
                apply_log = 'Successfully applied and verified.'
            WHERE job_id = ?
        """, (now, screenshot_path, job_id))
    elif status == "FAILED":
        c.execute("""
            UPDATE job_postings
            SET status = 'FAILED',
                apply_attempts = apply_attempts + 1,
                apply_log = ?
            WHERE job_id = ?
        """, (error_msg or "Application error", job_id))
    conn.commit()
    conn.close()

def run_batch(dry_run=True, limit=None, headless=True):
    pending = get_pending_booking_jobs(limit=limit, enforce_7_day_limit=not dry_run)
    logger.info(f"Found {len(pending)} pending Booking Holdings jobs (dry_run={dry_run}, limit={limit})")
    
    success_count = 0
    fail_count = 0
    
    for idx, (job_id, title, location, apply_url, status, posted_at, discovered_at) in enumerate(pending, 1):
        age_days = get_job_age_days(posted_at, discovered_at)
        if not dry_run and not is_within_7_days(posted_at, discovered_at):
            logger.warning(f"⚠️ Strict 7-day rule: Skipping live application for {job_id} ({age_days}d > 7d)")
            continue

        logger.info(f"[{idx}/{len(pending)}] Processing {job_id}: {title} ({location})")
        ok = apply_to_booking_job(apply_url, job_id, dry_run=dry_run, headless=headless)
        
        if ok:
            success_count += 1
            if not dry_run:
                proof = os.path.join(SCREENSHOTS_DIR, f"booking_{job_id}_submitted.png")
                if os.path.exists(proof):
                    update_job_status(job_id, "APPLIED", screenshot_path=proof)
                    logger.info(f"Updated {job_id} to APPLIED in jobs.db")
                else:
                    update_job_status(job_id, "FAILED", error_msg="Submit executed but confirmation screenshot not found")
        else:
            fail_count += 1
            if not dry_run:
                update_job_status(job_id, "FAILED", error_msg="Error during application flow")
                
    logger.info(f"Batch run finished: {success_count} succeeded, {fail_count} failed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch apply Booking Holdings jobs")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Dry run review screenshots only")
    parser.add_argument("--live", action="store_true", help="Live apply and record in DB")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to process")
    parser.add_argument("--no-headless", action="store_true", help="Run with visible browser")
    
    args = parser.parse_args()
    is_dry_run = not args.live
    run_batch(dry_run=is_dry_run, limit=args.limit, headless=not args.no_headless)

run_batch_applier = run_batch

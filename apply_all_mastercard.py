#!/usr/bin/env python3
"""
Batch Auto-Applier for all qualified Mastercard Workday positions.
- Reuses a single authenticated BrowserContext across all jobs to bypass login/PIN gates.
- Sequentially processes all pending qualified Mastercard Workday requisitions.
- For every job:
  1. Fills all steps: Contact/Address (Bangalore, India), Work Experience (4 panels: 73 Strings, Sumo Logic, Open Financial Technologies, Exotel), Education (B.Tech, SRM/JSS), 22 Skills, Questionnaires (Gender: Male, Sponsorship: No, etc.), and Consent.
  2. Captures the full Review page screenshot to screenshots/mastercard_{job_id}.png.
  3. Clicks Submit on the Review step to finalize application.
  4. Updates jobs.db status to APPLIED with screenshot_path.
"""

import asyncio
import os
import sys
import re
import sqlite3
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

from mastercard_applier import (
    MastercardApplier,
    capture_proof,
    safe_page_content
)
from database import get_db_connection, update_job_status
from experience_filter import is_qualified_job
from sync_mastercard_jobs import PORTAL_APPLIED_REQS
from job_age_filter import is_within_7_days, get_job_age_days


def get_pending_workday_jobs(limit: int = None, enforce_7_day_limit: bool = True) -> List[Dict[str, Any]]:
    """Returns all qualified, unapplied Workday jobs for Mastercard."""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
        FROM job_postings
        WHERE apply_url LIKE '%mastercard.wd1.myworkdayjobs.com%'
          AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')
        ORDER BY id ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    qualified = [r for r in rows if is_qualified_job(r["title"], r.get("location", ""))]
    unique_jobs = []
    seen_reqs = set()

    for r in qualified:
        req_match = re.search(r'R-\d+', (r.get("apply_url") or "") + " " + (r.get("job_id") or ""))
        req_clean = req_match.group(0) if req_match else None
        if req_clean:
            if req_clean in PORTAL_APPLIED_REQS or any(req_clean.startswith(ar) or ar.startswith(req_clean) for ar in PORTAL_APPLIED_REQS):
                continue
            if req_clean in seen_reqs:
                continue
            seen_reqs.add(req_clean)
        unique_jobs.append(r)

    if enforce_7_day_limit:
        unique_jobs = [r for r in unique_jobs if is_within_7_days(r.get("posted_at"), r.get("discovered_at"))]

    if limit:
        return unique_jobs[:limit]
    return unique_jobs


async def process_job(
    applier: MastercardApplier,
    job: Dict[str, Any],
    context: BrowserContext,
    idx: int,
    total: int
) -> bool:
    """Processes a single Mastercard job to review, captures screenshot, and submits."""
    job_id = job["job_id"]
    title = job["title"]
    sc_path = f"screenshots/mastercard_{job_id}.png"

    # Strict 7-day age filter for live applications
    age_days = get_job_age_days(job.get("posted_at"), job.get("discovered_at"))
    if not applier.dry_run and not is_within_7_days(job.get("posted_at"), job.get("discovered_at")):
        print(f"⚠️ Strict 7-day rule: Skipping live application for {job_id} ({age_days} days old > 7 days threshold).")
        return False

    print(f"\n{'='*75}")
    print(f"[{idx}/{total}] 💳 Processing: {title}")
    print(f"     Job ID:    {job_id}")
    print(f"     Apply URL: {job['apply_url']}")
    print(f"{'='*75}")

    page = await context.new_page()
    await applier.stealth.apply_stealth_async(page)

    try:
        success, msg = await applier.fill_workday_form(page, job)
        print(f"📋 Form fill result: success={success}, msg={msg}")

        # Ensure we capture the final review screenshot if not already captured
        if not os.path.exists(sc_path):
            await capture_proof(page, sc_path)
            print(f"📸 Saved final Review screenshot -> {sc_path}")

        if success:
            if "already applied" in msg.lower():
                update_job_status(
                    job_id=job_id,
                    status="APPLIED",
                    apply_log="Confirmed already applied on Workday portal",
                    screenshot_path=sc_path
                )
                print(f"✅ [{job_id}] Already applied on Workday. Marked APPLIED.")
                return True

            if "submitted" in msg.lower() or "received" in msg.lower():
                update_job_status(
                    job_id=job_id,
                    status="APPLIED",
                    apply_log=f"Auto-applied successfully. {msg}",
                    screenshot_path=sc_path
                )
                print(f"✅ [{job_id}] Confirmed submitted! Successfully APPLIED and recorded in jobs.db!")
                return True

            if applier.dry_run:
                print(f"🏁 [{job_id}] Dry-run completed successfully! Form filled and Review proof captured: {sc_path}")
                return True

            # Locate and click the final Submit button on Review step if still on review page
            submit_btn = await page.query_selector(
                "button[data-automation-id='pageFooterNextButton']:has-text('Submit'), "
                "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
                "button[data-automation-id='pageFooterSubmitButton'], "
                "button:has-text('Submit Application'), button:has-text('Submit')"
            )
            if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
                print("🚀 Clicking 'Submit' button on Review page...")
                await submit_btn.click(force=True)
                await asyncio.sleep(6.0)

                sub_sc = f"screenshots/mastercard_{job_id}_submitted.png"
                try:
                    await capture_proof(page, sub_sc)
                except Exception:
                    pass

                content = (await safe_page_content(page)).lower()
                confirmed = any(k in content for k in ["congratulations", "thank you", "application submitted", "received", "submitted"])
                if confirmed:
                    update_job_status(
                        job_id=job_id,
                        status="APPLIED",
                        apply_log=f"Auto-applied successfully. Confirmed submitted.",
                        screenshot_path=sub_sc if os.path.exists(sub_sc) else sc_path
                    )
                    print(f"✅ [{job_id}] Confirmed submitted! Successfully APPLIED and recorded in jobs.db!")
                    return True
                else:
                    update_job_status(job_id, "FAILED", "Submit clicked but confirmation text not detected in DOM", sc_path)
                    print(f"⚠️ [{job_id}] Submit clicked but confirmation not detected in DOM. Marked FAILED to avoid false positive.")
                    return False
            else:
                update_job_status(job_id, "FAILED", "Review page reached but Submit button was not clickable", sc_path)
                return False
        else:
            print(f"⚠️ [{job_id}] Did not reach final review cleanly: {msg}")
            if any(k in msg.lower() for k in ["expired", "removed", "doesn't exist", "does not exist", "no longer available"]):
                update_job_status(job_id, "EXPIRED", f"Requisition closed on Workday: {msg}", sc_path)
            elif any(k in msg.lower() for k in ["maintenance", "retry", "busy", "timed out", "timeout"]):
                # Keep as DISCOVERED so cron retries when portal is available
                print(f"⏳ [{job_id}] Temporary condition ({msg}). Keeping DISCOVERED for next run.")
            else:
                update_job_status(job_id, "FAILED", f"Error during auto-apply: {msg}", sc_path)
            return False

    except Exception as e:
        print(f"❌ Exception applying to {job_id}: {e}")
        try:
            await capture_proof(page, sc_path)
            update_job_status(job_id, "FAILED", f"Exception: {e}", sc_path)
        except Exception:
            pass
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def run_batch_applier(limit: Optional[int] = None, headless: bool = True, job_id: Optional[str] = None, dry_run: bool = False):
    """Batch applies to all pending qualified Mastercard Workday jobs."""
    if job_id:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM job_postings WHERE job_id = ? OR id = ?", (job_id, job_id))
        row = c.fetchone()
        conn.close()
        if not row:
            print(f"❌ Job ID '{job_id}' not found.")
            return
        pending_jobs = [dict(row)]
    else:
        pending_jobs = get_pending_workday_jobs(limit=limit)

    print(f"\n🚀 Found {len(pending_jobs)} pending qualified Mastercard Workday jobs to auto-apply. (Dry-run: {dry_run})")
    if not pending_jobs:
        print("✨ All qualified Mastercard Workday jobs are already APPLIED!")
        return

    applier = MastercardApplier(
        dry_run=dry_run,
        headless=headless,
        keep_open=False
    )

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-breakpad"
        ]
    )
    context = await browser.new_context(
        viewport={"width": 1366, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    try:
        # Pre-authenticate once to initialize session cookies
        print("🔑 Checking / initializing Mastercard Workday session...")
        auth_page = await context.new_page()
        await applier.stealth.apply_stealth_async(auth_page)
        await applier.ensure_logged_in(auth_page, "mastercard.wd1.myworkdayjobs.com")
        await auth_page.close()
        print("✅ Session established! Starting sequential application loop...\n")

        applied_count = 0
        failed_count = 0

        for idx, job in enumerate(pending_jobs, 1):
            success = await process_job(applier, job, context, idx, len(pending_jobs))
            if success:
                applied_count += 1
            else:
                failed_count += 1
            await asyncio.sleep(2.0)

        print(f"\n{'='*75}")
        print(f"🎉 Mastercard Auto-Apply Batch Run Completed!")
        print(f"   Total Processed: {len(pending_jobs)}")
        print(f"   Successfully Applied: {applied_count}")
        print(f"   Failed / Expired:     {failed_count}")
        print(f"{'='*75}\n")
    finally:
        await browser.close()
        await p.stop()


async def main_async():
    parser = argparse.ArgumentParser(description="Mastercard Batch Auto-Applier")
    parser.add_argument("--job-id", type=str, default=None, help="Process a specific job ID")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to process (default: all)")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless")
    parser.add_argument("--headed", action="store_false", dest="headless", help="Run headed")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Fill form up to Review and capture proof without submitting")
    args = parser.parse_args()

    await run_batch_applier(limit=args.limit, headless=args.headless, job_id=args.job_id, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main_async())

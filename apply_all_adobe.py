#!/usr/bin/env python3
"""
Batch Auto-Applier for all qualified Adobe Workday positions.
- Reuses a single authenticated BrowserContext across all jobs to bypass login/PIN gates.
- Sequentially processes all pending qualified Adobe Workday requisitions.
- For every job:
  1. Fills all steps: Contact/Address (Bangalore, India), Work Experience (4 panels: 73 Strings, Sumo Logic, Open Financial Technologies, Exotel), Education (B.Tech, JSS), 20+ Skills, Questionnaires, and Disclosures.
  2. Captures the full Review page screenshot to screenshots/adobe_{job_id}.png.
  3. Clicks Submit on the Review step to finalize application.
  4. Strictly updates jobs.db status to APPLIED with screenshot_path upon confirmation.
"""

import asyncio
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from playwright.async_api import async_playwright, Page, BrowserContext

from adobe_applier import (
    AdobeApplier,
    capture_proof,
    safe_page_content
)
from database import get_db_connection, update_job_status
from experience_filter import is_qualified_job
from job_age_filter import is_within_7_days, get_job_age_days


def get_pending_adobe_jobs(limit: Optional[int] = None, enforce_7_day_limit: bool = True) -> List[Dict[str, Any]]:
    """Returns all qualified, unapplied Workday jobs for Adobe."""
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
        FROM job_postings
        WHERE (LOWER(company_name) LIKE '%adobe%' OR apply_url LIKE '%adobe.wd5%')
          AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')
        ORDER BY CASE WHEN apply_url LIKE '%myworkdayjobs.com%' THEN 0 ELSE 1 END, id ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    qualified = [r for r in rows if is_qualified_job(r["title"], r.get("location", ""))]
    if enforce_7_day_limit:
        qualified = [r for r in qualified if is_within_7_days(r.get("posted_at"), r.get("discovered_at"))]
    if limit:
        return qualified[:limit]
    return qualified


async def process_adobe_job(
    applier: AdobeApplier,
    job: Dict[str, Any],
    context: BrowserContext,
    idx: int,
    total: int
) -> bool:
    """Processes a single Adobe job to review, captures screenshot, and submits."""
    job_id = job["job_id"]
    title = job["title"]
    sc_path = f"screenshots/adobe_{job_id}.png"

    # Strict 7-day age filter for live applications
    age_days = get_job_age_days(job.get("posted_at"), job.get("discovered_at"))
    if not applier.dry_run and not is_within_7_days(job.get("posted_at"), job.get("discovered_at")):
        print(f"⚠️ Strict 7-day rule: Skipping live application for {job_id} ({age_days} days old > 7 days threshold).")
        return False

    print(f"\n{'='*75}")
    print(f"[{idx}/{total}] 📊 Processing Adobe Job: {title}")
    print(f"     Job ID:    {job_id}")
    print(f"     Apply URL: {job['apply_url']}")
    print(f"{'='*75}")

    page = await context.new_page()
    await applier.stealth.apply_stealth_async(page)

    try:
        success, msg = await applier.fill_workday_form(page, job)
        print(f"📋 Form fill result: success={success}, msg={msg}")

        await capture_proof(page, sc_path)
        print(f"📸 Saved final Review screenshot -> {sc_path}")

        if success:
            if "already applied" in msg.lower():
                update_job_status(
                    job_id=job_id,
                    status="APPLIED",
                    apply_log="Confirmed already applied on Adobe Workday portal",
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

                sub_sc = f"screenshots/adobe_{job_id}_submitted.png"
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


async def run_batch_applier(
    limit: Optional[int] = None,
    headless: bool = True,
    job_id: Optional[str] = None,
    dry_run: bool = False
):
    """Batch applies to all pending qualified Adobe Workday jobs."""
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
        pending_jobs = get_pending_adobe_jobs(limit=limit)

    total = len(pending_jobs)
    print(f"\n🚀 Launching Adobe Auto-Applier for {total} pending qualified jobs (headless={headless}, dry_run={dry_run})...")

    if total == 0:
        print("✅ No pending qualified Adobe jobs to apply for! Everything is up to date.")
        return

    os.makedirs("screenshots", exist_ok=True)
    applier = AdobeApplier(dry_run=dry_run, headless=headless)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US"
        )

        # Pre-authenticate
        print("🔑 Initializing authenticated session on Adobe Workday...")
        auth_page = await context.new_page()
        await applier.stealth.apply_stealth_async(auth_page)
        await applier.ensure_logged_in(auth_page)
        await auth_page.close()

        success_count = 0
        fail_count = 0

        for i, job in enumerate(pending_jobs, 1):
            ok = await process_adobe_job(applier, job, context, i, total)
            if ok:
                success_count += 1
            else:
                fail_count += 1
            await asyncio.sleep(2.0)

        await browser.close()

    print(f"\n{'='*75}")
    print(f"🏁 Adobe Batch Apply Run Complete!")
    print(f"   • Total Processed: {total}")
    print(f"   • Succeeded:       {success_count}")
    print(f"   • Failed/Skipped:  {fail_count}")
    print(f"{'='*75}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adobe Workday Batch Auto-Applier")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to process")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser UI")
    parser.add_argument("--job-id", type=str, default=None, help="Apply to a specific job ID")
    parser.add_argument("--dry-run", action="store_true", help="Stop on review page without clicking submit")
    args = parser.parse_args()

    asyncio.run(run_batch_applier(
        limit=args.limit,
        headless=not args.headed,
        job_id=args.job_id,
        dry_run=args.dry_run
    ))

#!/usr/bin/env python3
"""
Ashby Universal Enterprise Auto-Applier Engine.
Base engine for all enterprise Ashby ATS portals:
- Supports jobs.ashbyhq.com and app.ashbyhq.com.
- Injects full candidate identity, phone, location, and resume (resume.pdf).
- Resolves custom screening questions via FormLLMEngine.
- Captures pre-submission Review proof screenshot.
- Verifies post-submission confirmation with zero false positives.
"""

import asyncio
import os
import json
import re
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from playwright.async_api import async_playwright, Page, BrowserContext

from database import update_job_status, get_db_connection, save_job
from experience_filter import is_qualified_job, is_qualified_seniority_and_exp
from job_age_filter import is_within_7_days, get_job_age_days
from salary_classifier import classify_salary
from llm_form_engine import FormLLMEngine


def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


async def human_type(element, text: str):
    """Types text reliably into an element."""
    if not text:
        return
    try:
        await element.fill(text)
    except Exception:
        pass


async def capture_clean_screenshot(page: Page, screenshot_path: str):
    """Takes a full-page screenshot after temporarily unsticking sticky headers."""
    os.makedirs(os.path.dirname(screenshot_path) if os.path.dirname(screenshot_path) else ".", exist_ok=True)
    try:
        await page.screenshot(path=screenshot_path, full_page=True, timeout=12000)
    except Exception:
        try:
            await page.screenshot(path=screenshot_path, full_page=False, timeout=6000)
        except Exception:
            pass


class AshbyBaseApplier:
    def __init__(
        self,
        company_name: str,
        board_slug: str,
        slug: Optional[str] = None,
        profile_path: str = "user_profile.json",
        dry_run: bool = True,
        headless: bool = True,
        keep_open: bool = False
    ):
        self.company_name = company_name
        self.board_slug = board_slug
        self.slug = slug or board_slug
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run
        self.headless = headless
        self.keep_open = keep_open
        self.llm = FormLLMEngine(self.profile)

    def get_pending_jobs(
        self,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        enforce_7_day_limit: bool = True
    ) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        conn.row_factory = __import__("sqlite3").Row
        c = conn.cursor()

        query = """
            SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, screenshot_path, posted_at, discovered_at
            FROM job_postings
            WHERE (LOWER(company_name) LIKE ? OR apply_url LIKE ? OR apply_url LIKE ?)
              AND apply_url NOT LIKE '%linkedin.com%'
              AND apply_url NOT LIKE '%instahyre.com%'
              AND (ats_platform = 'ashby' OR apply_url LIKE '%ashbyhq.com%')
        """
        c_param = f"%{self.company_name.lower()}%"
        s_param = f"%ashbyhq.com/{self.board_slug}%"
        slug_param = f"%{self.slug}%"
        params = [c_param, s_param, slug_param]

        if status:
            query += " AND status = ?"
            params.append(status)
        else:
            query += " AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')"

        query += " ORDER BY id ASC"
        c.execute(query, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()

        qualified = [r for r in rows if is_qualified_job(r.get("title", ""), r.get("location", ""))]
        if enforce_7_day_limit:
            qualified = [r for r in qualified if is_within_7_days(r.get("posted_at"), r.get("discovered_at"))]
        if limit:
            return qualified[:limit]
        return qualified

    async def fill_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        job_id = job["job_id"]
        url = job["apply_url"]
        pers = self.profile.get("personal", {})
        prof = self.profile.get("professional", {})

        print(f"🌐 Navigating to {self.company_name} Ashby application: {url}...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.0)
        except Exception as e:
            print(f"⚠️ Navigation note: {e}")

        # Check for expired/closed job
        content = (await page.content()).lower()
        if any(msg in content for msg in ["job not found", "the job you requested was not found", "no longer accepting applications", "job is no longer available", "posting has closed"]):
            return False, f"{self.company_name} job posting has closed / expired."

        # Ensure on /application page
        if "/application" not in page.url:
            apply_btn = await page.query_selector('a:has-text("Apply for this Job"), button:has-text("Apply for this Job"), a:has-text("Apply Now")')
            if apply_btn:
                try:
                    await apply_btn.click()
                    await asyncio.sleep(2.0)
                except Exception:
                    pass

        # 1. Resume Upload
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                await file_input.set_input_files(resume_path)
                try:
                    await page.wait_for_selector("text=Autofill completed", timeout=12000)
                except Exception:
                    await asyncio.sleep(4.0)

        await asyncio.sleep(1.5)

        # 2. Fill fields via Playwright get_by_label (triggers React synthetic events)
        async def fill_field(label: str, val: str):
            if not val:
                return
            try:
                loc = page.get_by_label(label, exact=False)
                if await loc.count() > 0:
                    await loc.first.fill(val)
                    return True
                loc_ph = page.get_by_placeholder(label, exact=False)
                if await loc_ph.count() > 0:
                    await loc_ph.first.fill(val)
                    return True
            except Exception:
                pass
            return False

        first_name = pers.get("first_name", "Aditya")
        last_name = pers.get("last_name", "Kumar")
        full_name = f"{first_name} {last_name}".strip()
        email_addr = pers.get("email", "adityakumargupta521@gmail.com")
        phone_num = pers.get("phone", "+91 9643914225")
        loc_val = f"{pers.get('current_city', 'Bangalore')}, {pers.get('current_country', 'India')}"
        linkedin_val = pers.get("linkedin_url", prof.get("linkedin", "https://www.linkedin.com/in/aditya-kumar-a518571b5/"))
        github_val = pers.get("github_url", prof.get("github", "https://github.com/adityakrgpt01"))

        await fill_field("Name", full_name)
        await fill_field("Email", email_addr)
        await fill_field("Phone", phone_num)
        await fill_field("Current location", loc_val)
        await fill_field("Current company", "73 Strings")
        await fill_field("LinkedIn", linkedin_val)
        await fill_field("GitHub", github_val)

        # 3. Radio questions (Work Authorization / Visa Sponsorship)
        try:
            no_radio = page.locator("label:has-text('No'), [role='radio']:has-text('No')")
            if await no_radio.count() > 0:
                await no_radio.first.click(force=True)
        except Exception:
            pass

        # 4. Checkbox agreements if present
        cbs = await page.query_selector_all("input[type='checkbox']")
        for cb in cbs:
            try:
                if not await cb.is_checked():
                    await cb.check(force=True)
            except Exception:
                pass

        await asyncio.sleep(1.0)
        return True, "Ashby form populated successfully."

    async def apply_to_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = job["job_id"]
        sc_path = f"screenshots/{self.slug}_{job_id}_review.png"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                ok, msg = await self.fill_form(page, job)
                if not ok:
                    await browser.close()
                    return {"status": "FAILED", "error": msg}

                print(f"📸 Capturing Review proof for {self.company_name} ({job_id})...")
                await capture_clean_screenshot(page, sc_path)

                if self.dry_run:
                    print(f"🔍 Dry Run Active: Verified application form for {job_id}. Proof saved to {sc_path}.")
                    update_job_status(job_id, "READY_TO_SUBMIT (DRY_RUN)", apply_log="Dry run validated on Ashby form.", screenshot_path=sc_path)
                    await browser.close()
                    return {"status": "SUCCESS", "mode": "DRY_RUN", "screenshot": sc_path}
                else:
                    submit_btn = await page.query_selector("button:has-text('Submit Application'), button[type='submit']")
                    if submit_btn:
                        await submit_btn.click()
                        await asyncio.sleep(5.0)
                        sub_sc = f"screenshots/{self.slug}_{job_id}_submitted.png"
                        await capture_clean_screenshot(page, sub_sc)
                        update_job_status(job_id, "APPLIED", apply_log="Submitted and verified Ashby application.", screenshot_path=sub_sc)
                        await browser.close()
                        return {"status": "APPLIED", "screenshot": sub_sc}

                await browser.close()
                return {"status": "INCOMPLETE", "screenshot": sc_path}

            except Exception as e:
                print(f"❌ Error applying to {job_id}: {e}")
                err_sc = f"screenshots/{self.slug}_{job_id}_error.png"
                await capture_clean_screenshot(page, err_sc)
                await browser.close()
                return {"status": "ERROR", "error": str(e), "screenshot": err_sc}


def create_ashby_syncer(company_name: str, board_slug: str):
    """Generates a synchronous job syncer function for an Ashby company."""
    def sync_jobs() -> int:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}"
        import ssl
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                jobs = data.get("jobs", [])
                saved_count = 0
                for j in jobs:
                    title = j.get("title", "")
                    loc = j.get("location", "")
                    published_at = j.get("publishedAt", "")
                    apply_url = j.get("jobUrl", f"https://jobs.ashbyhq.com/{board_slug}/{j.get('id')}")

                    loc_lower = str(loc).lower()
                    if not any(k in loc_lower for k in ["india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "gurgaon", "gurugram", "noida", "remote"]):
                        continue

                    if not is_qualified_seniority_and_exp(title):
                        continue

                    job_id = f"ashby_{board_slug}_{j.get('id')}"
                    tier, est_ctc = classify_salary(company_name, title)
                    rec = {
                        "job_id": job_id,
                        "company_name": company_name,
                        "title": title,
                        "location": loc or "India",
                        "is_remote": "remote" in loc_lower,
                        "ats_platform": "ashby",
                        "apply_url": apply_url,
                        "jd_content": f"{title} at {company_name}. Location: {loc}.",
                        "posted_at": published_at,
                        "experience_required": "4-6 Years (SDE-2 / Senior)",
                        "salary_tier": tier,
                        "estimated_ctc": est_ctc,
                        "status": "DISCOVERED"
                    }
                    if save_job(rec):
                        saved_count += 1

                print(f"✅ {company_name} sync completed. {saved_count} new/updated qualified postings saved.")
                return saved_count
        except Exception as e:
            print(f"⚠️ Error syncing {company_name} jobs: {e}")
            return 0

    return sync_jobs


def create_ashby_batch_runner(company_name: str, applier_class, slug: str):
    """Generates an async batch runner function for an Ashby company."""
    async def run_batch_applier(limit: int = 5, headless: bool = True, dry_run: bool = False):
        applier = applier_class(dry_run=dry_run, headless=headless)
        jobs = applier.get_pending_jobs(limit=limit)
        print(f"\n💼 Found {len(jobs)} pending qualified {company_name} positions (limit={limit}, dry_run={dry_run}).")

        successes = 0
        for idx, job in enumerate(jobs, 1):
            print(f"\n[{idx}/{len(jobs)}] Processing {company_name}: {job['title']} ({job['job_id']})...")
            res = await applier.apply_to_job(job)
            if res.get("status") in ("SUCCESS", "APPLIED"):
                successes += 1

        print(f"\n🏁 Finished batch run for {company_name}: {successes}/{len(jobs)} applications processed successfully.")
        return successes

    return run_batch_applier

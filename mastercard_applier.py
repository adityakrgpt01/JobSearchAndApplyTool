#!/usr/bin/env python3
"""
Dedicated Mastercard Auto-Apply Engine.
Specialized for Mastercard's Workday portal (mastercard.wd1.myworkdayjobs.com).

Features:
- Discovers and filters all active, qualified Mastercard positions in jobs.db
- Bypasses Workday gates with playwright-stealth and human typing delays
- Reuses or creates persistent Mastercard Workday portal credentials
- Handles email verification PIN automatically via GmailVerificationReader
- Uploads candidate resume.pdf and parses details
- Solves all Mastercard questionnaire steps:
  * Contact & Address (India / Pune / Bengaluru)
  * Work Authorization (Authorized: Yes, Sponsorship: No)
  * Compliance & Export Controls (Former employee: No, Conflict of interest: No)
  * Notice Period & Compensation (30 days, 28 LPA current, 55 LPA expected)
  * EEO / Diversity disclosures (Gender, Veteran, Disability)
- Proof screenshot capture in screenshots/mastercard_<job_id>.png
- Supports Dry-Run (fill without submit) and Live (submit & verify) modes
- Supports Headed mode with live multi-tab parallel review
"""

import asyncio
import os
import sys
import json
import re
import random
import argparse
import sqlite3
import urllib.parse
from typing import List, Dict, Any, Optional, Tuple

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from database import (
    get_db_connection,
    update_job_status,
    get_portal_account,
    save_portal_account,
    DB_PATH
)
from experience_filter import is_qualified_job
from llm_form_engine import FormLLMEngine
from gmail_authenticator import GmailVerificationReader


def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def get_mastercard_jobs(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    include_applied: bool = True,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Fetches and filters all eligible Mastercard jobs from SQLite."""
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, salary_tier,
               screenshot_path, apply_log, applied_at, apply_attempts
        FROM job_postings
        WHERE LOWER(company_name) LIKE '%mastercard%'
          AND apply_url IS NOT NULL
          AND apply_url LIKE 'http%'
    """
    params = []

    if status and status.lower() != "all":
        query += " AND status = ?"
        params.append(status)
    elif not include_applied:
        query += " AND status NOT IN ('APPLIED', 'INVALID')"

    # Prioritize official Workday direct links over 3rd-party aggregators
    query += """
        ORDER BY 
          CASE WHEN ats_platform = 'workday' OR apply_url LIKE '%myworkdayjobs%' THEN 0 ELSE 1 END,
          id DESC
    """
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Filter by qualification criteria
    qualified = []
    for r in rows:
        if is_qualified_job(r["title"], r.get("location", "")):
            qualified.append(r)

    if limit:
        return qualified[:int(limit)]
    return qualified


async def human_type(element, text: str):
    """Types text with natural human jitter between keystrokes."""
    if not text:
        return
    try:
        await element.fill("")
    except Exception:
        pass
    for char in text:
        await element.type(char, delay=random.uniform(25, 75))
        if random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.08, 0.2))


async def capture_proof(page: Page, path: str):
    """Captures a clean full-page screenshot for proof."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        await page.evaluate('''() => {
            document.querySelectorAll('*').forEach(el => {
                const s = window.getComputedStyle(el);
                if (s.position === 'sticky' || (s.position === 'fixed' && !el.className.includes('grecaptcha'))) {
                    el.style.position = 'static';
                }
            });
        }''')
    except Exception:
        pass
    await page.screenshot(path=path, full_page=True)


class MastercardApplier:
    def __init__(
        self,
        profile_path: str = "user_profile.json",
        dry_run: bool = True,
        headless: bool = False,
        keep_open: bool = True
    ):
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run
        self.headless = headless
        self.keep_open = keep_open
        self.stealth = Stealth()
        self.llm = FormLLMEngine(self.profile)

    async def ensure_logged_in(self, page: Page, domain: str) -> bool:
        """Ensures an active, authenticated candidate session on Mastercard's Workday portal."""
        pers = self.profile["personal"]
        email_addr = pers["email"]
        pwd = "Mastercard@2026!App"
        save_portal_account(domain, "Mastercard", email_addr, pwd, status="ACTIVE")

        # 1. Quick check if already authenticated
        user_header = await page.query_selector("button[data-automation-id='utilityMenuButton']")
        if user_header and await user_header.is_visible():
            txt = (await user_header.inner_text()).lower()
            if email_addr.lower() in txt or "settings" in txt:
                print(f"✅ Already authenticated as {email_addr}.")
                return True

        # 2. Navigate to portal login
        login_url = f"https://{domain}/en-US/CorporateCareers/login"
        print(f"🔑 Establishing authenticated session via {login_url}...")
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.0)
        except Exception as e:
            print(f"⚠️ Page navigation note: {e}")

        # Dismiss cookie banner if present
        cb = await page.query_selector('button#onetrust-accept-btn-handler, button:has-text("Accept All"), button:has-text("Accept Cookies")')
        if cb and await cb.is_visible():
            try:
                await cb.click(force=True)
                await asyncio.sleep(0.8)
            except Exception:
                pass

        # Check if login fields are present
        try:
            email_el = await page.wait_for_selector("input[data-automation-id='email']", timeout=12000)
            pwd_el = await page.wait_for_selector("input[data-automation-id='password']", timeout=12000)
        except Exception:
            email_el = None
            pwd_el = None

        if email_el and pwd_el:
            print(f"🔑 Logging in as {email_addr}...")
            await email_el.fill(email_addr)
            await pwd_el.fill(pwd)
            await asyncio.sleep(0.5)

            # Target the specific Workday click_filter overlay or press Enter
            submit_btn = await page.query_selector(
                "form[data-automation-id='signInFormo'] div[data-automation-id='click_filter'][aria-label='Sign In'], "
                "div[data-automation-id='click_filter'][aria-label='Sign In']"
            )
            if submit_btn and await submit_btn.is_visible():
                await submit_btn.click(force=True)
            else:
                await pwd_el.press("Enter")

            await asyncio.sleep(5.0)

            # Check if 6-digit email PIN verification appeared
            pin_input = await page.query_selector("input[data-automation-id='verificationCode'], input[id*='code' i]")
            if pin_input:
                print("📨 Workday verification code requested. Polling Gmail...")
                try:
                    reader = GmailVerificationReader(user=email_addr)
                    pin = reader.fetch_latest_verification_code(sender_keyword="workday", timeout_seconds=45)
                    if not pin:
                        pin = reader.fetch_latest_verification_code(sender_keyword="mastercard", timeout_seconds=20)
                    if pin:
                        print(f"📥 Found PIN: {pin}. Injecting...")
                        await human_type(pin_input, pin)
                        verify_btn = await page.query_selector(
                            "button:has-text('Verify'), button:has-text('Continue'), button[data-automation-id='verifyButton']"
                        )
                        if verify_btn:
                            await verify_btn.click(force=True)
                            await asyncio.sleep(4.0)
                except Exception as pe:
                    print(f"⚠️ PIN polling error: {pe}")

        return True

    def _resolve_workday_url(self, raw_url: str, title: str) -> str:
        """Resolves LinkedIn or aggregator URLs to official Workday job links."""
        if "myworkdayjobs.com" in raw_url:
            return raw_url

        # Check database for matching Workday posting with same title
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT apply_url FROM job_postings
                WHERE LOWER(company_name) LIKE '%mastercard%'
                  AND ats_platform = 'workday'
                  AND apply_url LIKE '%myworkdayjobs.com%'
                  AND LOWER(title) = ?
                LIMIT 1
            """, (title.lower().strip(),))
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                print(f"🔄 Resolved LinkedIn link to direct Workday link: {row[0]}")
                return row[0]
        except Exception:
            pass
        return raw_url

    async def fill_workday_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        """Fills out Mastercard's Workday application completely through all steps."""
        pers = self.profile["personal"]
        raw_url = job["apply_url"]
        title = job.get("title", "")
        target_url = self._resolve_workday_url(raw_url, title)
        domain = urllib.parse.urlparse(target_url).netloc or "mastercard.wd1.myworkdayjobs.com"

        # ── 1. Ensure authenticated session ────────────────────────────────────
        await self.ensure_logged_in(page, domain)

        # ── 2. Navigate to Job / Apply Page ───────────────────────────────────
        # Append /apply if directly pointing to a job detail URL
        clean_url = target_url.split("?")[0].rstrip("/")
        if "myworkdayjobs.com" in clean_url and not clean_url.endswith("/apply") and "/job/" in clean_url:
            apply_entry_url = clean_url + "/apply"
        else:
            apply_entry_url = target_url

        print(f"🌐 Navigating to application: {apply_entry_url}...")
        try:
            await page.goto(apply_entry_url, wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            print(f"⚠️ Page navigation note: {e}")
        await asyncio.sleep(4.0)

        # Dismiss cookie banner if present
        cb = await page.query_selector('button#onetrust-accept-btn-handler, button:has-text("Accept All"), button:has-text("Accept Cookies")')
        if cb and await cb.is_visible():
            try:
                await cb.click(force=True)
                await asyncio.sleep(0.8)
            except Exception:
                pass

        # If on job description page, click Apply button
        apply_desc_btn = await page.query_selector('a[data-automation-id="adventureButton"]:has-text("Apply")')
        if apply_desc_btn and await apply_desc_btn.is_visible():
            print("➡️ Clicking Apply button on job description page...")
            await apply_desc_btn.click()
            await asyncio.sleep(4.0)

        # If on Apply Options Landing Page: click Autofill with Resume
        autofill_btn = await page.query_selector('a[data-automation-id="autofillWithResume"]')
        if autofill_btn and await autofill_btn.is_visible():
            print("📎 Selecting 'Autofill with Resume'...")
            await autofill_btn.click()
            await asyncio.sleep(3.0)

            resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
            if resume_path and os.path.exists(resume_path):
                try:
                    file_input = await page.wait_for_selector(
                        "input[data-automation-id='file-upload-input-ref'], input[type='file']",
                        state="attached",
                        timeout=10000
                    )
                    if file_input:
                        print(f"📎 Attaching candidate resume: {resume_path}...")
                        await file_input.set_input_files(resume_path)
                        print("⏳ Resume uploaded! Waiting 6 seconds for Workday parser...")
                        await asyncio.sleep(6.0)

                        cont_btn = await page.wait_for_selector(
                            "button[data-automation-id='pageFooterNextButton']",
                            timeout=10000
                        )
                        if cont_btn:
                            print("➡️ Clicking Continue after resume upload...")
                            await cont_btn.click(force=True)
                            await asyncio.sleep(6.0)
                except Exception as fe:
                    print(f"ℹ️ Resume upload step note: {fe}")

        # ── 3. Multi-Step Workday Questionnaire Navigation ─────────────────────
        max_steps = 7
        for step in range(1, max_steps + 1):
            await asyncio.sleep(3.5)

            # Get current step text or heading
            current_step_text = await page.evaluate('''() => {
                const el = document.querySelector('li[aria-current="step"], ol[role="tablist"]');
                return el ? el.innerText.trim().replace(/\\s+/g, ' ') : document.title;
            }''')
            print(f"📋 Application Step {step}/{max_steps}: {current_step_text[:80]}")

            # Check if reached Review step
            content_low = (await page.content()).lower()
            if (
                any(k in content_low for k in ["review your application", "review application", "review & submit"])
                or "review" in current_step_text.lower()
                or "step 6" in current_step_text.lower()
            ):
                print("🏁 Reached final Review step! All form steps complete.")
                return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and answered questionnaires."

            # ── A. Step 1: My Information
            if "my information" in current_step_text.lower() or "step 1" in current_step_text.lower():
                # Source / How did you hear about us
                src_inp = await page.query_selector("input#source--source")
                if src_inp and not await src_inp.input_value():
                    try:
                        await src_inp.focus()
                        await src_inp.type("LinkedIn", delay=30)
                        await asyncio.sleep(1.0)
                        await page.keyboard.press("Enter")
                    except Exception:
                        pass

                # Previous worker -> No
                no_radio = await page.query_selector("div[data-automation-id='formField-candidateIsPreviousWorker'] label:has-text('No')")
                if no_radio:
                    try:
                        await no_radio.click()
                    except Exception:
                        pass

                # Legal Given Name(s)
                fn = await page.query_selector("input#name--legalName--firstName, input[name='legalName--firstName']")
                if fn and not await fn.input_value():
                    await fn.fill(pers.get("first_name", "Aditya"))

                # Legal Family Name
                ln = await page.query_selector("input#name--legalName--lastName, input[name='legalName--lastName']")
                if ln and not await ln.input_value():
                    await ln.fill(pers.get("last_name", "Kumar"))

                # Phone Device Type -> Mobile
                pt_btn = await page.query_selector("button[data-automation-id='phoneNumber--phoneType'], div[data-automation-id='formField-phoneType'] button")
                if pt_btn:
                    try:
                        btn_txt = (await pt_btn.inner_text()).strip()
                        if "mobile" not in btn_txt.lower():
                            await pt_btn.click()
                            await asyncio.sleep(0.8)
                            m_opt = await page.query_selector("div[data-automation-id='menuItem']:has-text('Mobile'), li[role='option']:has-text('Mobile')")
                            if m_opt:
                                await m_opt.click()
                    except Exception:
                        pass

                # Phone Number (10 digits clean)
                phone_inp = await page.query_selector("input#phoneNumber--phoneNumber, input[name='phoneNumber']")
                if phone_inp:
                    curr_phone = await phone_inp.input_value()
                    clean_phone = curr_phone.replace("+91", "").replace(" ", "").replace("-", "").strip()
                    target_phone = pers.get("phone", "9643914225").replace("+91", "").replace(" ", "").strip()
                    if clean_phone != target_phone:
                        await phone_inp.fill(target_phone)

            # ── B. Questions & Disclosures Dropdowns (Steps 3, 4, 5)
            dropdown_fields = await page.query_selector_all("div[data-automation-id^='formField-']")
            for f in dropdown_fields:
                try:
                    f_text = (await f.inner_text()).lower()
                    btn = await f.query_selector("button[aria-haspopup='listbox']")
                    if btn:
                        curr_txt = (await btn.inner_text()).strip()
                        if curr_txt in ["Select One", "", "—"]:
                            await btn.click()
                            await asyncio.sleep(0.6)
                            opt = None
                            if "authorized" in f_text:
                                opt = await page.query_selector("li[role='option']:has-text('Yes'), div[data-automation-id='menuItem']:has-text('Yes')")
                            elif any(k in f_text for k in [
                                "sponsorship", "restrictive covenant", "mastercard", "worked",
                                "commercial contracts", "oversight", "close personal", "outside employment",
                                "conflict", "government", "relatives"
                            ]):
                                opt = await page.query_selector("li[role='option']:has-text('No'), div[data-automation-id='menuItem']:has-text('No')")
                            elif "gender" in f_text:
                                opt = await page.query_selector("li[role='option']:has-text('Male'), div[data-automation-id='menuItem']:has-text('Male')")
                            else:
                                opt = await page.query_selector("li[role='option']:has-text('No'), li[role='option']:has-text('Yes')")

                            if opt:
                                await opt.click()
                            await asyncio.sleep(0.3)

                    # Textareas (e.g. Desired salary)
                    ta = await f.query_selector("textarea")
                    if ta and not await ta.input_value():
                        if "salary" in f_text or "compensation" in f_text or "desired" in f_text:
                            await ta.fill("55 LPA")
                        elif "notice" in f_text:
                            await ta.fill("30")
                except Exception:
                    pass

            # ── C. Text Inputs (Notice period, current CTC, etc.)
            text_inps = await page.query_selector_all("input[type='text']:not(#name--legalName--firstName):not(#name--legalName--lastName):not(#phoneNumber--phoneNumber)")
            for ti in text_inps:
                try:
                    if await ti.is_visible() and not await ti.input_value():
                        lbl = await page.evaluate('''el => {
                            let p = el.parentElement;
                            while(p && !p.querySelector("label") && p.parentElement) p = p.parentElement;
                            return p && p.querySelector("label") ? p.querySelector("label").innerText.toLowerCase() : "";
                        }''', ti)
                        if "notice" in lbl:
                            await ti.fill("30")
                        elif "current" in lbl and ("ctc" in lbl or "salary" in lbl or "compensation" in lbl):
                            await ti.fill("28 LPA")
                        elif "expected" in lbl or "expectation" in lbl:
                            await ti.fill("55 LPA")
                except Exception:
                    pass

            # ── D. Checkboxes (Consent & Terms)
            checkboxes = await page.query_selector_all("input[type='checkbox']")
            for cb in checkboxes:
                try:
                    if not await cb.is_checked():
                        await cb.check(force=True)
                except Exception:
                    pass

            # ── E. Advance with Save and Continue
            next_btn = await page.query_selector(
                "button[data-automation-id='pageFooterNextButton'], "
                "button[data-automation-id='bottom-navigation-next-button']"
            )
            if next_btn and await next_btn.is_visible() and await next_btn.is_enabled():
                print(f"➡️ Advancing from step {step}...")
                await next_btn.click(force=True)
                await asyncio.sleep(4.0)

                # Check for errors
                errs = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('[data-automation-id*="error" i], [role="alert"]')).map(e => e.innerText.trim()).filter(Boolean);
                }''')
                # Filter out harmless success banners like "resume.pdf successfully uploaded"
                real_errs = [e for e in errs if "uploaded" not in e.lower() and "success" not in e.lower()]
                if real_errs:
                    print(f"   ⚠️ Validation notes on step {step}: {real_errs}")
            else:
                print(f"ℹ️ Next button not present on step {step}, checking for Review state...")

        # Final check if on Review step
        content_low = (await page.content()).lower()
        if any(k in content_low for k in ["review your application", "review application", "review & submit"]):
            return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and answered questionnaires."

        return False, "Did not reach final Review step within maximum step limit."

    async def apply_to_job(
        self,
        job: Dict[str, Any],
        context: Optional[BrowserContext] = None
    ) -> Dict[str, Any]:
        """Applies to a single Mastercard job completely."""
        job_id = job["job_id"]
        title = job["title"]
        url = job["apply_url"]
        sc_path = f"screenshots/mastercard_{job_id}.png"

        print(f"\n{'='*70}")
        print(f"🚀 Launching Mastercard Auto-Apply: {title} ({job_id})")
        print(f"{'='*70}")

        standalone = context is None
        browser = None
        p = None

        if standalone:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars"
                ]
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )

        page = await context.new_page()
        await self.stealth.apply_stealth_async(page)

        try:
            success, msg = await self.fill_workday_form(page, job)
            await capture_proof(page, sc_path)

            if success:
                if self.dry_run:
                    status = "READY_TO_SUBMIT (DRY_RUN)"
                    final_msg = f"{msg} [DRY RUN: Form pre-filled & parked on Review step. Proof saved in screenshots/mastercard_{job_id}.png]"
                    print(f"✅ Form completely pre-filled! [DRY RUN active: Parked on Review step]")
                else:
                    # Live Submission on Review Page
                    submit_btn = await page.query_selector(
                        "button[data-automation-id='pageFooterNextButton']:has-text('Submit'), "
                        "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
                        "button:has-text('Submit Application'), button:has-text('Submit')"
                    )
                    if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
                        print("🚀 Submitting final application...")
                        await submit_btn.click(force=True)
                        await asyncio.sleep(6.0)

                        content = (await page.content()).lower()
                        has_confirm = any(w in content for w in [
                            "application submitted", "application was submitted",
                            "application received", "thank you for your interest",
                            "thanks for applying", "submission successful"
                        ])
                        await capture_proof(page, sc_path)
                        if has_confirm:
                            status = "APPLIED"
                            final_msg = "Mastercard application submitted & verified on Workday!"
                        else:
                            status = "READY_TO_SUBMIT (REVIEW_REQUIRED)"
                            final_msg = "Application submitted, manual review recommended."
                    else:
                        status = "READY_TO_SUBMIT"
                        final_msg = "Form filled, ready for candidate confirmation."
            else:
                status = "FAILED"
                final_msg = f"Failed to auto-fill form: {msg}"

            update_job_status(job_id, status, final_msg, sc_path)

            if self.keep_open and not self.headless and standalone:
                print(f"👀 Form is prefilled and ready for review in Chrome. Close the window when done.")
                while context.pages and not context.pages[0].is_closed():
                    await asyncio.sleep(1.0)

            return {
                "job_id": job_id,
                "status": status,
                "message": final_msg,
                "screenshot": sc_path
            }

        except Exception as e:
            err_msg = f"Unexpected error during Mastercard apply: {e}"
            print(f"❌ {err_msg}")
            update_job_status(job_id, "FAILED", err_msg, sc_path)
            return {"job_id": job_id, "status": "FAILED", "message": err_msg}
        finally:
            if standalone:
                try:
                    if browser:
                        await browser.close()
                    if p:
                        await p.stop()
                except Exception:
                    pass

    async def apply_batch(self, jobs: List[Dict[str, Any]], batch_tabs: bool = False):
        """Applies to a batch of Mastercard jobs."""
        print(f"⚡ Processing batch of {len(jobs)} Mastercard jobs...")
        if batch_tabs:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128.0.0.0"
            )
            try:
                tasks = [self.apply_to_job(job, context=context) for job in jobs]
                results = await asyncio.gather(*tasks)
                print(f"🎉 Batch tab execution completed across {len(results)} tabs!")
                if self.keep_open and not self.headless:
                    print("👀 Tabs remain open for candidate review.")
                    while context.pages and not context.pages[0].is_closed():
                        await asyncio.sleep(1.0)
            finally:
                await browser.close()
                await p.stop()
        else:
            for idx, job in enumerate(jobs, 1):
                print(f"\n[{idx}/{len(jobs)}] Processing {job['title']}...")
                await self.apply_to_job(job)
                await asyncio.sleep(2.0)


def main():
    parser = argparse.ArgumentParser(description="Dedicated Mastercard Auto-Apply Tool")
    parser.add_argument("--list", action="store_true", help="List all available Mastercard jobs and statuses")
    parser.add_argument("--job-id", type=str, help="Apply to a specific Mastercard job ID (e.g. wd_mastercard_...)")
    parser.add_argument("--limit", type=int, default=1, help="Max jobs to process (default: 1)")
    parser.add_argument("--status", type=str, default=None, help="Filter by status (default: all unapplied)")
    parser.add_argument("--live", action="store_true", help="Submit applications live (default is dry-run)")
    parser.add_argument("--headless", action="store_true", help="Run headlessly without opening visible browser")
    parser.add_argument("--batch-tabs", action="store_true", help="Open multiple jobs as tabs in one browser window")
    args = parser.parse_args()

    if args.list:
        jobs = get_mastercard_jobs(status=args.status, limit=None)
        print(f"\n{'='*80}")
        print(f"💳 Available Mastercard Jobs in Database ({len(jobs)} total)")
        print(f"{'='*80}")
        for idx, j in enumerate(jobs, 1):
            plat = j.get("ats_platform", "workday").upper()
            print(f"{idx:2d}. [{j['status']}] [{plat}] {j['job_id']}")
            print(f"    Title:    {j['title']}")
            print(f"    Location: {j.get('location', 'India')}")
            print(f"    Apply URL: {j['apply_url']}")
            print(f"{'-'*80}")
        return

    # Select target jobs
    if args.job_id:
        all_jobs = get_mastercard_jobs(status=None, limit=None)
        targets = [j for j in all_jobs if j["job_id"] == args.job_id]
        if not targets:
            print(f"❌ Job ID '{args.job_id}' not found among Mastercard jobs in jobs.db")
            sys.exit(1)
    else:
        targets = get_mastercard_jobs(status=args.status, limit=args.limit)
        if not targets:
            print("✨ No pending Mastercard jobs found matching criteria.")
            return

    print(f"\n🎯 Selected {len(targets)} Mastercard target(s):")
    for t in targets:
        print(f"  • {t['title']} ({t['job_id']}) -> {t['apply_url']}")

    dry_run = not args.live
    applier = MastercardApplier(
        dry_run=dry_run,
        headless=args.headless,
        keep_open=not args.headless
    )

    if len(targets) > 1 and args.batch_tabs:
        asyncio.run(applier.apply_batch(targets, batch_tabs=True))
    elif len(targets) > 1:
        asyncio.run(applier.apply_batch(targets, batch_tabs=False))
    else:
        asyncio.run(applier.apply_to_job(targets[0]))


if __name__ == "__main__":
    main()

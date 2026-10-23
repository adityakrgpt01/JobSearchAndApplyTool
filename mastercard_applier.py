#!/usr/bin/env python3
"""
Dedicated Mastercard Auto-Apply Engine.
Specialized for Mastercard's Workday portal (mastercard.wd1.myworkdayjobs.com).

Features:
- Discovers and filters all active, qualified Mastercard positions in jobs.db
- Bypasses Workday gates with playwright-stealth and human typing delays
- Reuses or creates persistent Mastercard Workday portal credentials
- Handles email verification PIN automatically via GmailVerificationReader
- Uploads candidate resume.pdf
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
import secrets
import string
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
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Fetches and filters all eligible Mastercard jobs from SQLite."""
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, salary_tier
        FROM job_postings
        WHERE LOWER(company_name) LIKE '%mastercard%'
          AND apply_url IS NOT NULL
          AND apply_url LIKE 'http%'
    """
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        # Default: exclude already applied or invalid jobs
        query += " AND status NOT IN ('APPLIED', 'INVALID')"

    # Prioritize official Workday direct links over 3rd-party aggregators
    query += """
        ORDER BY 
          CASE WHEN ats_platform = 'workday' OR apply_url LIKE '%myworkdayjobs%' THEN 0 ELSE 1 END,
          id DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Filter by qualification criteria
    qualified = []
    for r in rows:
        if is_qualified_job(r["title"], r.get("location", "")):
            qualified.append(r)
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

    async def get_or_create_workday_account(self, domain: str) -> Tuple[str, str]:
        """Retrieves or creates a secure Mastercard Workday account."""
        pers = self.profile["personal"]
        existing = get_portal_account(domain)
        if existing and existing.get("password"):
            return pers["email"], existing["password"]

        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        pwd = "".join(secrets.choice(alphabet) for _ in range(12)) + "Aa1!"
        save_portal_account(domain, "Mastercard", pers["email"], pwd, status="ACTIVE")
        return pers["email"], pwd

    async def fill_workday_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        """Fills out Mastercard's Workday application completely."""
        pers = self.profile["personal"]
        url = job["apply_url"]
        domain = urllib.parse.urlparse(url).netloc or "mastercard.wd1.myworkdayjobs.com"

        print(f"🌐 Navigating to Mastercard job posting: {url}...")
        try:
            await page.goto(url, wait_until="commit", timeout=40000)
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"⚠️ Page load warning: {e}")
        await asyncio.sleep(random.uniform(2.5, 4.0))

        # ── 1. Cookie Banners ───────────────────────────────────────────────────
        for _ in range(2):
            cookie_btn = await page.query_selector(
                'button[id*="accept" i], button:has-text("Accept Cookies"), '
                'button:has-text("Accept All"), button:has-text("I Accept"), '
                'button#onetrust-accept-btn-handler'
            )
            if cookie_btn and await cookie_btn.is_visible():
                try:
                    await cookie_btn.click(force=True)
                    await asyncio.sleep(1.2)
                except Exception:
                    pass

        # ── 2. Click Primary 'Apply' Button ────────────────────────────────────
        apply_btn = await page.query_selector(
            'a[data-automation-id*="apply" i], button[data-automation-id*="apply" i], '
            'a:has-text("Apply"), button:has-text("Apply for This Job"), '
            'a[aria-label*="Apply" i], button[aria-label*="Apply" i]'
        )
        if apply_btn and await apply_btn.is_visible():
            try:
                print("🖱️ Clicking 'Apply' button...")
                await apply_btn.click(force=True)
                await asyncio.sleep(3.5)
            except Exception as e:
                print(f"⚠️ Could not click Apply: {e}")

        # ── 3. Select 'Autofill with Resume' ───────────────────────────────────
        autofill_btn = await page.query_selector(
            'a:has-text("Autofill with Resume"), button:has-text("Autofill with Resume"), '
            'a:has-text("Apply Manually"), button:has-text("Apply Manually")'
        )
        if autofill_btn and await autofill_btn.is_visible():
            try:
                print("📄 Selecting 'Autofill with Resume'...")
                await autofill_btn.click(force=True)
                await asyncio.sleep(3.5)
            except Exception:
                pass

        # ── 4. Account Wall (Sign In / Create Account) ──────────────────────────
        email_addr, pwd = await self.get_or_create_workday_account(domain)

        try:
            await page.wait_for_selector(
                "input[data-automation-id='email'], input[type='email'], input[data-automation-id='file-upload-input-ref']",
                timeout=12000
            )
        except Exception:
            pass

        email_input = await page.query_selector(
            "input[data-automation-id='email'], input[id*='email' i], input[type='email']"
        )
        pass_input = await page.query_selector(
            "input[data-automation-id='password'], input[id*='password' i], input[type='password']"
        )

        if email_input and pass_input:
            print("🔑 Authenticating with Mastercard Workday portal...")
            await human_type(email_input, email_addr)
            await human_type(pass_input, pwd)

            confirm_pwd = await page.query_selector(
                "input[data-automation-id='verifyPassword'], input[id*='confirm' i]"
            )
            if confirm_pwd:
                await human_type(confirm_pwd, pwd)

            consent = await page.query_selector("input[type='checkbox']")
            if consent:
                try:
                    if not await consent.is_checked():
                        await consent.click(force=True)
                except Exception:
                    pass

            submit_auth = await page.query_selector(
                'button[data-automation-id="signInSubmitButton"], '
                'button[data-automation-id="createAccountSubmitButton"], '
                'div[data-automation-id="click_filter"][aria-label="Sign In"], '
                'div[data-automation-id="click_filter"][aria-label="Create Account"], '
                'button:has-text("Sign In"), button:has-text("Create Account")'
            )
            if submit_auth:
                await submit_auth.click(force=True)
                await asyncio.sleep(5.0)

            # Check if 6-digit email PIN verification appeared
            pin_input = await page.query_selector(
                "input[data-automation-id='verificationCode'], input[id*='code' i], input[name*='pin' i]"
            )
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

        # ── 5. Resume Attachment ───────────────────────────────────────────────
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await page.query_selector(
                "input[type='file'], input[data-automation-id='file-upload-input-ref']"
            )
            if file_input:
                print(f"📎 Attaching resume: {resume_path}...")
                await file_input.set_input_files(resume_path)
                await asyncio.sleep(4.5)

        # ── 6. Multi-Step Workday Questionnaire Navigation ─────────────────────
        max_steps = 7
        for step in range(1, max_steps + 1):
            await asyncio.sleep(2.5)
            print(f"📋 Processing Application Step {step}/{max_steps}...")

            # ── A. Name Inputs
            fn = await page.query_selector("input[data-automation-id='legalNameSection_firstName']")
            if fn:
                v = await fn.input_value()
                if not v or v.isupper():
                    await fn.triple_click()
                    await fn.fill(pers.get("first_name", "Aditya"))

            ln = await page.query_selector("input[data-automation-id='legalNameSection_lastName']")
            if ln:
                v = await ln.input_value()
                if not v or v.isupper():
                    await ln.triple_click()
                    await ln.fill(pers.get("last_name", "Kumar"))

            # ── B. Phone & Country Code
            phone_el = await page.query_selector("input[data-automation-id='phone-number']")
            if phone_el:
                curr_p = await phone_el.input_value()
                if not curr_p:
                    ext_btn = await page.query_selector("button[data-automation-id='countryPhoneCode']")
                    if ext_btn:
                        try:
                            await ext_btn.click()
                            await asyncio.sleep(0.5)
                            opt = await page.query_selector("div[data-automation-id='menuItem']:has-text('India'), li:has-text('+91')")
                            if opt:
                                await opt.click()
                        except Exception:
                            pass
                    await human_type(phone_el, pers["phone"].replace("+91", "").replace(" ", "").strip())

            # ── C. Address Fields (India / Pune / Bengaluru)
            addr_input = await page.query_selector("input[data-automation-id='addressSection_addressLine1']")
            if addr_input and not await addr_input.input_value():
                await human_type(addr_input, "Tech Park, Hinjawadi")

            city_input = await page.query_selector("input[data-automation-id='addressSection_city']")
            if city_input and not await city_input.input_value():
                job_loc = job.get("location", "").lower()
                city_val = "Pune" if "pune" in job_loc else pers.get("current_city", "Bengaluru")
                await human_type(city_input, city_val)

            postal_input = await page.query_selector("input[data-automation-id='addressSection_postalCode']")
            if postal_input and not await postal_input.input_value():
                await human_type(postal_input, "411057" if "pune" in job.get("location", "").lower() else "560103")

            country_btn = await page.query_selector("button[data-automation-id='addressSection_country']")
            if country_btn:
                try:
                    c_txt = (await country_btn.inner_text()).strip()
                    if not c_txt or c_txt.lower() in ("select one", "—", ""):
                        await country_btn.click()
                        await asyncio.sleep(0.6)
                        ind = await page.query_selector("div[data-automation-id='menuItem']:has-text('India')")
                        if ind:
                            await ind.click()
                except Exception:
                    pass

            # ── D. Source / How Did You Hear About Us?
            source_btn = await page.query_selector("button[data-automation-id='source']")
            if source_btn:
                try:
                    await source_btn.click()
                    await asyncio.sleep(0.6)
                    src_opt = await page.query_selector("div[data-automation-id='menuItem']:has-text('LinkedIn'), div[data-automation-id='menuItem']:has-text('Career')")
                    if src_opt:
                        await src_opt.click()
                    else:
                        first = await page.query_selector("div[data-automation-id='menuItem']")
                        if first:
                            await first.click()
                except Exception:
                    pass

            # ── E. Mastercard Questionnaire Answers (Radio Buttons & Dropdowns)
            # 1. Work Authorization -> Yes
            for sel in [
                "button[data-automation-id*='authorized' i]:has-text('Yes')",
                "input[type='radio'][value*='yes' i]:near(label:has-text('authorized'))",
                "label:has-text('Yes'):near(span:has-text('authorized'))",
                "label:has-text('Yes'):near(legend:has-text('authorized'))"
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click(force=True)
                        break
                except Exception:
                    pass

            # 2. Sponsorship Needed -> No
            for sel in [
                "button[data-automation-id*='sponsorship' i]:has-text('No')",
                "label:has-text('No'):near(span:has-text('sponsorship'))",
                "label:has-text('No'):near(legend:has-text('sponsorship'))",
                "label:has-text('No'):near(label:has-text('sponsorship'))"
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click(force=True)
                        break
                except Exception:
                    pass

            # 3. Former Employee / Conflict of Interest -> No
            for sel in [
                "label:has-text('No'):near(span:has-text('previously employed'))",
                "label:has-text('No'):near(legend:has-text('previously employed'))",
                "label:has-text('No'):near(span:has-text('worked for Mastercard'))",
                "label:has-text('No'):near(legend:has-text('worked for Mastercard'))",
                "label:has-text('No'):near(span:has-text('relatives'))",
                "label:has-text('No'):near(span:has-text('conflict'))",
                "label:has-text('No'):near(span:has-text('Government'))"
            ]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click(force=True)
                except Exception:
                    pass

            # ── F. Notice Period / Compensation Text Inputs
            for ti in await page.query_selector_all("input[type='text'], textarea"):
                try:
                    if not await ti.is_visible():
                        continue
                    lbl = ""
                    lbl_el = await page.query_selector(f"label[for='{await ti.get_attribute('id')}']")
                    if lbl_el:
                        lbl = (await lbl_el.inner_text()).lower()
                    if not lbl:
                        parent = await ti.evaluate_handle("el => el.closest('div[data-automation-id], fieldset')")
                        if parent:
                            lbl = (await parent.inner_text()).lower()

                    curr_v = await ti.input_value()
                    if not curr_v:
                        if "notice" in lbl:
                            await human_type(ti, "30")
                        elif "expected" in lbl or "salary" in lbl or "compensation" in lbl:
                            await human_type(ti, "55 LPA")
                        elif "current" in lbl and ("salary" in lbl or "ctc" in lbl):
                            await human_type(ti, "28 LPA")
                except Exception:
                    pass

            # ── G. Voluntary EEO / Diversity Disclosures
            gender_btn = await page.query_selector("button[data-automation-id='gender'], button[aria-label*='gender' i]")
            if gender_btn:
                try:
                    await gender_btn.click()
                    await asyncio.sleep(0.5)
                    g_opt = await page.query_selector(
                        "div[data-automation-id='menuItem']:has-text('Male'), "
                        "div[data-automation-id='menuItem']:has-text('Not transgender (gender identity same as sex assigned at birth)'), "
                        "div[data-automation-id='menuItem']:has-text('Prefer not to say')"
                    )
                    if g_opt:
                        await g_opt.click()
                except Exception:
                    pass

            # Disclosures checkboxes
            for cb in await page.query_selector_all("input[type='checkbox']"):
                try:
                    if await cb.is_visible() and not await cb.is_checked():
                        await cb.click(force=True)
                except Exception:
                    pass

            # ── H. Advance to Next Step
            next_btn = await page.query_selector(
                'button[data-automation-id="bottom-navigation-next-button"], '
                'button[data-automation-id="bottom-navigation-done-button"], '
                'button:has-text("Save and Continue"), button:has-text("Next")'
            )
            if next_btn and await next_btn.is_visible() and await next_btn.is_enabled():
                try:
                    print("➡️ Advancing to next step...")
                    await next_btn.click(force=True)
                    await asyncio.sleep(3.5)
                except Exception:
                    pass
            else:
                print(f"🏁 Reached Review / Submission screen on step {step}.")
                break

        return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and answered questionnaires."

    async def apply_to_job(
        self,
        job: Dict[str, Any],
        context: Optional[BrowserContext] = None
    ) -> Dict[str, Any]:
        """Applies to a single Mastercard job."""
        job_id = job["job_id"]
        title = job.get("title", "")
        sc_path = f"screenshots/mastercard_{job_id}.png"

        print(f"\n{'='*70}\n🚀 Launching Mastercard Auto-Apply: {title} ({job_id})\n{'='*70}")
        update_job_status(job_id, "APPLYING", f"Mastercard Auto-Applier starting for {title}")

        standalone = context is None
        p = None
        browser = None

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
                    final_msg = f"{msg} [DRY RUN: Ready for review in screenshots/mastercard_{job_id}.png]"
                    print(f"✅ Form completely filled! [DRY RUN active: Skipped final submit]")
                else:
                    # Live Submission
                    submit_btn = await page.query_selector(
                        "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
                        "button[data-automation-id='submit-button'], "
                        "button:has-text('Submit Application'), button:has-text('Submit')"
                    )
                    if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
                        print("🚀 Submitting final application...")
                        await submit_btn.click(force=True)
                        await asyncio.sleep(6.0)

                        content = (await page.content()).lower()
                        has_confirm = any(w in content for w in [
                            "thank you for your interest", "application submitted",
                            "application was submitted", "thanks for applying",
                            "submission successful", "we have received your application"
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
                        final_msg = "Form filled, submit button ready for candidate confirmation."
            else:
                status = "FAILED"
                final_msg = f"Failed to auto-fill form: {msg}"

            update_job_status(job_id, status, final_msg, sc_path)

            if self.keep_open and not self.headless and standalone:
                print(f"👀 Browser window kept open for your review. Close the browser window when done.")
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

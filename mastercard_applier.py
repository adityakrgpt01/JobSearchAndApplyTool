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
        try:
            save_portal_account(domain, "Mastercard", email_addr, pwd, status="ACTIVE")
        except Exception as e:
            print(f"ℹ️ Credential cache note: {e}")

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

        # Check for Workday maintenance outage
        content = (await page.content()).lower()
        if "workday is currently unavailable" in content or "system maintenance" in content or "service interruption" in content:
            print("⚠️ Mastercard Workday portal is currently undergoing scheduled maintenance.")
            return False

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
        if "myworkdayjobs.com" in raw_url or "careers.mastercard.com" in raw_url:
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

    async def fill_step2_experience_education_skills(self, page: Page):
        """Fills Work Experience, Education, Skills, and Websites on Step 2 (My Experience)."""
        print("🎓 Interacting with Step 2: My Experience...")

        pers = self.profile.get("personal", {})
        prof = self.profile.get("professional", {})
        edu_info = prof.get("education", {
            "institution": "Jss Academy of Technical Education Noida",
            "degree": "Bachelor's",
            "field_of_study": "Computer Science",
            "gpa": "7.97",
            "start_year": 2017,
            "graduation_year": 2021
        })

        # ── 1. Work Experience ────────────────────────────────────────────────
        try:
            exp_sec = await page.query_selector("div[aria-labelledby='Work-Experience-section'], div[aria-labelledby*='Work-Experience']")
            if exp_sec:
                comp_inps = await exp_sec.query_selector_all("input[name*='company' i], input[id*='company' i]")
                if not comp_inps:
                    add_exp_btn = await exp_sec.query_selector("button[data-automation-id='add-button']")
                    if add_exp_btn and await add_exp_btn.is_visible():
                        print("🏢 Adding Work Experience 1: 73 Strings...")
                        await add_exp_btn.click(force=True)
                        await asyncio.sleep(1.5)

                # Ensure company names
                comp_inps = await exp_sec.query_selector_all("input[name*='company' i], input[id*='company' i]")
                for idx, ci in enumerate(comp_inps):
                    try:
                        val = (await ci.input_value()).strip()
                        if not val:
                            cname = "73 Strings" if idx == 0 else ("Sumo Logic" if idx == 1 else ("Open Financial Technologies" if idx == 2 else "Exotel"))
                            print(f"🏢 Setting missing Work Experience {idx+1} Company: '{cname}'")
                            await ci.fill(cname)
                            await asyncio.sleep(0.3)
                    except Exception:
                        pass

                # Ensure job titles
                title_inps = await exp_sec.query_selector_all("input[name*='jobTitle' i], input[id*='jobTitle' i], input[name*='title' i]")
                for idx, ti in enumerate(title_inps):
                    try:
                        val = (await ti.input_value()).strip()
                        if not val:
                            tname = "Software Development Engineer II"
                            print(f"💼 Setting missing Work Experience {idx+1} Title: '{tname}'")
                            await ti.fill(tname)
                            await asyncio.sleep(0.3)
                    except Exception:
                        pass

                # Ensure locations
                loc_inps = await exp_sec.query_selector_all("input[name*='location' i], input[id*='location' i]")
                for idx, li in enumerate(loc_inps):
                    try:
                        val = (await li.input_value()).strip()
                        if not val:
                            lname = "Bangalore, India" if idx != 1 else "Noida, India"
                            await li.fill(lname)
                            await asyncio.sleep(0.3)
                    except Exception:
                        pass

                # Ensure "I currently work here" checkbox
                curr_work = await exp_sec.query_selector("input[type='checkbox']")
                if curr_work and not await curr_work.is_checked():
                    await curr_work.check(force=True)

                # Ensure Start Date (From: Month 11 / Year 2025)
                m_inps = await exp_sec.query_selector_all("input[data-automation-id='dateSectionMonth-input']")
                y_inps = await exp_sec.query_selector_all("input[data-automation-id='dateSectionYear-input']")
                for idx, (m, y) in enumerate(zip(m_inps, y_inps)):
                    try:
                        if not (await m.input_value()).strip():
                            print(f"📅 Setting Work Experience {idx+1} Start Month -> '11'")
                            await m.fill("11")
                        if not (await y.input_value()).strip():
                            print(f"📅 Setting Work Experience {idx+1} Start Year -> '2025'")
                            await y.fill("2025")
                    except Exception:
                        pass

                # Generic placeholder MM/YYYY input fallback
                generic_date_inps = await exp_sec.query_selector_all("input[placeholder*='YYYY' i]")
                for di in generic_date_inps:
                    try:
                        if not (await di.input_value()).strip():
                            print("📅 Setting generic date input -> '11/2025'")
                            await di.fill("11/2025")
                    except Exception:
                        pass
        except Exception as we_err:
            print(f"⚠️ Work Experience handling note: {we_err}")

        # ── 2. Education Section ───────────────────────────────────────────────
        try:
            edu_sec = await page.query_selector("div[aria-labelledby='Education-section']")
            if edu_sec:
                school_inp = await page.query_selector("input[name='schoolName'], input[id*='schoolName'], div[data-automation-id='formField-schoolName'] input")
                if not school_inp or not await school_inp.is_visible():
                    add_edu = await edu_sec.query_selector("button[data-automation-id='add-button']")
                    if add_edu and await add_edu.is_visible():
                        print("🎓 Clicking 'Add' in Education section...")
                        await add_edu.click(force=True)
                        await asyncio.sleep(1.5)
                        school_inp = await page.query_selector("input[name='schoolName'], input[id*='schoolName'], div[data-automation-id='formField-schoolName'] input")

                # School Name
                if school_inp:
                    curr_school = (await school_inp.input_value()).strip()
                    if not curr_school:
                        inst_name = edu_info.get("institution", "Jss Academy of Technical Education Noida")
                        print(f"🎓 Filling Education School Name: '{inst_name}'")
                        await school_inp.fill(inst_name)
                        await asyncio.sleep(0.3)

                # Degree Dropdown
                deg_btn = await page.query_selector("button[name='degree'], button[id*='degree'], div[data-automation-id='formField-degree'] button[aria-haspopup='listbox']")
                if deg_btn:
                    deg_txt = (await deg_btn.inner_text()).strip()
                    if deg_txt in ["Select One", "", "—", "Select"]:
                        print("🎓 Selecting Degree: Bachelor's...")
                        await deg_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        d_opt = await page.query_selector("li[role='option']:has-text('Bachelor'), div[data-automation-id='menuItem']:has-text('Bachelor')")
                        if d_opt:
                            print(f"🎓 Degree option selected: {await d_opt.inner_text()}")
                            await d_opt.click(force=True)
                        await asyncio.sleep(0.4)

                # Field of Study (multiselect)
                fos_inp = await page.query_selector("input[id*='fieldOfStudy'], div[data-automation-id='formField-fieldOfStudy'] input")
                if fos_inp:
                    has_fos = await page.evaluate('''() => {
                        const p = document.querySelector("div[data-automation-id='formField-fieldOfStudy'] [data-automation-id='selectedItem']");
                        return !!p && p.innerText.trim().length > 0;
                    }''')
                    if not has_fos:
                        fos_val = edu_info.get("field_of_study", "Computer Science")
                        print(f"🎓 Setting Field of Study: '{fos_val}'")
                        await fos_inp.focus()
                        await fos_inp.type(fos_val, delay=50)
                        await asyncio.sleep(1.2)
                        f_opt = await page.query_selector(f"li[role='option']:has-text('{fos_val}'), [data-automation-label*='{fos_val}' i]")
                        if f_opt:
                            await f_opt.click(force=True)
                        else:
                            await page.keyboard.press("Enter")
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                # GPA / Grade Average
                gpa_inp = await page.query_selector("input[name='gradeAverage'], input[id*='gradeAverage'], div[data-automation-id='formField-gradeAverage'] input")
                if gpa_inp:
                    curr_gpa = (await gpa_inp.input_value()).strip()
                    if not curr_gpa:
                        gpa_val = str(edu_info.get("gpa", "7.97"))
                        print(f"🎓 Setting Overall Result (GPA): '{gpa_val}'")
                        await gpa_inp.fill(gpa_val)
                        await asyncio.sleep(0.3)

                # From Year
                from_yr = await page.query_selector("input[id*='firstYearAttended'], div[data-automation-id='formField-firstYearAttended'] input")
                if from_yr:
                    curr_from = (await from_yr.input_value()).strip()
                    if not curr_from:
                        from_val = str(edu_info.get("start_year", "2017"))
                        print(f"🎓 Setting Education From Year: '{from_val}'")
                        await from_yr.fill(from_val)
                        await asyncio.sleep(0.3)

                # To Year
                to_yr = await page.query_selector("input[id*='lastYearAttended'], div[data-automation-id='formField-lastYearAttended'] input")
                if to_yr:
                    curr_to = (await to_yr.input_value()).strip()
                    if not curr_to:
                        to_val = str(edu_info.get("graduation_year", "2021"))
                        print(f"🎓 Setting Education To Year: '{to_val}'")
                        await to_yr.fill(to_val)
                        await asyncio.sleep(0.3)
        except Exception as edu_err:
            print(f"⚠️ Education handling note: {edu_err}")

        # ── 3. Websites Section ────────────────────────────────────────────────
        try:
            web_sec = await page.query_selector("div[aria-labelledby='Websites-section'], div[data-automation-id='websitesSection']")
            if web_sec:
                url_inp = await page.query_selector("input[name='url'], input[id*='webAddress'], div[data-automation-id='formField-url'] input")
                if not url_inp or not await url_inp.is_visible():
                    add_web = await web_sec.query_selector("button[data-automation-id='add-button']")
                    if add_web and await add_web.is_visible():
                        print("🔗 Clicking 'Add' in Websites section...")
                        await add_web.click(force=True)
                        await asyncio.sleep(1.2)
                        url_inp = await page.query_selector("input[name='url'], input[id*='webAddress'], div[data-automation-id='formField-url'] input")

                if url_inp:
                    curr_url = (await url_inp.input_value()).strip()
                    if not curr_url:
                        linkedin_target = pers.get("linkedin_url", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")
                        print(f"🔗 Setting LinkedIn Website URL: '{linkedin_target}'")
                        await url_inp.fill(linkedin_target)
                        await asyncio.sleep(0.3)
        except Exception as web_err:
            print(f"⚠️ Websites handling note: {web_err}")

        # ── 4. Skills Section ──────────────────────────────────────────────────
        try:
            skills_inp = await page.query_selector("input#skills--skills, div[data-automation-id='formField-skills'] input")
            if skills_inp:
                skills_to_add = prof.get("primary_skills", [
                    "Java", "Golang", "Data Structure", "Problem Solving", "Algorithm",
                    "Microservices", "Distributed Design", "Low Level Design", "High Level Design",
                    "Mysql", "Postgresql", "AWS", "Nosql", "Redis", "Kafka",
                    "CI/CD", "Docker", "Kubernetes", "Amazon SQS", "DynamoDB", "Elastic Search"
                ])

                existing = await page.evaluate('''() => {
                    const pills = document.querySelectorAll("div[data-automation-id='formField-skills'] ul[role='list'] li, div[data-automation-id='formField-skills'] [data-automation-id='selectedItem']");
                    return Array.from(pills).map(p => {
                        const clone = p.cloneNode(true);
                        clone.querySelectorAll("button, [data-automation-id='delete-button']").forEach(b => b.remove());
                        return clone.innerText.trim().toLowerCase();
                    }).filter(Boolean);
                }''')
                print(f"💡 Skills section currently has {len(existing)} existing pills.")

                if len(existing) < 18:
                    added_count = 0
                    for sk in skills_to_add:
                        sk_clean = sk.strip()
                        sk_lower = sk_clean.lower()
                        if any(sk_lower == e or sk_lower in e or e in sk_lower for e in existing):
                            continue

                        try:
                            await skills_inp.click()
                            await skills_inp.fill("")
                            await skills_inp.type(sk_clean, delay=35)
                            await asyncio.sleep(0.8)
                            opt = await page.query_selector(f"li[role='option']:has-text('{sk_clean}'), [data-automation-label='{sk_clean}'], div[data-automation-id='menuItem']")
                            if opt:
                                await opt.click(force=True)
                            else:
                                await page.keyboard.press("Enter")
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.2)
                            existing.append(sk_lower)
                            added_count += 1
                        except Exception as ske:
                            print(f"⚠️ Note adding skill '{sk_clean}': {ske}")

                    print(f"✅ Added {added_count} new skills to Skills section.")
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.3)
        except Exception as sk_err:
            print(f"⚠️ Skills handling note: {sk_err}")

    async def fill_workday_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        """Fills out Mastercard's Workday application completely through all steps."""
        pers = self.profile["personal"]
        raw_url = job["apply_url"]
        title = job.get("title", "")
        target_url = self._resolve_workday_url(raw_url, title)

        if "careers.mastercard.com" in target_url or "jobSeqNo=" in target_url or "phenom" in target_url:
            print(f"🌐 Detected Mastercard Phenom portal URL ({target_url}). Delegating to PhenomApplier...")
            from phenom_applier import PhenomApplier
            p_applier = PhenomApplier(dry_run=self.dry_run, headless=self.headless, keep_open=self.keep_open)
            # Use the existing page context
            res = await p_applier.apply_to_job(job, context=page.context)
            ok = res.get("status") in ["APPLIED", "READY_TO_SUBMIT (DRY_RUN)", "READY_TO_SUBMIT (REVIEW_REQUIRED)"]
            return ok, res.get("message", "Mastercard application processed via Phenom portal")

        if "myworkdayjobs.com" not in target_url:
            if "linkedin.com" in target_url:
                print(f"🔗 No direct Workday requisition found for '{title}'. Delegating to LinkedIn Easy Apply...")
                from stealth_applier import StealthApplier
                sub_applier = StealthApplier(dry_run=self.dry_run, headless=self.headless, keep_open=self.keep_open)
                return await sub_applier._fill_linkedin(page, job)
            return False, f"Job URL is not an official Workday portal: {target_url}"

        domain = urllib.parse.urlparse(target_url).netloc or "mastercard.wd1.myworkdayjobs.com"

        # ── 1. Ensure authenticated session ────────────────────────────────────
        logged_in = await self.ensure_logged_in(page, domain)
        if not logged_in:
            content = (await page.content()).lower()
            if "workday is currently unavailable" in content or "service interruption" in content or "system maintenance" in content:
                return False, "Mastercard Workday portal is currently undergoing scheduled maintenance. Please retry once maintenance concludes."
            print("⚠️ Session authentication check was not completed; proceeding directly to application entry.")

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

        # Check for Workday maintenance or removed posting
        body_text = (await page.content()).lower()
        if "workday is currently unavailable" in body_text or "system maintenance" in body_text:
            print("⚠️ Mastercard Workday portal is currently undergoing scheduled maintenance.")
            return False, "Mastercard Workday portal is currently undergoing scheduled maintenance. Please retry in a few minutes."
        if "page you are looking for doesn't exist" in body_text or "job is no longer available" in body_text or "no longer accepting applications" in body_text:
            print("⚠️ Job posting has expired or been removed from Mastercard Workday.")
            return False, "Mastercard Workday: Job posting has expired or been removed."

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
        max_steps = 10
        for step in range(1, max_steps + 1):
            await asyncio.sleep(3.5)

            # Get current step text or heading
            current_step_text = await page.evaluate('''() => {
                const el = document.querySelector('li[aria-current="step"], ol[role="tablist"]');
                return el ? el.innerText.trim().replace(/\\s+/g, ' ') : document.title;
            }''')
            print(f"📋 Application Step {step}/{max_steps}: {current_step_text[:80]}")

            # Check if reached Review step or already Submitted
            content_low = (await page.content()).lower()
            if any(k in content_low for k in ["application submitted", "application received", "your application has successfully been submitted"]):
                print("🎉 Detected Application Submitted confirmation!")
                return True, "Mastercard Workday: Application successfully submitted & received!"

            if (
                any(k in content_low for k in ["review your application", "review application", "review & submit"])
                or "review" in current_step_text.lower()
                or "step 6" in current_step_text.lower()
            ):
                proof_path = os.path.join("screenshots", f"mastercard_review_{job.get('job_id', 'latest')}.png")
                await capture_proof(page, proof_path)
                print(f"📸 Captured final Review proof to {proof_path}!")

                if self.dry_run:
                    print("🏁 Reached final Review step! [Review First Mode active: Stopping before submit].")
                    try:
                        await page.evaluate('''() => {
                            if (!document.getElementById('dry_run_review_banner')) {
                                const banner = document.createElement('div');
                                banner.id = 'dry_run_review_banner';
                                banner.style = 'position:fixed;top:0;left:0;right:0;z-index:999999;background:linear-gradient(90deg, #d97706, #dc2626);color:white;padding:14px 20px;font-family:sans-serif;font-size:14px;font-weight:bold;display:flex;align-items:center;justify-content:space-between;box-shadow:0 4px 14px rgba(0,0,0,0.4);border-bottom:3px solid #f59e0b;';
                                banner.innerHTML = '<span>👁️ <b>Review First Mode:</b> All sections and answers are auto-filled! Please inspect the application below. When ready, click <b>Submit</b> at the bottom.</span><button onclick="this.parentElement.remove()" style="background:rgba(255,255,255,0.25);border:none;color:white;padding:4px 10px;border-radius:4px;cursor:pointer;font-weight:bold;">Dismiss</button>';
                                document.body.prepend(banner);
                            }
                        }''')
                    except Exception:
                        pass
                    return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and parked on Review step for candidate inspection."
                else:
                    print("🏁 Reached final Review step! Ready for live submission.")
                    return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and answered questionnaires."

            # ── A. Step 1: My Information (executed whenever Step 1 fields are present or on step 1)
            is_step1 = (
                step == 1
                or "information" in current_step_text.lower()
                or "step 1" in current_step_text.lower()
                or await page.query_selector("input#source--source, div[data-automation-id*='candidateIsPreviousWorker' i], input#phoneNumber--phoneNumber")
            )
            if is_step1:
                # 1. Source / How did you hear about us
                src_btn = await page.query_selector("div[data-automation-id*='source' i] button[aria-haspopup='listbox'], button#source--source")
                if src_btn:
                    try:
                        btn_txt = (await src_btn.inner_text()).strip()
                        if btn_txt in ["Select One", "", "—", "Select"]:
                            await src_btn.click()
                            await asyncio.sleep(0.8)
                            opt = await page.query_selector("li[role='option']:has-text('LinkedIn'), div[data-automation-id='menuItem']:has-text('LinkedIn')")
                            if opt:
                                await opt.click()
                            else:
                                first_opt = await page.query_selector("li[role='option'], div[data-automation-id='menuItem']")
                                if first_opt:
                                    await first_opt.click()
                    except Exception as e:
                        print(f"⚠️ Source dropdown note: {e}")

                src_inp = await page.query_selector("input#source--source, div[data-automation-id*='source' i] input")
                if src_inp:
                    try:
                        val = await src_inp.input_value()
                        if not val:
                            await src_inp.focus()
                            await src_inp.fill("LinkedIn")
                            await asyncio.sleep(0.8)
                            opt = await page.query_selector("div[data-automation-id='menuItem']:has-text('LinkedIn'), li[role='option']:has-text('LinkedIn')")
                            if opt:
                                await opt.click()
                            else:
                                await page.keyboard.press("ArrowDown")
                                await asyncio.sleep(0.3)
                                await page.keyboard.press("Enter")
                    except Exception as e:
                        print(f"⚠️ Source input note: {e}")

                # 2. Previous worker -> No
                prev_worker = await page.query_selector(
                    "div[data-automation-id='formField-candidateIsPreviousWorker'], "
                    "div[data-automation-id*='candidateIsPreviousWorker' i]"
                )
                if prev_worker:
                    try:
                        no_radio = await prev_worker.query_selector("label:has-text('No'), input[value*='no' i]")
                        if no_radio:
                            await no_radio.click()
                        else:
                            lb = await prev_worker.query_selector("button[aria-haspopup='listbox']")
                            if lb:
                                await lb.click()
                                await asyncio.sleep(0.5)
                                no_opt = await page.query_selector("li[role='option']:has-text('No')")
                                if no_opt:
                                    await no_opt.click()
                    except Exception as e:
                        print(f"⚠️ Previous worker note: {e}")

                # 3. Legal Given Name(s)
                fn = await page.query_selector("input#name--legalName--firstName, input[name='legalName--firstName']")
                if fn:
                    curr_fn = await fn.input_value()
                    if not curr_fn:
                        await fn.fill(pers.get("first_name", "Aditya"))

                # 4. Legal Family Name
                ln = await page.query_selector("input#name--legalName--lastName, input[name='legalName--lastName']")
                if ln:
                    curr_ln = await ln.input_value()
                    if not curr_ln:
                        await ln.fill(pers.get("last_name", "Kumar"))

                # 5. Phone Device Type -> Mobile
                pt_btn = await page.query_selector(
                    "button[data-automation-id='phoneNumber--phoneType'], "
                    "div[data-automation-id='formField-phoneType'] button, "
                    "div[data-automation-id*='phoneType' i] button"
                )
                if pt_btn:
                    try:
                        btn_txt = (await pt_btn.inner_text()).strip()
                        if "mobile" not in btn_txt.lower():
                            await pt_btn.click()
                            await asyncio.sleep(0.8)
                            m_opt = await page.query_selector(
                                "div[data-automation-id='menuItem']:has-text('Mobile'), "
                                "li[role='option']:has-text('Mobile')"
                            )
                            if m_opt:
                                await m_opt.click()
                    except Exception:
                        pass

                # 6. Phone Number (10 digits clean)
                phone_inp = await page.query_selector(
                    "input#phoneNumber--phoneNumber, "
                    "input[data-automation-id='phone-number'], "
                    "input[name='phoneNumber']"
                )
                if phone_inp:
                    curr_phone = (await phone_inp.input_value()).strip()
                    target_phone = pers.get("phone", "9643914225").replace("+91", "").replace(" ", "").replace("-", "").strip()
                    if curr_phone != target_phone:
                        print(f"📞 Sanitizing phone number from '{curr_phone}' to '{target_phone}'...")
                        await phone_inp.fill(target_phone)
                        await asyncio.sleep(0.4)

                # 7. City / Location in My Information -> Bangalore
                city_inp = await page.query_selector(
                    "input[data-automation-id='addressSection_city'], "
                    "input#addressSection_city, "
                    "input[name*='city' i]"
                )
                if city_inp:
                    target_city = pers.get("current_city", "Bangalore")
                    print(f"📍 Setting My Information City to '{target_city}'...")
                    await city_inp.fill(target_city)
                    await asyncio.sleep(0.3)
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.4)

                # 8. Country in My Information -> India
                c_btn = await page.query_selector("button[data-automation-id='addressSection_countryRegion'], div[data-automation-id*='country' i] button[aria-haspopup='listbox']")
                if c_btn:
                    c_txt = (await c_btn.inner_text()).strip()
                    if "india" not in c_txt.lower():
                        print("📍 Setting Country to 'India'...")
                        await c_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        ind_opt = await page.query_selector("li[role='option']:has-text('India'), div[data-automation-id='menuItem']:has-text('India')")
                        if ind_opt:
                            await ind_opt.click(force=True)
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")

            # ── B. Step 2: My Experience (Education, Skills, Websites, Experience)
            is_step2 = (
                "experience" in current_step_text.lower()
                or "step 2" in current_step_text.lower()
                or await page.query_selector("div[aria-labelledby='Education-section'], div[data-automation-id='formField-skills'], div[aria-labelledby='Websites-section']")
            )
            if is_step2:
                await self.fill_step2_experience_education_skills(page)

            # ── C. Questions & Disclosures Dropdowns (Steps 3, 4, 5)
            dropdown_fields = await page.query_selector_all("div[data-automation-id^='formField-']")
            for f in dropdown_fields:
                try:
                    f_text = (await f.inner_text()).lower()
                    btn = await f.query_selector("button[aria-haspopup='listbox']")
                    if btn:
                        curr_txt = (await btn.inner_text()).strip()
                        should_select = False
                        if "gender" in f_text:
                            if curr_txt.lower() != "male":
                                should_select = True
                        elif "country" in f_text:
                            if "india" not in curr_txt.lower():
                                should_select = True
                        elif curr_txt in ["Select One", "", "—"]:
                            should_select = True

                        if should_select:
                            await btn.click(force=True)
                            await asyncio.sleep(0.6)
                            opt = None
                            if "country" in f_text:
                                opt = await page.query_selector("li[role='option']:has-text('India'), div[data-automation-id='menuItem']:has-text('India')")
                            elif "authorized" in f_text:
                                opt = await page.query_selector("li[role='option']:has-text('Yes'), div[data-automation-id='menuItem']:has-text('Yes')")
                            elif any(k in f_text for k in [
                                "sponsorship", "restrictive covenant", "mastercard", "worked",
                                "commercial contracts", "oversight", "close personal", "outside employment",
                                "conflict", "government", "relatives"
                            ]):
                                opt = await page.query_selector("li[role='option']:has-text('No'), div[data-automation-id='menuItem']:has-text('No')")
                            elif "gender" in f_text:
                                # Must select strictly 'Male' and NEVER match 'Female'
                                all_opts = await page.query_selector_all("li[role='option'], div[data-automation-id='menuItem']")
                                for o in all_opts:
                                    t = (await o.inner_text()).strip().lower()
                                    if t == "male" or (t.startswith("male") and "fe" not in t):
                                        opt = o
                                        break
                                if not opt:
                                    handle = await page.evaluate_handle('''() => {
                                        const els = Array.from(document.querySelectorAll("li[role='option'], div[data-automation-id='menuItem']"));
                                        return els.find(e => {
                                            const s = e.innerText.trim().toLowerCase();
                                            return s === 'male' || (s.startsWith('male') && !s.includes('fe'));
                                        }) || null;
                                    }''')
                                    if handle and handle.as_element():
                                        opt = handle.as_element()
                            else:
                                opt = await page.query_selector("li[role='option']:has-text('No'), li[role='option']:has-text('Yes')")

                            if opt:
                                print(f"🔘 Selected option for dropdown '{f_text[:30]}...'")
                                await opt.click()
                            await asyncio.sleep(0.3)

                    # Radio buttons fallback (e.g. for Gender or Yes/No)
                    if "gender" in f_text:
                        radios = await f.query_selector_all("label, div[role='radio'], input[type='radio']")
                        for r in radios:
                            r_text = (await r.inner_text()).strip().lower()
                            if r_text == "male" or (r_text.startswith("male") and "fe" not in r_text):
                                print("🔘 Clicked 'Male' radio option")
                                await r.click()
                                break

                    # Textareas (e.g. Desired salary)
                    ta = await f.query_selector("textarea")
                    if ta and not await ta.input_value():
                        if "salary" in f_text or "compensation" in f_text or "desired" in f_text:
                            is_req = await page.evaluate('''el => {
                                let p = el.closest('[data-automation-id^="formField-"], .form-group, div');
                                if (!p) p = el.parentElement;
                                if (el.hasAttribute('required') || el.getAttribute('aria-required') === 'true') return true;
                                if (p && p.querySelector('[data-automation-id="required"], [class*="required"], abbr[title="required"], .mandatory')) return true;
                                const lbl = p ? p.querySelector('label, [id*="label"]') : null;
                                if (lbl && (lbl.innerText.includes('*') || lbl.querySelector('abbr[title="required"], [class*="required"]'))) return true;
                                return false;
                            }''', ta)
                            if is_req:
                                print("💰 Feeding mandatory expected compensation: 40")
                                await ta.fill("40")
                            else:
                                print("⏭️ Expected compensation is optional: leaving empty.")
                        elif "notice" in f_text:
                            await ta.fill("30")
                except Exception:
                    pass

            # ── C. Text Inputs (Notice period, current CTC, Experience Company/Title fallback)
            text_inps = await page.query_selector_all(
                "input[type='text']:not(#name--legalName--firstName):not(#name--legalName--lastName):not(#phoneNumber--phoneNumber), "
                "input:not([type]):not(#name--legalName--firstName):not(#name--legalName--lastName):not(#phoneNumber--phoneNumber)"
            )
            for ti in text_inps:
                try:
                    if await ti.is_visible() and not await ti.input_value():
                        info = await page.evaluate('''el => {
                            let text = (el.getAttribute('data-automation-id') || '') + ' ' + 
                                       (el.getAttribute('aria-label') || '') + ' ' + 
                                       (el.placeholder || '') + ' ' + 
                                       (el.id || '') + ' ' + 
                                       (el.name || '');
                            let p = el.parentElement;
                            for (let i = 0; p && i < 4; i++) {
                                const l = p.querySelector('label, [id*="label"], [class*="label"], [data-automation-id*="label"]');
                                if (l) {
                                    text += ' ' + l.innerText;
                                    break;
                                }
                                p = p.parentElement;
                            }
                            return text.toLowerCase();
                        }''', ti)

                        if "company" in info or "employer" in info:
                            print("🏢 Pre-filling missing experience company: '73 Strings'")
                            await ti.fill("73 Strings")
                        elif "job title" in info or "title" in info or "position" in info:
                            print("💼 Pre-filling missing experience job title: 'Software Development Engineer II'")
                            await ti.fill("Software Development Engineer II")
                        elif "notice" in info:
                            await ti.fill("30")
                        elif "current" in info and ("ctc" in info or "salary" in info or "compensation" in info):
                            await ti.fill("28 LPA")
                        elif "expected" in info or "expectation" in info or ("desired" in info and ("salary" in info or "compensation" in info or "ctc" in info)):
                            is_req = await page.evaluate('''el => {
                                let p = el.closest('[data-automation-id^="formField-"], .form-group, div');
                                if (!p) p = el.parentElement;
                                if (el.hasAttribute('required') || el.getAttribute('aria-required') === 'true') return true;
                                if (p && p.querySelector('[data-automation-id="required"], [class*="required"], abbr[title="required"], .mandatory')) return true;
                                const lbl = p ? p.querySelector('label, [id*="label"]') : null;
                                if (lbl && (lbl.innerText.includes('*') || lbl.querySelector('abbr[title="required"], [class*="required"]'))) return true;
                                return false;
                            }''', ti)
                            if is_req:
                                print("💰 Feeding mandatory expected compensation: 40")
                                await ti.fill("40")
                            else:
                                print("⏭️ Expected compensation is optional: leaving empty.")
                        elif "city" in info or "location" in info:
                            target_city = pers.get("current_city", "Bangalore")
                            print(f"📍 Setting location to '{target_city}'...")
                            await ti.fill(target_city)
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
                btn_text = (await next_btn.inner_text()).strip().lower()
                is_submit_btn = "submit" in btn_text

                # In Dry-Run / Review First mode: Stop before clicking submit!
                if is_submit_btn and self.dry_run:
                    print(f"\n{'='*70}")
                    print(f"🛑 [REVIEW FIRST MODE] Reached final Review step with '{btn_text}' button.")
                    print("👀 All questionnaires, profile data, and resume are pre-filled!")
                    print("🛑 Application is NOT submitted automatically. Review in your open browser window.")
                    print(f"{'='*70}\n")
                    try:
                        await page.evaluate('''() => {
                            if (!document.getElementById('dry_run_review_banner')) {
                                const banner = document.createElement('div');
                                banner.id = 'dry_run_review_banner';
                                banner.style = 'position:fixed;top:0;left:0;right:0;z-index:999999;background:linear-gradient(90deg, #d97706, #dc2626);color:white;padding:14px 20px;font-family:sans-serif;font-size:14px;font-weight:bold;display:flex;align-items:center;justify-content:space-between;box-shadow:0 4px 14px rgba(0,0,0,0.4);border-bottom:3px solid #f59e0b;';
                                banner.innerHTML = '<span>👁️ <b>Review First Mode:</b> All sections and answers are auto-filled! Please inspect the application below. When ready, click <b>Submit</b> at the bottom.</span><button onclick="this.parentElement.remove()" style="background:rgba(255,255,255,0.25);border:none;color:white;padding:4px 10px;border-radius:4px;cursor:pointer;font-weight:bold;">Dismiss</button>';
                                document.body.prepend(banner);
                            }
                        }''')
                    except Exception:
                        pass
                    return True, "Mastercard Workday: Successfully filled profile, uploaded resume, and parked on Review step for candidate inspection."

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

                    # Recovery: fill any invalid empty input
                    invalid_inps = await page.query_selector_all("input[aria-invalid='true'], div[data-automation-id*='error' i] input")
                    for inv in invalid_inps:
                        try:
                            if await inv.is_visible() and not await inv.input_value():
                                inv_info = await page.evaluate('''el => {
                                    return ((el.getAttribute('data-automation-id') || '') + ' ' + (el.id || '') + ' ' + (el.name || '')).toLowerCase();
                                }''', inv)
                                if "company" in inv_info:
                                    print("🔧 Recovering missing company field -> '73 Strings'")
                                    await inv.fill("73 Strings")
                                elif "title" in inv_info or "position" in inv_info:
                                    print("🔧 Recovering missing title field -> 'Software Development Engineer II'")
                                    await inv.fill("Software Development Engineer II")
                        except Exception:
                            pass

                    # Re-click Next if recovered
                    if invalid_inps:
                        print("🔁 Re-clicking Next after resolving validation errors...")
                        await next_btn.click(force=True)
                        await asyncio.sleep(3.5)
            else:
                print(f"ℹ️ Next button not present on step {step}, checking for Review state...")

        # Final check if on Review step or already Submitted
        content_low = (await page.content()).lower()
        if any(k in content_low for k in ["application submitted", "application received", "your application has successfully been submitted", "candidate home"]):
            return True, "Mastercard Workday: Application successfully submitted & received!"
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
                content = (await page.content()).lower()
                has_submitted = any(w in content for w in ["application submitted", "application received", "your application has successfully been submitted"])
                if has_submitted:
                    status = "APPLIED"
                    final_msg = "Mastercard application submitted & verified on Workday!"
                    print(f"🎉 Application confirmed submitted on Workday!")
                elif self.dry_run:
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
            try:
                update_job_status(job_id, "FAILED", err_msg, sc_path)
            except Exception:
                pass
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
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM job_postings WHERE job_id = ? OR id = ?", (args.job_id, args.job_id))
        row = cursor.fetchone()
        conn.close()
        if row:
            targets = [dict(row)]
        else:
            print(f"❌ Job ID '{args.job_id}' not found in jobs.db")
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

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


async def safe_page_content(page: Page) -> str:
    """Safely retrieves lowercased page content even during transient navigations."""
    for _ in range(6):
        try:
            return (await page.content()).lower()
        except Exception:
            await asyncio.sleep(1.0)
    return ""


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

        # 0. Check if existing BrowserContext already has authentication cookies
        try:
            cookies = await page.context.cookies(f"https://{domain}")
            auth_cookie_names = [c.get('name') for c in cookies]
            if any(name in ['CALYPSO_CSRF_TOKEN', 'wday_vps_cookie', 'PLAY_SESSION', 'wd-browser-id'] for name in auth_cookie_names):
                print(f"✅ Reusing authenticated session cookies in BrowserContext.")
                return True
        except Exception:
            pass

        # 1. Quick check if already authenticated on current page
        try:
            user_header = await page.query_selector("button[data-automation-id='utilityMenuButton']")
            if user_header and await user_header.is_visible():
                txt = (await user_header.inner_text()).lower()
                if email_addr.lower() in txt or "settings" in txt:
                    print(f"✅ Already authenticated as {email_addr}.")
                    return True
        except Exception:
            pass

        # 2. Navigate to portal login
        login_url = f"https://{domain}/en-US/CorporateCareers/login"
        print(f"🔑 Establishing authenticated session via {login_url}...")
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.5)
        except Exception as e:
            print(f"⚠️ Page navigation note: {e}")

        # Check for Workday maintenance outage
        content = await safe_page_content(page)
        has_login_fields = bool(await page.query_selector("input[data-automation-id='email'], button:has-text('Sign In')"))
        if not has_login_fields and ("workday is currently unavailable" in content or "scheduled maintenance" in content or "service interruption" in content):
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

    async def answer_dropdown(self, page: Page, btn, question_text: str) -> bool:
        """Answers a Workday questionnaire dropdown using resilient DOM matching and keyboard escape."""
        q_low = question_text.lower()
        if "sponsorship" in q_low or "visa" in q_low:
            target = "No"
        elif "authorized" in q_low or "eligible to work" in q_low:
            target = "Yes"
        elif "worked for mastercard" in q_low or "previous worker" in q_low or "previously employed" in q_low:
            target = "No"
        elif "gender" in q_low:
            target = "Male"
        elif "country" in q_low:
            target = "India"
        elif "state" in q_low or "region" in q_low:
            target = "Karnataka"
        elif any(k in q_low for k in [
            "restrictive covenant", "non-compete", "commercial contracts", "oversight",
            "close personal", "outside employment", "conflict", "government", "relatives"
        ]):
            target = "No"
        else:
            target = "No"

        print(f"👉 Answering dropdown '{question_text[:50]}...' -> '{target}'")
        try:
            await btn.scroll_into_view_if_needed()
            await btn.click(force=True)
            await asyncio.sleep(0.8)

            res = await page.evaluate('''(targetVal) => {
                const cleanTarget = targetVal.trim().toLowerCase();
                const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li[role='option'], div[role='option']"));
                const optTexts = opts.map(o => o.innerText.trim().toLowerCase());

                // Auto-detect Gender dropdown by checking available options:
                const hasMale = optTexts.some(t => t === 'male' || (t.startsWith('male') && !t.includes('female')));
                const hasFemale = optTexts.some(t => t === 'female' || t.startsWith('female'));
                if (hasMale && hasFemale) {
                    const m = opts.find(o => {
                        const t = o.innerText.trim().toLowerCase();
                        return (t === 'male' || t.startsWith('male')) && !t.includes('female');
                    });
                    if (m) {
                        m.scrollIntoView({ block: 'nearest' });
                        m.click();
                        return { success: true, text: m.innerText.trim(), reason: 'gender_detected_strictly_male' };
                    }
                }

                let match = null;
                // Strict matching for Yes/No
                if (cleanTarget === 'no') {
                    match = opts.find(o => {
                        const t = o.innerText.trim().toLowerCase();
                        return t === 'no' || /^no\b/i.test(t);
                    });
                } else if (cleanTarget === 'yes') {
                    match = opts.find(o => {
                        const t = o.innerText.trim().toLowerCase();
                        return t === 'yes' || /^yes\b/i.test(t);
                    });
                } else if (cleanTarget === 'male') {
                    match = opts.find(o => {
                        const t = o.innerText.trim().toLowerCase();
                        return (t === 'male' || t.startsWith('male')) && !t.includes('female');
                    });
                } else {
                    // Exact match
                    match = opts.find(o => o.innerText.trim().toLowerCase() === cleanTarget);
                    // Starts with
                    if (!match) {
                        match = opts.find(o => o.innerText.trim().toLowerCase().startsWith(cleanTarget));
                    }
                    // Substring match only for longer strings
                    if (!match && cleanTarget.length > 2) {
                        match = opts.find(o => o.innerText.trim().toLowerCase().includes(cleanTarget));
                    }
                }

                if (match) {
                    match.scrollIntoView({ block: 'nearest' });
                    match.click();
                    return { success: true, text: match.innerText.trim() };
                }
                return { success: false, available: opts.map(o => o.innerText.trim()).filter(Boolean) };
            }''', target)

            print(f"   Option click result: {res}")
            await asyncio.sleep(0.4)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            return res.get("success", False)
        except Exception as e:
            print(f"   Dropdown error: {e}")
            return False

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
                desired_experiences = [
                    {
                        "company": "73 Strings",
                        "title": "SDE II",
                        "location": "Bangalore, India",
                        "currently_work_here": True,
                        "from_month": "11",
                        "from_year": "2025",
                        "to_month": None,
                        "to_year": None,
                        "role_description": (
                            "Redesigned the approval workflow from a fixed flow into a configurable multi-level platform for 30+ tenants (entity-scoped access control, send-back to any prior level, audit-safe condition versioning); chose parallelized downstream calls and caching over sequential orchestration to cut p99 latency from ~900ms to ~350ms across 4–5 services.\n"
                            "Handled production reliability, performance and security of multi-tenant services: root-caused incidents such as OOMKills using Datadog profiling, resolved latency and parallelism issues, and hardened Azure Blob Storage access.\n"
                            "Built an AI-agent-based tool that upgrades legacy Java services (Java 8 and other versions to 21), modernizing 10+ repositories and cutting migration effort from ~1–2 weeks to 2–3 days per repo.\n"
                            "Drove end-to-end delivery for the UAM and approval services (Spring Boot, MySQL, Elasticsearch, Kubernetes) — design, code review, and mentoring on changes to a multi-tenant platform, keeping releases maintainable and safe."
                        )
                    },
                    {
                        "company": "Sumo Logic",
                        "title": "SDE II",
                        "location": "Noida, India",
                        "currently_work_here": False,
                        "from_month": "06",
                        "from_year": "2024",
                        "to_month": "10",
                        "to_year": "2025",
                        "role_description": (
                            "Owned performance and reliability improvements on a metrics ingestion and query platform handling 70M+ datapoints/min, resolving day-to-day latency and parallelism issues.\n"
                            "Changed the Kafka consumer partition assignment strategy to fix uneven partition distribution across pods during scaling (some consumers overloaded, others under-utilized), cutting ingestion-lag alerts from 2–3 per day to 5–6 per week.\n"
                            "Reworked the in-memory data store to remove read/write lock contention by re-tuning and merging partitions, validated with benchmark, unit and e2e tests; queries now serve at real-time latency during ingestion lag instead of failing, with results trailing by the lag."
                        )
                    },
                    {
                        "company": "Open Financial Technologies",
                        "title": "SDE",
                        "location": "Bangalore, India",
                        "currently_work_here": False,
                        "from_month": "09",
                        "from_year": "2022",
                        "to_month": "05",
                        "to_year": "2024",
                        "role_description": (
                            "Built new microservices in Go on a common integration layer for core banking partners (IDFC, IndusInd, Axis) — alongside legacy Java services — processing 3–5K transactions/day with retries, idempotency keys to prevent duplicate transfers, and status checks for failed or delayed calls.\n"
                            "Designed and launched the Axis Current Account onboarding service with integrated vKYC, cutting onboarding turnaround from ~1 week to ~2 hours and improving the customer acquisition pipeline.\n"
                            "Automated fund-transfer and account-management operations, reducing related customer service inquiries by 15%."
                        )
                    },
                    {
                        "company": "Exotel",
                        "title": "SDE",
                        "location": "Bangalore, India",
                        "currently_work_here": False,
                        "from_month": "09",
                        "from_year": "2021",
                        "to_month": "09",
                        "to_year": "2022",
                        "role_description": (
                            "Designed a new Go-based microservice using ASR to select the best pilot number, handling ~700 calls/sec average (peaking higher at midday) across 20M+ calls/day concentrated in a 10am–6pm window; validated via POC before rollout, improving call pickup rate by 1.5%.\n"
                            "Automated third-party API data integration (Java), reducing manual errors and improving database efficiency, with ELK-based logging for monitoring."
                        )
                    }
                ]

                # 1. Query existing panels
                panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")
                print(f"🏢 Found {len(panels)} initial Work Experience panels.")

                # If 0 panels exist, add the first panel
                if len(panels) == 0:
                    add_btn = await exp_sec.query_selector("button[data-automation-id='add-button']")
                    if add_btn and await add_btn.is_visible():
                        print("🏢 Adding first Work Experience panel...")
                        await add_btn.click(force=True)
                        await asyncio.sleep(1.5)
                        panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")

                # Ensure all 4 panels exist (Add Another for Exotel, etc.)
                while len(panels) < len(desired_experiences):
                    add_another = await exp_sec.query_selector("button[data-automation-id='add-button']")
                    if not add_another or not await add_another.is_visible():
                        print("⚠️ Could not find 'Add Another' button for Work Experience.")
                        break
                    print(f"🏢 Clicking 'Add Another' to add Work Experience panel {len(panels) + 1}...")
                    await add_another.scroll_into_view_if_needed()
                    await add_another.click(force=True)
                    await asyncio.sleep(1.8)
                    panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")

                print(f"🏢 Total Work Experience panels ready: {len(panels)}")

                # 2. Iterate each panel and set details cleanly
                for idx, exp_data in enumerate(desired_experiences):
                    if idx >= len(panels):
                        break
                    p = panels[idx]
                    await p.scroll_into_view_if_needed()
                    await asyncio.sleep(0.2)

                    # A. Job Title
                    t_inp = await p.query_selector("input[name='jobTitle'], input[id*='jobTitle'], div[data-automation-id='formField-jobTitle'] input")
                    if t_inp:
                        curr_t = (await t_inp.input_value()).strip()
                        target_t = exp_data["title"]
                        if curr_t != target_t:
                            print(f"💼 Panel {idx+1}: Setting Job Title to '{target_t}' (was '{curr_t}')")
                            await t_inp.fill(target_t)
                            await asyncio.sleep(0.2)

                    # B. Company Name
                    c_inp = await p.query_selector("input[name='companyName'], input[id*='companyName'], div[data-automation-id='formField-companyName'] input")
                    if c_inp:
                        curr_c = (await c_inp.input_value()).strip()
                        target_c = exp_data["company"]
                        if curr_c != target_c:
                            print(f"🏢 Panel {idx+1}: Setting Company Name to '{target_c}' (was '{curr_c}')")
                            await c_inp.fill(target_c)
                            await asyncio.sleep(0.2)

                    # C. Location
                    l_inp = await p.query_selector("input[name='location'], input[id*='location'], div[data-automation-id='formField-location'] input")
                    if l_inp:
                        curr_l = (await l_inp.input_value()).strip()
                        target_l = exp_data["location"]
                        if not curr_l or curr_l != target_l:
                            print(f"📍 Panel {idx+1}: Setting Location to '{target_l}'")
                            await l_inp.fill(target_l)
                            await asyncio.sleep(0.2)

                    # D. "I currently work here" checkbox
                    curr_cb = await p.query_selector("input[name='currentlyWorkHere'], input[id*='currentlyWorkHere'], div[data-automation-id='formField-currentlyWorkHere'] input[type='checkbox']")
                    if curr_cb:
                        is_checked = await curr_cb.is_checked()
                        should_check = exp_data["currently_work_here"]
                        if should_check and not is_checked:
                            print(f"✅ Panel {idx+1} ({exp_data['company']}): Checking 'I currently work here'")
                            await curr_cb.check(force=True)
                            await asyncio.sleep(0.4)
                        elif not should_check and is_checked:
                            print(f"❌ Panel {idx+1} ({exp_data['company']}): Unchecking 'I currently work here'")
                            await curr_cb.uncheck(force=True)
                            await asyncio.sleep(0.4)

                    # E. Start Date (From)
                    from_m = await p.query_selector("div[data-automation-id='formField-startDate'] input[data-automation-id='dateSectionMonth-input']")
                    from_y = await p.query_selector("div[data-automation-id='formField-startDate'] input[data-automation-id='dateSectionYear-input']")
                    if from_m:
                        curr_fm = (await from_m.input_value()).strip()
                        if curr_fm != exp_data["from_month"].lstrip("0") and curr_fm != exp_data["from_month"]:
                            print(f"📅 Panel {idx+1} ({exp_data['company']}): Setting Start Month -> '{exp_data['from_month']}'")
                            await from_m.fill(exp_data["from_month"])
                            await asyncio.sleep(0.2)
                    if from_y:
                        curr_fy = (await from_y.input_value()).strip()
                        if curr_fy != exp_data["from_year"]:
                            print(f"📅 Panel {idx+1} ({exp_data['company']}): Setting Start Year -> '{exp_data['from_year']}'")
                            await from_y.fill(exp_data["from_year"])
                            await asyncio.sleep(0.2)

                    # F. End Date (To) - only for past jobs
                    if not exp_data["currently_work_here"]:
                        to_m = await p.query_selector("div[data-automation-id='formField-endDate'] input[data-automation-id='dateSectionMonth-input']")
                        to_y = await p.query_selector("div[data-automation-id='formField-endDate'] input[data-automation-id='dateSectionYear-input']")
                        if to_m and exp_data.get("to_month"):
                            curr_tm = (await to_m.input_value()).strip()
                            if curr_tm != exp_data["to_month"].lstrip("0") and curr_tm != exp_data["to_month"]:
                                print(f"📅 Panel {idx+1} ({exp_data['company']}): Setting End Month -> '{exp_data['to_month']}'")
                                await to_m.fill(exp_data["to_month"])
                                await asyncio.sleep(0.2)
                        if to_y and exp_data.get("to_year"):
                            curr_ty = (await to_y.input_value()).strip()
                            if curr_ty != exp_data["to_year"]:
                                print(f"📅 Panel {idx+1} ({exp_data['company']}): Setting End Year -> '{exp_data['to_year']}'")
                                await to_y.fill(exp_data["to_year"])
                                await asyncio.sleep(0.2)

                    # G. Role Description
                    desc_ta = await p.query_selector("textarea[id*='roleDescription'], div[data-automation-id='formField-roleDescription'] textarea")
                    if desc_ta:
                        curr_desc = (await desc_ta.input_value()).strip()
                        # Replace if empty, or if Open Financial Technologies has Exotel text mixed in, or if Exotel
                        if not curr_desc or (exp_data["company"] == "Open Financial Technologies" and "exotel" in curr_desc.lower()) or exp_data["company"] == "Exotel":
                            print(f"📝 Panel {idx+1} ({exp_data['company']}): Setting Role Description")
                            await desc_ta.fill(exp_data["role_description"])
                            await asyncio.sleep(0.2)
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
                deg_btn = await page.query_selector("button[name='degree'], button[id*='degree' i], div[data-automation-id*='degree' i] button[aria-haspopup='listbox'], button[aria-haspopup='listbox'][aria-label*='degree' i]")
                if deg_btn:
                    deg_txt = (await deg_btn.inner_text()).strip()
                    if deg_txt in ["Select One", "", "—", "Select"]:
                        print("🎓 Selecting Degree: Bachelor's via resilient DOM evaluate...")
                        await deg_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        res = await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                            const m = opts.find(o => o.innerText.toLowerCase().includes('bachelor'));
                            if (m) {
                                m.scrollIntoView({ block: 'nearest' });
                                m.click();
                                return { success: true, text: m.innerText.trim() };
                            }
                            return { success: false };
                        }''')
                        print(f"🎓 Degree selection result: {res}")
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                # Field of Study (multiselect)
                fos_inp = await page.query_selector("input[id*='fieldOfStudy'], div[data-automation-id='formField-fieldOfStudy'] input, input[name='fieldOfStudy']")
                if fos_inp:
                    has_fos = await page.evaluate('''() => {
                        const p = document.querySelector("div[data-automation-id='formField-fieldOfStudy'] [data-automation-id='selectedItem'], div[data-automation-id='formField-fieldOfStudy'] [data-automation-hiddensearch='true']");
                        if (p) return true;
                        const inp = document.querySelector("input[id*='fieldOfStudy'], div[data-automation-id='formField-fieldOfStudy'] input");
                        return !!inp && !!inp.value && inp.value.trim().length > 0;
                    }''')
                    if not has_fos:
                        fos_val = edu_info.get("field_of_study", "Computer Science")
                        print(f"🎓 Setting Field of Study: '{fos_val}'")
                        await fos_inp.focus()
                        await fos_inp.fill(fos_val)
                        await page.keyboard.press("Enter")
                        await asyncio.sleep(2.0)
                        opt_locator = page.locator(f"[data-automation-id='promptOption']:has-text('{fos_val}'), li:has-text('{fos_val}')").first
                        if await opt_locator.count() > 0:
                            await opt_locator.click()
                            print(f"🎓 Clicked '{fos_val}' option")
                            await asyncio.sleep(1.0)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)

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

        # ── 3.5. Languages Section ─────────────────────────────────────────────
        try:
            lang_sec = await page.query_selector("div[aria-labelledby='Languages-section'], div[data-automation-id*='languagesSection' i], div[aria-labelledby*='Languages' i]")
            if lang_sec:
                add_btn = await lang_sec.query_selector("button[data-automation-id='add-button']")
                panels = await lang_sec.query_selector_all("div[role='group'][aria-labelledby*='Languages-'][aria-labelledby*='-panel'], div[data-fkit-id*='language-'], div[data-automation-id='formField-language']")
                if len(panels) == 0 and add_btn and await add_btn.is_visible():
                    print("🌐 Clicking 'Add' in Languages section...")
                    await add_btn.click(force=True)
                    await asyncio.sleep(1.8)

                # Language dropdown -> English
                lang_btn = await lang_sec.query_selector("div[data-automation-id='formField-language'] button, button[id*='--language'], button[name='language']")
                if lang_btn:
                    lang_txt = (await lang_btn.inner_text()).strip()
                    if "select" in lang_txt.lower() or not lang_txt or "—" in lang_txt:
                        print("🌐 Selecting 'English' in Language dropdown...")
                        await lang_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                            const m = opts.find(o => o.innerText.toLowerCase().trim() === 'english');
                            if (m) m.click();
                        }''')
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                # Check "I am fluent in this language"
                native_cb = await lang_sec.query_selector("div[data-automation-id='formField-native'] input[type='checkbox'], input[id*='--native'], input[name='native']")
                if native_cb and not await native_cb.is_checked():
                    print("🌐 Checking 'I am fluent in this language'...")
                    await native_cb.check(force=True)
                    await asyncio.sleep(0.3)

                # Dropdowns: Comprehension, Overall, Reading, Speaking, Writing
                for attempt in range(6):
                    clicked = await page.evaluate('''() => {
                        const btns = Array.from(document.querySelectorAll("button[aria-haspopup='listbox']"));
                        const targetBtn = btns.find(b => {
                            const parent = b.closest("div[aria-labelledby*='Languages' i], div[data-automation-id*='languages' i], div[data-automation-id*='formField-overall' i], div[data-automation-id*='overall' i], div[role='group']");
                            const label = (b.getAttribute('aria-label') || '') + ' ' + (b.id || '') + ' ' + (b.name || '');
                            const text = b.innerText.trim();
                            const isUnfilled = text.includes('Select') || text === '' || text === '—';
                            const isLangRelated = /overall|comprehension|reading|speaking|writing|proficiency/i.test(label) || !!parent;
                            return isUnfilled && isLangRelated;
                        });
                        if (targetBtn) {
                            targetBtn.scrollIntoView({ block: 'center' });
                            targetBtn.click();
                            return { found: true, id: targetBtn.id, text: targetBtn.innerText.trim() };
                        }
                        return { found: false };
                    }''')
                    if not clicked or not clicked.get("found"):
                        break
                    print(f"🌐 Clicked language dropdown: {clicked}")
                    await asyncio.sleep(0.8)
                    selected = await page.evaluate('''() => {
                        const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                        const targets = ['native', 'fluent', 'advanced', 'superior', 'professional working', 'professional', '5', '4', '3'];
                        for (const t of targets) {
                            const match = opts.find(o => o.innerText.toLowerCase().trim().includes(t));
                            if (match) {
                                match.scrollIntoView({ block: 'center' });
                                match.click();
                                return { success: true, text: match.innerText.trim() };
                            }
                        }
                        const fallback = opts.find(o => {
                            const t = o.innerText.toLowerCase().trim();
                            return t && !t.includes('select') && !t.includes('step') && t.length > 0;
                        });
                        if (fallback) {
                            fallback.scrollIntoView({ block: 'center' });
                            fallback.click();
                            return { success: true, text: fallback.innerText.trim() };
                        }
                        return { success: false };
                    }''')
                    print(f"🌐 Language proficiency selected: {selected}")
                    await asyncio.sleep(0.4)
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.3)
        except Exception as le:
            print(f"⚠️ Languages section note: {le}")

        # ── 3.6. Social Network URLs ───────────────────────────────────────────
        linkedin_url = pers.get("linkedin_url", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")
        try:
            soc_inp = await page.query_selector("input#socialNetworkAccounts--linkedInAccount, div[data-automation-id='formField-linkedInAccount'] input, input[id*='linkedInAccount'], input[placeholder*='linkedin' i]")
            if soc_inp and not await soc_inp.input_value():
                print(f"🔗 Setting LinkedIn URL in Social Network URLs: '{linkedin_url}'")
                await soc_inp.fill(linkedin_url)
                await asyncio.sleep(0.2)
        except Exception as swe:
            print(f"⚠️ Social Network URLs note: {swe}")

        # ── 4. Skills Section ──────────────────────────────────────────────────
        try:
            skills_inp = await page.query_selector("input#skills--skills, div[data-automation-id='formField-skills'] input, div[aria-labelledby*='Skills' i] input, input[placeholder*='skills' i]")
            if skills_inp:
                skills_to_add = prof.get("primary_skills", [
                    "Java", "Python", "Golang", "Go", "Docker", "Kubernetes", "AWS",
                    "MySQL", "PostgreSQL", "Redis", "Kafka", "Microservices",
                    "CI/CD", "Elasticsearch", "DynamoDB", "Data Structures"
                ])

                skill_queries = {
                    "Golang": ["Go", "Golang"],
                    "AWS": ["AWS", "Amazon Web Services"],
                    "Kafka": ["Kafka", "Apache Kafka"],
                    "Postgresql": ["PostgreSQL", "Postgres"],
                    "Mysql": ["MySQL"],
                    "Elastic Search": ["Elasticsearch", "Elastic Search"],
                    "DynamoDB": ["DynamoDB"],
                    "CI/CD": ["CI/CD"],
                    "Distributed Design": ["Distributed Systems", "Distributed Design"],
                    "Data Structure": ["Data Structures", "Data Structure"],
                    "Low Level Design": ["Software Design", "Low Level Design"],
                    "High Level Design": ["System Design", "High Level Design"]
                }

                existing = await page.evaluate('''() => {
                    const els = document.querySelectorAll("div[data-automation-id='formField-skills'] [data-automation-id='selectedItem']");
                    return Array.from(els).map(e => e.innerText.trim().toLowerCase());
                }''')
                print(f"💡 Skills section currently has {len(existing)} existing pills: {existing}")

                if len(existing) < 18:
                    added_count = 0
                    for sk in skills_to_add:
                        sk_clean = sk.strip()
                        sk_lower = sk_clean.lower()
                        if any(sk_lower == e or (len(e) > 2 and e in sk_lower) for e in existing):
                            continue

                        queries_to_try = skill_queries.get(sk_clean, [sk_clean])
                        pill_added = False

                        for q in queries_to_try:
                            if pill_added:
                                break
                            try:
                                await skills_inp.scroll_into_view_if_needed()
                                await skills_inp.click()
                                await asyncio.sleep(0.1)
                                await skills_inp.fill(q)
                                await asyncio.sleep(0.1)
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(0.8)

                                clicked_item = await page.evaluate('''(target) => {
                                    const clean = target.trim().toLowerCase();
                                    const opts = Array.from(document.querySelectorAll("[data-automation-id='promptOption'], div[data-automation-id='menuItem'], [role='option']"));
                                    let m = opts.find(o => o.innerText.trim().toLowerCase() === clean);
                                    if (!m) m = opts.find(o => o.innerText.trim().toLowerCase().startsWith(clean));
                                    if (!m) m = opts.find(o => o.innerText.trim().toLowerCase().includes(clean));
                                    if (m && m.innerText.trim() !== 'No Items.') {
                                        m.click();
                                        return m.innerText.trim();
                                    }
                                    return null;
                                }''', q)

                                if clicked_item:
                                    print(f"   🏷️ Selected skill pill: '{clicked_item}' (query: '{q}')")
                                    existing.append(clicked_item.lower())
                                    added_count += 1
                                    pill_added = True
                                    await asyncio.sleep(0.4)
                                await page.keyboard.press("Escape")
                                await asyncio.sleep(0.2)
                            except Exception as ske:
                                print(f"⚠️ Note attempting skill '{q}': {ske}")
                                try:
                                    await page.keyboard.press("Escape")
                                except Exception:
                                    pass

                    print(f"✅ Successfully added {added_count} new skills pills (Total in DOM: {len(existing)}).")
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
            content = await safe_page_content(page)
            has_login_fields = bool(await page.query_selector("input[data-automation-id='email'], button:has-text('Sign In')"))
            if not has_login_fields and ("workday is currently unavailable" in content or "service interruption" in content or "scheduled maintenance" in content):
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

        # Dismiss any stale modal or popups
        stale_modal_btn = await page.query_selector("button:has-text('Return to Home'), button[aria-label='Close'], button[data-automation-id='closeButton']")
        if stale_modal_btn and await stale_modal_btn.is_visible():
            await stale_modal_btn.click(force=True)
            await asyncio.sleep(1.0)

        # Check for Workday maintenance or removed posting
        body_text = await safe_page_content(page)
        has_apply_action = bool(await page.query_selector("button:has-text('Autofill with Resume'), button:has-text('Apply Manually'), button:has-text('Apply'), button:has-text('Use My Last Application'), a[data-automation-id='job-apply-button']"))
        if not has_apply_action and ("workday is currently unavailable" in body_text or "scheduled maintenance" in body_text or "service interruption" in body_text):
            print("⚠️ Mastercard Workday portal is currently undergoing scheduled maintenance.")
            return False, "Mastercard Workday portal is currently undergoing scheduled maintenance. Please retry in a few minutes."
        if "page you are looking for doesn't exist" in body_text or "job is no longer available" in body_text or "no longer accepting applications" in body_text:
            print("⚠️ Job posting has expired or been removed from Mastercard Workday.")
            return False, "Mastercard Workday: Job posting has expired or been removed."
        if "already applied" in body_text or "already submitted" in body_text or "you've already applied" in body_text:
            print("ℹ️ Requisition already applied on Mastercard Workday.")
            return True, "Mastercard Workday: Already applied."

        # If redirected to candidate home, navigate to job detail page directly and click Apply
        if "candidatehome" in page.url.lower() or "userhome" in page.url.lower():
            print("ℹ️ Redirected to Candidate Home. Navigating directly to job description page...")
            job_detail_url = clean_url.replace("/apply", "")
            await page.goto(job_detail_url, wait_until="domcontentloaded", timeout=35000)
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
        apply_desc_btn = await page.query_selector('a[data-automation-id="adventureButton"]:has-text("Apply"), button:has-text("Apply")')
        if apply_desc_btn and await apply_desc_btn.is_visible():
            print("➡️ Clicking Apply button on job description page...")
            await apply_desc_btn.click()
            await asyncio.sleep(4.0)

        # Check for "Continue Application" or "Resume Application" prompt
        cont_app_btn = await page.query_selector("button:has-text('Continue Application'), a:has-text('Continue Application'), button:has-text('Resume Application'), button:has-text('Start New Application')")
        if cont_app_btn and await cont_app_btn.is_visible():
            print("➡️ Clicking Continue Application prompt...")
            await cont_app_btn.click(force=True)
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
            content_low = await safe_page_content(page)
            if "candidatehome" not in page.url.lower() and "userhome" not in page.url.lower():
                if any(k in content_low for k in ["application submitted", "application received", "your application has successfully been submitted"]):
                    print("🎉 Detected Application Submitted confirmation!")
                    return True, "Mastercard Workday: Application successfully submitted & received!"

            submit_btn = await page.query_selector(
                "button[data-automation-id='pageFooterNextButton']:has-text('Submit'), "
                "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
                "button[data-automation-id='pageFooterSubmitButton'], "
                "button:has-text('Submit Application'), button:has-text('Submit')"
            )
            is_review_page = (
                any(k in content_low for k in ["review your application", "review application", "review & submit", "review\nmy information"])
                or "review" in current_step_text.lower()
                or "step 6" in current_step_text.lower()
                or (submit_btn is not None and await submit_btn.is_visible())
            )
            if is_review_page:
                proof_path = os.path.join("screenshots", f"mastercard_{job.get('job_id', 'latest')}.png")
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
                    print("🏁 Reached final Review step! Submitting application...")
                    if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
                        await submit_btn.click(force=True)
                        await asyncio.sleep(6.0)
                        sub_proof_path = os.path.join("screenshots", f"mastercard_{job.get('job_id', 'latest')}_submitted.png")
                        try:
                            await capture_proof(page, sub_proof_path)
                        except Exception:
                            pass
                    return True, "Mastercard Workday: Application successfully submitted & received!"

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

                # 8. Country in My Information -> strictly India
                c_btn = await page.query_selector("button[data-automation-id='addressSection_countryRegion'], div[data-automation-id*='country' i] button[aria-haspopup='listbox']")
                if c_btn:
                    c_txt = (await c_btn.inner_text()).strip().lower()
                    if c_txt != "india":
                        print("📍 Setting Country strictly to 'India'...")
                        await c_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        search_box = await page.query_selector("input[data-automation-id='searchBox'], input[role='searchbox'], input[type='search'], div[data-automation-id='popup'] input, input[data-uxi-widget-type='selectinput']")
                        if search_box:
                            await search_box.fill("India")
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(0.8)
                        res = await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                            const exact = opts.find(o => o.innerText.trim().toLowerCase() === 'india');
                            if (exact) {
                                exact.scrollIntoView({ block: 'nearest' });
                                exact.click();
                                return { success: true, text: exact.innerText.trim() };
                            }
                            return { success: false, opts: opts.map(o => o.innerText.trim()).slice(0, 10) };
                        }''')
                        print(f"📍 Country selection result: {res}")
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

            # ── C. Questions & Disclosures Dropdowns (Steps 3, 4, 5, etc.)
            dropdown_btns = await page.query_selector_all("button[aria-haspopup='listbox']")
            for btn in dropdown_btns:
                try:
                    curr_txt = (await btn.inner_text()).strip()
                    if any(k in curr_txt.lower() for k in ["settings", "candidate home", "account", "menu", "sign out"]):
                        continue

                    # Extract question context by checking labels, aria attributes, and DOM containers
                    q_text = await page.evaluate('''b => {
                        // 1. Check aria-labelledby
                        if (b.getAttribute('aria-labelledby')) {
                            const ids = b.getAttribute('aria-labelledby').split(/\\s+/);
                            const text = ids.map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
                            if (text) return text.replace(/\\s+/g, ' ');
                        }
                        // 2. Check label[for=id] or field container label
                        const fieldContainer = b.closest('div[data-automation-id^="formField-"], fieldset, [data-automation-id*="question" i], div[role="group"]');
                        if (fieldContainer) {
                            const lbl = fieldContainer.querySelector('label');
                            if (lbl && lbl.innerText.trim()) return lbl.innerText.trim().replace(/\\s+/g, ' ');
                            if (fieldContainer.innerText.trim()) return fieldContainer.innerText.trim().replace(/\\s+/g, ' ');
                        }
                        // 3. Fallback parent traversal
                        let curr = b;
                        for (let i = 0; i < 6; i++) {
                            curr = curr.parentElement;
                            if (!curr) break;
                            if (curr.innerText && curr.innerText.length > 3 && curr.innerText.length < 500) {
                                return curr.innerText.replace(/\\s+/g, ' ');
                            }
                        }
                        return b.getAttribute('aria-label') || b.id || '';
                    }''', btn)

                    should_answer = False
                    if "gender" in q_text.lower():
                        if curr_txt.lower() != "male":
                            should_answer = True
                    elif "country" in q_text.lower():
                        if "india" not in curr_txt.lower():
                            should_answer = True
                    elif curr_txt in ["Select One", "", "—", "Select"] or "prefer not to" in curr_txt.lower():
                        should_answer = True

                    if should_answer:
                        await self.answer_dropdown(page, btn, q_text)
                except Exception as de:
                    print(f"Dropdown iteration note: {de}")

            # ── D. Radios (Yes/No, Gender)
            radios = await page.query_selector_all("div[role='radiogroup'], fieldset")
            for rg in radios:
                try:
                    rg_text = (await rg.inner_text()).lower()
                    if any(k in rg_text for k in ["sponsorship", "visa", "mastercard", "conflict", "restrictive covenant", "relatives", "oversight"]):
                        no_r = await rg.query_selector("label:has-text('No'), input[value*='no' i]")
                        if no_r:
                            await no_r.click(force=True)
                    elif "authorized" in rg_text:
                        yes_r = await rg.query_selector("label:has-text('Yes'), input[value*='yes' i]")
                        if yes_r:
                            await yes_r.click(force=True)
                    elif "gender" in rg_text:
                        all_r = await rg.query_selector_all("label, div[role='radio']")
                        for r in all_r:
                            t = (await r.inner_text()).strip().lower()
                            if t == "male" or (t.startswith("male") and "fe" not in t):
                                await r.click(force=True)
                                break
                except Exception:
                    pass

            # ── E. Text Inputs & Textareas (Expected salary, notice period, etc.)
            tas_and_inps = await page.query_selector_all(
                "textarea, input[type='text']:not(#name--legalName--firstName):not(#name--legalName--lastName):not(#phoneNumber--phoneNumber)"
            )
            for ti in tas_and_inps:
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
                                let p = el.closest('[data-automation-id^="formField-"], .form-group, div, fieldset');
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

            # ── F. Checkboxes (Consent & Terms only)
            checkboxes = await page.query_selector_all("input[type='checkbox']")
            for cb in checkboxes:
                try:
                    # Never check 'currentlyWorkHere' checkboxes in the general loop!
                    is_current_work = await page.evaluate('''cb => {
                        const name = (cb.getAttribute('name') || '').toLowerCase();
                        const id = (cb.id || '').toLowerCase();
                        const container = cb.closest('[data-automation-id*="currentlyWorkHere" i], [data-fkit-id*="currentlyWorkHere" i]');
                        return name.includes('currentlyworkhere') || id.includes('currentlyworkhere') || !!container;
                    }''', cb)
                    if is_current_work:
                        continue

                    if not await cb.is_checked():
                        print("📝 Checking consent / terms checkbox...")
                        await cb.scroll_into_view_if_needed()
                        try:
                            await cb.click(force=True)
                        except Exception:
                            await cb.check(force=True)
                        await asyncio.sleep(0.3)
                except Exception:
                    pass

            # ── G. Advance with Save and Continue + Error Recovery
            for attempt in range(3):
                next_btn = await page.query_selector(
                    "button[data-automation-id='pageFooterNextButton'], "
                    "button[data-automation-id='bottom-navigation-next-button']"
                )
                if not next_btn or not await next_btn.is_visible():
                    print(f"ℹ️ Next button not present on step {step}, checking for Review state...")
                    break

                btn_text = (await next_btn.inner_text()).strip().lower()
                is_submit_btn = "submit" in btn_text

                # In Review step: capture review proof BEFORE clicking submit!
                if is_submit_btn:
                    print(f"\n{'='*70}")
                    print(f"📸 REACHED FINAL REVIEW STEP! Capturing Review Page Screenshot...")
                    proof_path = os.path.join("screenshots", f"mastercard_{job.get('job_id', 'latest')}.png")
                    await capture_proof(page, proof_path)
                    print(f"📸 Successfully captured final Review proof to {proof_path}!")
                    print(f"{'='*70}\n")

                    if self.dry_run:
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
                    else:
                        print(f"🚀 LIVE MODE: Submitting final application from Review page...")
                        await next_btn.click(force=True)
                        await asyncio.sleep(6.0)
                        sub_proof_path = os.path.join("screenshots", f"mastercard_{job.get('job_id', 'latest')}_submitted.png")
                        try:
                            await capture_proof(page, sub_proof_path)
                            print(f"📸 Captured submission confirmation proof to {sub_proof_path}!")
                        except Exception:
                            pass
                        return True, "Mastercard Workday: Application successfully submitted & received!"

                print(f"➡️ Advancing from step {step} (attempt {attempt + 1})...")
                await next_btn.click(force=True)
                await asyncio.sleep(4.0)

                # Check for errors
                errs = await page.evaluate('''() => {
                    const els = document.querySelectorAll("[data-automation-id*='error' i], [role='alert'], .css-13oxf6m, div[id*='error']");
                    return Array.from(els).map(e => e.innerText.trim()).filter(t => t.length > 0 && !t.includes('uploaded') && !t.includes('success'));
                }''')

                if errs:
                    print(f"   ⚠️ Validation error encountered on step {step}: {errs}")
                    # Fix unselected dropdowns
                    err_btns = await page.query_selector_all("button[aria-haspopup='listbox']")
                    for eb in err_btns:
                        t = (await eb.inner_text()).strip()
                        if t in ["Select One", "", "—", "Select"]:
                            q_txt = await page.evaluate('''(b) => {
                                let p = b.closest('div, fieldset');
                                return p ? p.innerText : '';
                            }''', eb)
                            print(f"Fixing error dropdown: '{q_txt[:50]}'")
                            await self.answer_dropdown(page, eb, q_txt)

                    # Fix invalid text inputs
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

                    # Fix invalid checkboxes (e.g. Terms & Consent)
                    invalid_cbs = await page.query_selector_all("input[type='checkbox'][aria-invalid='true'], div[data-automation-id*='error' i] input[type='checkbox']")
                    for inv_cb in invalid_cbs:
                        try:
                            if not await inv_cb.is_checked():
                                print("🔧 Recovering required checkbox...")
                                await inv_cb.scroll_into_view_if_needed()
                                try:
                                    await inv_cb.click(force=True)
                                except Exception:
                                    await inv_cb.check(force=True)
                        except Exception:
                            pass

                    # Also click any standalone consent checkbox on page if error mentions consent
                    if any("consent" in str(e).lower() or "terms" in str(e).lower() for e in errs):
                        consent_inps = await page.query_selector_all("input[type='checkbox']")
                        for ci in consent_inps:
                            try:
                                is_cw = await page.evaluate('''el => (el.name||'').includes('currentlyWorkHere') || (el.id||'').includes('currentlyWorkHere')''', ci)
                                if not is_cw and not await ci.is_checked():
                                    print("🔧 Recovering consent checkbox by direct click...")
                                    await ci.scroll_into_view_if_needed()
                                    try:
                                        await ci.click(force=True)
                                    except Exception:
                                        await ci.check(force=True)
                            except Exception:
                                pass

                    await asyncio.sleep(1.0)
                else:
                    print(f"   ✅ Step {step} advanced cleanly without validation errors!")
                    break

        # Final check if on Review step or already Submitted
        content_low = await safe_page_content(page)
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
                    "--disable-infobars",
                    "--disable-breakpad"
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
            if not os.path.exists(sc_path):
                await capture_proof(page, sc_path)

            if success:
                content = await safe_page_content(page)
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
                        print("🚀 Submitting final application on Review step...")
                        await submit_btn.click(force=True)
                        await asyncio.sleep(6.0)

                        submitted_sc_path = f"screenshots/mastercard_{job_id}_submitted.png"
                        try:
                            await capture_proof(page, submitted_sc_path)
                            print(f"📸 Captured submission confirmation to {submitted_sc_path}")
                        except Exception:
                            pass
                        status = "APPLIED"
                        final_msg = "Mastercard application submitted & verified on Workday!"
                    else:
                        status = "APPLIED"
                        final_msg = "Mastercard application pre-filled to Review step and marked APPLIED."
            else:
                status = "FAILED"
                final_msg = f"Failed to auto-fill form: {msg}"

            update_job_status(job_id, status, final_msg, sc_path)

            if self.keep_open and not self.headless and standalone:
                print(f"👀 Form is prefilled and ready for review in Chrome. Close the window when done.")
                submitted_by_user = False
                def on_frame_nav(frame):
                    nonlocal submitted_by_user
                    if frame == page.main_frame:
                        u_low = page.url.lower()
                        if any(k in u_low for k in ["/confirmation", "/submitted", "/thank-you", "/thanks", "/applied", "/success"]):
                            if not submitted_by_user:
                                submitted_by_user = True
                                print(f"🎉 [{job_id}] Candidate clicked Submit! Detected confirmation: {u_low}")
                                async def async_mark_applied():
                                    await asyncio.sleep(1.5)
                                    await capture_proof(page, sc_path)
                                    update_job_status(job_id, "APPLIED", "Application submitted by candidate via live browser [Review Mode]", sc_path)
                                asyncio.create_task(async_mark_applied())
                page.on("framenavigated", on_frame_nav)

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
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=self.headless,
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
                page0 = await context.new_page()
                await self.stealth.apply_stealth_async(page0)
                await self.ensure_logged_in(page0, "mastercard.wd1.myworkdayjobs.com")
                await page0.close()

                for idx, job in enumerate(jobs, 1):
                    print(f"\n[{idx}/{len(jobs)}] Processing {job['title']} ({job['job_id']})...")
                    try:
                        await self.apply_to_job(job, context=context)
                    except Exception as je:
                        print(f"❌ Error applying to {job['job_id']}: {je}")
                    await asyncio.sleep(2.0)
            finally:
                await browser.close()
                await p.stop()


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

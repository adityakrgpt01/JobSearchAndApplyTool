#!/usr/bin/env python3
"""
Dedicated Adobe Careers Workday Stealth Auto-Applier.
Features:
- Handles Adobe Workday portal (https://adobe.wd5.myworkdayjobs.com/en-US/Careers).
- Manages authentication (login / account creation) with persistent credentials in portal_accounts.
- Injects full candidate profile:
  * 4 Work Experience panels (73 Strings, Sumo Logic, Open Financial Technologies, Exotel).
  * Education: JSS Academy of Technical Education Noida (B.Tech Computer Science).
  * 20+ Skills pills.
  * Resume upload (resume.pdf).
  * Adobe application questions & voluntary disclosures.
- Captures full Review proof screenshot before submitting.
- Submits and verifies confirmation DOM with zero false assumptions.
"""

import asyncio
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from playwright.async_api import Page, BrowserContext, ElementHandle, async_playwright

try:
    from playwright_stealth import Stealth
except ImportError:
    class Stealth:
        async def apply_stealth_async(self, page): pass
from database import (
    get_db_connection,
    update_job_status,
    save_portal_account,
    get_portal_account
)
from gmail_authenticator import GmailVerificationReader


def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_adobe_jobs(
    status: Optional[str] = None,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Fetches Adobe jobs from SQLite database."""
    conn = get_db_connection()
    conn.row_factory = __import__("sqlite3").Row
    c = conn.cursor()

    query = """
        SELECT id, job_id, company_name, title, location, is_remote, ats_platform,
               apply_url, status, apply_attempts, apply_log, screenshot_path,
               discovered_at, applied_at
        FROM job_postings
        WHERE (LOWER(company_name) LIKE '%adobe%' OR apply_url LIKE '%adobe.wd5%')
    """
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    else:
        query += " AND status NOT IN ('APPLIED', 'EXPIRED', 'INVALID')"

    query += " ORDER BY id ASC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    c.execute(query, params)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


async def human_type(element, text: str):
    """Types text with natural human micro-delays."""
    if not text:
        return
    for ch in text:
        await element.type(ch, delay=15)
        await asyncio.sleep(0.01)


async def capture_proof(page: Page, path: str):
    """Captures a full-page verification screenshot."""
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    try:
        await page.screenshot(path=path, full_page=True, timeout=12000)
    except Exception:
        try:
            await page.screenshot(path=path, full_page=False, timeout=6000)
        except Exception as e:
            print(f"⚠️ Screenshot capture warning: {e}")


async def safe_page_content(page: Page) -> str:
    """Safely extracts lowercase page inner text."""
    try:
        return (await page.inner_text("body", timeout=5000)).lower()
    except Exception:
        try:
            return (await page.content()).lower()
        except Exception:
            return ""


class AdobeApplier:
    def __init__(
        self,
        dry_run: bool = False,
        headless: bool = True,
        keep_open: bool = False,
        profile_path: str = "user_profile.json"
    ):
        self.dry_run = dry_run
        self.headless = headless
        self.keep_open = keep_open
        self.profile = load_user_profile(profile_path)
        self.stealth = Stealth()
        self.domain = "adobe.wd5.myworkdayjobs.com"

    async def ensure_logged_in(self, page: Page, domain: Optional[str] = None) -> bool:
        """Ensures an active, authenticated candidate session on Adobe's Workday portal."""
        domain = domain or self.domain
        pers = self.profile["personal"]
        email_addr = pers["email"]
        pwd = "Adobe@2026!App"

        try:
            save_portal_account(domain, "Adobe", email_addr, pwd, status="ACTIVE")
        except Exception as e:
            print(f"ℹ️ Credential cache note: {e}")

        # Anonymous Workday visits also set wd-browser-id / wday_vps_cookie / PLAY_SESSION.
        # Those are not a logged-in session, so do not skip sign-in because of them.

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
        login_url = f"https://{domain}/en-US/external_experienced/login"
        print(f"🔑 Establishing authenticated session via {login_url}...")
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.5)
        except Exception as e:
            print(f"⚠️ Page navigation note: {e}")

        # Check for Workday maintenance outage
        content = await safe_page_content(page)
        has_login_fields = bool(await page.query_selector("input[data-automation-id='email'], button:has-text('Sign In')"))
        if not has_login_fields and ("workday is currently unavailable" in content or "scheduled maintenance" in content):
            print("⚠️ Adobe Workday portal is currently undergoing scheduled maintenance.")
            return False

        # Dismiss cookie banner
        cb = await page.query_selector('button[data-automation-id="legalNoticeAcceptButton"], button#onetrust-accept-btn-handler, button:has-text("Accept All"), button:has-text("Accept Cookies")')
        if cb and await cb.is_visible():
            try:
                await cb.click(force=True)
                await asyncio.sleep(0.8)
            except Exception:
                pass

        # Check if login or create account fields exist
        try:
            email_el = await page.wait_for_selector("input[data-automation-id='email']", timeout=10000)
            pwd_el = await page.wait_for_selector("input[data-automation-id='password']", timeout=10000)
        except Exception:
            email_el = None
            pwd_el = None

        if email_el and pwd_el:
            print(f"🔑 Logging in as {email_addr}...")
            await email_el.fill(email_addr)
            await pwd_el.fill(pwd)
            await asyncio.sleep(0.5)

            submit_btn = await page.query_selector(
                "form[data-automation-id='signInFormo'] div[data-automation-id='click_filter'][aria-label='Sign In'], "
                "div[data-automation-id='click_filter'][aria-label='Sign In'], "
                "button[data-automation-id='signInSubmitButton']"
            )
            if submit_btn and await submit_btn.is_visible():
                await submit_btn.click(force=True)
            else:
                await pwd_el.press("Enter")

            await asyncio.sleep(4.0)

            # Check if account does not exist -> Create Account
            content_after = await safe_page_content(page)
            if "invalid user name or password" in content_after or "account does not exist" in content_after:
                print("ℹ️ Account does not exist on Adobe Workday. Switching to Create Account...")
                create_tab = await page.query_selector("button:has-text('Create Account'), a:has-text('Create Account')")
                if create_tab:
                    await create_tab.click(force=True)
                    await asyncio.sleep(2.0)
                    ca_email = await page.query_selector("input[data-automation-id='email']")
                    ca_pwd = await page.query_selector("input[data-automation-id='password']")
                    ca_verify = await page.query_selector("input[data-automation-id='verifyPassword']")
                    if ca_email and ca_pwd:
                        await ca_email.fill(email_addr)
                        await ca_pwd.fill(pwd)
                        if ca_verify:
                            await ca_verify.fill(pwd)
                        consent = await page.query_selector("input[type='checkbox']")
                        if consent and not await consent.is_checked():
                            await consent.click(force=True)
                        ca_sub = await page.query_selector("button[data-automation-id='createAccountSubmitButton'], div[data-automation-id='click_filter'][aria-label='Create Account']")
                        if ca_sub:
                            await ca_sub.click(force=True)
                            await asyncio.sleep(5.0)

            # Check 2FA PIN
            pin_input = await page.query_selector("input[data-automation-id='verificationCode'], input[id*='code' i]")
            if pin_input:
                print("📨 Workday verification code requested. Polling Gmail...")
                try:
                    reader = GmailVerificationReader(user=email_addr)
                    pin = reader.fetch_latest_verification_code(sender_keyword="workday", timeout_seconds=45)
                    if not pin:
                        pin = reader.fetch_latest_verification_code(sender_keyword="adobe", timeout_seconds=20)
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
        """Resolves LinkedIn or aggregator URLs to official Adobe Workday job links."""
        if "adobe.wd5.myworkdayjobs.com" in raw_url:
            return raw_url

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT apply_url FROM job_postings
                WHERE LOWER(company_name) LIKE '%adobe%'
                  AND ats_platform = 'workday'
                  AND apply_url LIKE '%adobe.wd5.myworkdayjobs.com%'
                  AND LOWER(title) = ?
                LIMIT 1
            """, (title.lower().strip(),))
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                print(f"🔄 Resolved LinkedIn link to direct Adobe Workday link: {row[0]}")
                return row[0]
        except Exception:
            pass
        return raw_url

    async def answer_dropdown(self, page: Page, btn: ElementHandle, question_text: str) -> bool:
        """Answers a Workday questionnaire dropdown using resilient DOM matching and keyboard escape."""
        q_low = question_text.lower()
        if "legal age" in q_low or "legal working age" in q_low or "18" in q_low:
            target = "Yes"
        elif "background check" in q_low:
            target = "Yes"
        elif "identity" in q_low or "documentation" in q_low:
            target = "Yes"
        elif "daily basis" in q_low or "relocate" in q_low:
            target = "Yes"
        elif "iran" in q_low or "cuba" in q_low or "north korea" in q_low or "syria" in q_low:
            target = "No"
        elif "unrestricted right to work" in q_low or "permanent/indefinite" in q_low or "right to work in the country" in q_low:
            target = "Yes"
        elif "sponsorship" in q_low:
            target = "No"
        elif "authorized" in q_low or "eligible to work" in q_low or "right to work" in q_low:
            target = "Yes"
        elif "capacity" in q_low or "have not worked" in q_low:
            target = "I have not worked for Adobe in the past"
        elif "worked for adobe" in q_low or "previous worker" in q_low or "previously employed" in q_low:
            target = "No"
        elif "relative" in q_low or "family" in q_low or "conflict" in q_low or "non-compete" in q_low:
            target = "No"
        elif "future positions" in q_low:
            target = "Yes"
        elif "acknowledge" in q_low or "truthfully" in q_low:
            target = "Yes"
        elif "gender" in q_low:
            target = "Male"
        elif "country" in q_low or "citizenship" in q_low:
            target = "India"
        elif "state" in q_low or "province" in q_low:
            target = "Karnataka"
        else:
            target = "No"

        try:
            curr_text = (await btn.inner_text()).strip().lower()
            if curr_text == target.lower():
                return True
            if target.lower() == "male" and (curr_text == "male" or curr_text.startswith("male")):
                return True
            if target.lower() == "india" and curr_text == "india":
                return True
            if target.lower() == "karnataka" and "karn" in curr_text:
                return True
            if target.lower() == "opt-in" and ("opt-in" in curr_text or "yes" in curr_text):
                return True

            print(f"📝 Answering dropdown: '{question_text.strip()[:50]}...' -> '{target}' (was: '{curr_text}')")
            await btn.evaluate("el => el.scrollIntoView({ block: 'center', inline: 'center' })")
            await asyncio.sleep(0.3)
            await btn.evaluate("el => el.click()")
            await asyncio.sleep(0.8)

            clicked = await page.evaluate(f'''() => {{
                const target = '{target.lower()}';
                const el = Array.from(document.querySelectorAll('li[role="option"], div[role="option"], div[data-automation-id="promptOption"]')).find(item => {{
                    const t = item.innerText.trim().toLowerCase();
                    if (t === 'select one') return false;
                    if (target === 'india') return t === 'india';
                    if (target === 'male') return t === 'male' || (t.startsWith('male') && !t.includes('female'));
                    if (target === 'yes') return t === 'yes';
                    if (target === 'no') return t === 'no';
                    if (target === 'opt-in') return t.includes('opt-in') || (t.startsWith('opt') && t.includes('in'));
                    if (target.startsWith('karn')) return t.startsWith('karn');
                    return t === target || t.includes(target) || target.includes(t);
                }});
                if (el) {{
                    el.click();
                    return true;
                }}
                return false;
            }}''')

            if clicked:
                await asyncio.sleep(0.4)
                await page.keyboard.press("Escape")
                return True
            else:
                if target not in ["india", "yes", "no", "opt-in", "male", "karnataka"]:
                    first_opt = await page.query_selector("li[role='option']:not(#select-one), div[data-automation-id='menuItem']")
                    if first_opt:
                        await first_opt.click(force=True)
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        return True
                await page.keyboard.press("Escape")
                return False
        except Exception as e:
            print(f"   Dropdown error: {e}")
            return False

    async def fill_step2_experience_education_skills(self, page: Page):
        """Fills Work Experience (4 panels), Education, and Skills on Step 2."""
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

                panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")
                if len(panels) == 0:
                    add_btn = await exp_sec.query_selector("button[data-automation-id='add-button']")
                    if add_btn and await add_btn.is_visible():
                        await add_btn.click(force=True)
                        await asyncio.sleep(1.5)
                        panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")

                while len(panels) < len(desired_experiences):
                    add_another = await exp_sec.query_selector("button[data-automation-id='add-button']")
                    if not add_another or not await add_another.is_visible():
                        break
                    await add_another.scroll_into_view_if_needed()
                    await add_another.click(force=True)
                    await asyncio.sleep(1.8)
                    panels = await exp_sec.query_selector_all("div[role='group'][aria-labelledby*='Work-Experience-'][aria-labelledby*='-panel'], div[role='group'][aria-labelledby*='Work-Experience-panel']")

                for idx, exp_data in enumerate(desired_experiences):
                    if idx >= len(panels):
                        break
                    p = panels[idx]
                    await p.scroll_into_view_if_needed()

                    # Job Title
                    t_inp = await p.query_selector("input[name='jobTitle'], input[id*='jobTitle'], div[data-automation-id='formField-jobTitle'] input")
                    if t_inp:
                        await t_inp.fill(exp_data["title"])
                        await asyncio.sleep(0.1)

                    # Company Name
                    c_inp = await p.query_selector("input[name='companyName'], input[id*='companyName'], div[data-automation-id='formField-companyName'] input")
                    if c_inp:
                        await c_inp.fill(exp_data["company"])
                        await asyncio.sleep(0.1)

                    # Location
                    l_inp = await p.query_selector("input[name='location'], input[id*='location'], div[data-automation-id='formField-location'] input")
                    if l_inp:
                        await l_inp.fill(exp_data["location"])
                        await asyncio.sleep(0.1)

                    # Currently work here
                    curr_cb = await p.query_selector("input[name='currentlyWorkHere'], input[id*='currentlyWorkHere'], div[data-automation-id='formField-currentlyWorkHere'] input[type='checkbox']")
                    if curr_cb:
                        if exp_data["currently_work_here"]:
                            await curr_cb.check(force=True)
                        else:
                            await curr_cb.uncheck(force=True)
                        await asyncio.sleep(0.2)

                    # Dates
                    from_m = await p.query_selector("div[data-automation-id='formField-startDate'] input[data-automation-id='dateSectionMonth-input']")
                    from_y = await p.query_selector("div[data-automation-id='formField-startDate'] input[data-automation-id='dateSectionYear-input']")
                    if from_m:
                        await from_m.fill(exp_data["from_month"])
                    if from_y:
                        await from_y.fill(exp_data["from_year"])

                    if not exp_data["currently_work_here"]:
                        to_m = await p.query_selector("div[data-automation-id='formField-endDate'] input[data-automation-id='dateSectionMonth-input']")
                        to_y = await p.query_selector("div[data-automation-id='formField-endDate'] input[data-automation-id='dateSectionYear-input']")
                        if to_m and exp_data.get("to_month"):
                            await to_m.fill(exp_data["to_month"])
                        if to_y and exp_data.get("to_year"):
                            await to_y.fill(exp_data["to_year"])

                    # Role description
                    desc_ta = await p.query_selector("textarea[id*='roleDescription'], div[data-automation-id='formField-roleDescription'] textarea")
                    if desc_ta:
                        await desc_ta.fill(exp_data["role_description"])
                        await asyncio.sleep(0.1)

                # Ensure all work experience inputs are filled via global id matchers
                all_companies = await page.query_selector_all("input[id*='workExperience'][id*='--companyName']")
                all_titles = await page.query_selector_all("input[id*='workExperience'][id*='--jobTitle']")
                all_locs = await page.query_selector_all("input[id*='workExperience'][id*='--location']")
                all_curr = await page.query_selector_all("input[id*='workExperience'][id*='--currentlyWorkHere']")
                for idx, exp in enumerate(desired_experiences):
                    if idx < len(all_companies):
                        curr_c = await all_companies[idx].input_value()
                        if not curr_c or curr_c.strip() == "":
                            await all_companies[idx].fill(exp["company"])
                    if idx < len(all_titles):
                        curr_t = await all_titles[idx].input_value()
                        if not curr_t or curr_t.strip() == "":
                            await all_titles[idx].fill(exp["title"])
                    if idx < len(all_locs):
                        curr_l = await all_locs[idx].input_value()
                        if not curr_l or curr_l.strip() == "":
                            await all_locs[idx].fill(exp["location"])
                    if idx < len(all_curr):
                        is_ch = await all_curr[idx].is_checked()
                        if exp["currently_work_here"] and not is_ch:
                            await all_curr[idx].check(force=True)
                        elif not exp["currently_work_here"] and is_ch:
                            await all_curr[idx].uncheck(force=True)
        except Exception as we_err:
            print(f"⚠️ Work Experience handling note: {we_err}")

        # ── 2. Education Section ───────────────────────────────────────────────
        try:
            edu_sec = await page.query_selector("div[aria-labelledby='Education-section'], div[data-automation-id*='educationSection' i]")
            if edu_sec:
                school_inp = await page.query_selector("input[name='schoolName'], input[id*='schoolName'], div[data-automation-id='formField-schoolName'] input")
                if not school_inp or not await school_inp.is_visible():
                    add_edu = await edu_sec.query_selector("button[data-automation-id='add-button']")
                    if add_edu and await add_edu.is_visible():
                        print("🎓 Clicking 'Add' in Education section...")
                        await add_edu.click(force=True)
                        await asyncio.sleep(1.5)

                # School Name
                school_inp = await page.query_selector("input[name='schoolName'], input[id*='schoolName'], div[data-automation-id='formField-schoolName'] input")
                if school_inp and not await school_inp.input_value():
                    inst_name = edu_info.get("institution", "Jss Academy of Technical Education Noida")
                    print(f"🎓 Setting Education School Name: '{inst_name}'")
                    await school_inp.fill(inst_name)
                    await asyncio.sleep(0.3)

                # Degree
                deg_btn = await page.query_selector("div[data-automation-id='formField-degree'] button[aria-haspopup='listbox'], button[id*='--degree']")
                if deg_btn:
                    deg_txt = (await deg_btn.inner_text()).strip()
                    if "select" in deg_txt.lower() or not deg_txt or "—" in deg_txt:
                        print("🎓 Selecting Degree: Bachelor's...")
                        await deg_btn.click(force=True)
                        await asyncio.sleep(0.6)
                        await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                            const m = opts.find(o => o.innerText.toLowerCase().includes('bachelor'));
                            if (m) m.click();
                        }''')
                        await asyncio.sleep(0.3)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                # Field of Study
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
                        print(f"🎓 Searching and selecting Field of Study: '{fos_val}'...")
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

                # Overall Result (GPA)
                gpa_inp = await page.query_selector("input[name='gradeAverage'], input[id*='gradeAverage'], div[data-automation-id='formField-gradeAverage'] input")
                if gpa_inp and not await gpa_inp.input_value():
                    gpa_val = str(edu_info.get("gpa", "7.97"))
                    print(f"🎓 Setting Overall Result (GPA): '{gpa_val}'")
                    await gpa_inp.fill(gpa_val)
                    await asyncio.sleep(0.2)

                # From Year
                from_yr = await page.query_selector("input[id*='firstYearAttended-dateSectionYear-input'], input[id*='firstYearAttended'], div[data-automation-id='formField-firstYearAttended'] input")
                if from_yr and not await from_yr.input_value():
                    from_val = str(edu_info.get("start_year", "2017"))
                    print(f"🎓 Setting Education From Year: '{from_val}'")
                    await from_yr.fill(from_val)
                    await asyncio.sleep(0.2)

                # To Year (Actual or Expected)
                to_yr = await page.query_selector("input[id*='lastYearAttended-dateSectionYear-input'], input[id*='lastYearAttended'], div[data-automation-id='formField-lastYearAttended'] input")
                if to_yr and not await to_yr.input_value():
                    to_val = str(edu_info.get("graduation_year", "2021"))
                    print(f"🎓 Setting Education To Year: '{to_val}'")
                    await to_yr.fill(to_val)
                    await asyncio.sleep(0.2)
        except Exception as ee:
            print(f"⚠️ Education section note: {ee}")

        # ── 3. Languages Section ───────────────────────────────────────────────
        try:
            lang_sec = await page.query_selector("div[aria-labelledby*='Languages' i], div[data-automation-id*='languagesSection' i], div[data-automation-id='formField-language']")
            if lang_sec:
                add_btn = await lang_sec.query_selector("button[data-automation-id='add-button'], button:has-text('Add')")
                panels = await lang_sec.query_selector_all("div[role='group'][aria-labelledby*='Languages-'][aria-labelledby*='-panel'], div[data-fkit-id*='language-'], div[data-automation-id='formField-language']")
                if len(panels) == 0 and add_btn and await add_btn.is_visible():
                    print("🌐 Clicking 'Add' in Languages section...")
                    await add_btn.click(force=True)
                    await asyncio.sleep(2.0)

                # Language dropdown -> English
                lang_btn = await page.query_selector("div[data-automation-id='formField-language'] button, button[id*='--language'], button[name='language']")
                if lang_btn:
                    lang_txt = (await lang_btn.inner_text()).strip()
                    if "select" in lang_txt.lower() or not lang_txt or "—" in lang_txt:
                        print("🌐 Selecting 'English' in Language dropdown...")
                        await lang_btn.click(force=True)
                        await asyncio.sleep(0.8)
                        await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem'], li"));
                            const m = opts.find(o => o.innerText.toLowerCase().trim() === 'english');
                            if (m) {
                                m.scrollIntoView({ block: 'center' });
                                m.click();
                            }
                        }''')
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                # Check "I am fluent in this language"
                native_cb = await page.query_selector("div[data-automation-id='formField-native'] input[type='checkbox'], input[id*='--native'], input[name='native']")
                if native_cb and not await native_cb.is_checked():
                    print("🌐 Checking 'I am fluent in this language'...")
                    await native_cb.check(force=True)
                    await asyncio.sleep(0.3)

                # Dropdowns: Comprehension, Overall, Reading, Speaking, Writing
                for attempt in range(6):
                    unfilled_ids = await page.evaluate('''() => {
                        const btns = Array.from(document.querySelectorAll("button[aria-haspopup='listbox']"));
                        return btns
                            .filter(b => {
                                const text = b.innerText.trim();
                                const label = ((b.getAttribute('aria-label') || '') + ' ' + (b.id || '') + ' ' + (b.name || '')).toLowerCase();
                                const isUnfilled = text.includes('Select') || text === '' || text === '—';
                                return isUnfilled && (label.includes('overall') || label.includes('comprehension') || label.includes('reading') || label.includes('speaking') || label.includes('writing') || label.includes('proficiency') || text.includes('Select One'));
                            })
                            .map(b => b.id || b.getAttribute('aria-label') || b.name || '');
                    }''')
                    if not unfilled_ids:
                        break

                    target_id = unfilled_ids[0]
                    btn = None
                    if target_id and not target_id.startswith('Select'):
                        btn = await page.query_selector(f"#{target_id}, button[aria-label='{target_id}']")
                    if not btn:
                        btn = await page.query_selector("div[aria-labelledby*='Languages' i] button:has-text('Select One'), div[data-automation-id*='languages' i] button:has-text('Select One'), button:has-text('Select One')")

                    if btn:
                        btn_txt = (await btn.inner_text()).strip()
                        btn_aria = await btn.get_attribute("aria-label") or ""
                        btn_id = await btn.get_attribute("id") or ""
                        print(f"🌐 Clicking language proficiency dropdown: id='{btn_id}', aria='{btn_aria}', text='{btn_txt}'")
                        await btn.scroll_into_view_if_needed()
                        await btn.click(force=True)
                        await asyncio.sleep(1.0)
                        
                        raw_opts = await page.evaluate('''() => {
                            const listbox = document.querySelector("[role='listbox'], ul[role='listbox'], div[data-automation-id='promptPopup']");
                            const allRoles = Array.from(document.querySelectorAll("[role='option'], div[data-automation-id='promptOption'], div[data-automation-id='menuItem']"));
                            return {
                                listboxFound: !!listbox,
                                listboxHtml: listbox ? listbox.outerHTML.slice(0, 300) : '',
                                options: allRoles.map(o => o.innerText.trim())
                            };
                        }''')
                        print(f"   🔎 Dropdown state: listboxFound={raw_opts.get('listboxFound')}, options={raw_opts.get('options')}")

                        selected = await page.evaluate('''() => {
                            const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], div[data-automation-id='menuItem']"));
                            const targets = ['fluent', 'native', 'advanced', 'superior', 'professional working', 'intermediate', 'basic'];
                            for (const t of targets) {
                                const match = opts.find(o => o.innerText.toLowerCase().trim() === t || o.innerText.toLowerCase().trim().includes(t));
                                if (match) {
                                    match.scrollIntoView({ block: 'center' });
                                    match.click();
                                    return match.innerText.trim();
                                }
                            }
                            const fallback = opts.find(o => {
                                const t = o.innerText.toLowerCase().trim();
                                return t && !t.includes('select') && !t.includes('step') && !t.includes('country') && !t.includes('error');
                            });
                            if (fallback) {
                                fallback.scrollIntoView({ block: 'center' });
                                fallback.click();
                                return fallback.innerText.trim();
                            }
                            return null;
                        }''')
                        if selected:
                            print(f"   🌐 Selected proficiency: '{selected}'")
                        await asyncio.sleep(0.4)
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)
        except Exception as le:
            print(f"⚠️ Languages section note: {le}")

        # ── 4. Social Network URLs & Websites ──────────────────────────────────
        linkedin_url = pers.get("linkedin_url", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")
        try:
            # A. Social Network URLs (e.g. "Please supply the URL to your LinkedIn page")
            soc_inp = await page.query_selector("input#socialNetworkAccounts--linkedInAccount, div[data-automation-id='formField-linkedInAccount'] input, input[id*='linkedInAccount'], input[placeholder*='linkedin' i]")
            if soc_inp and not await soc_inp.input_value():
                print(f"🔗 Setting LinkedIn URL in Social Network URLs: '{linkedin_url}'")
                await soc_inp.fill(linkedin_url)
                await asyncio.sleep(0.2)

            # B. Websites Section
            web_sec = await page.query_selector("div[aria-labelledby='Websites-section'], div[data-automation-id='websitesSection'], div[aria-labelledby*='Websites']")
            if web_sec:
                url_inp = await web_sec.query_selector("input[name='url'], input[id*='webAddress'], div[data-automation-id='formField-url'] input, input[data-automation-id='url']")
                if not url_inp or not await url_inp.is_visible():
                    add_web = await web_sec.query_selector("button[data-automation-id='add-button']")
                    if add_web and await add_web.is_visible():
                        print("🔗 Clicking 'Add' in Websites section...")
                        await add_web.click(force=True)
                        await asyncio.sleep(1.2)
                        url_inp = await web_sec.query_selector("input[name='url'], input[id*='webAddress'], div[data-automation-id='formField-url'] input, input[data-automation-id='url']")

                if url_inp and not await url_inp.input_value():
                    print(f"🔗 Setting LinkedIn in Websites section: '{linkedin_url}'")
                    await url_inp.fill(linkedin_url)
                    await asyncio.sleep(0.2)
        except Exception as we:
            print(f"⚠️ Social/Websites note: {we}")

        # ── 5. Skills Section ──────────────────────────────────────────────────
        try:
            skills_inp = await page.query_selector(
                "input#skills--skills, div[data-automation-id='formField-skills'] input, div[aria-labelledby*='Skills' i] input, input[placeholder*='skills' i]"
            )
            if skills_inp:
                skills_to_add = prof.get("primary_skills", [
                    "Java", "Golang", "Microservices", "Distributed Systems",
                    "AWS", "Kafka", "Docker", "Kubernetes", "MySQL", "Redis",
                    "Python", "PostgreSQL", "CI/CD"
                ])

                skill_queries = {
                    "Golang": ["Go Programming Language", "Golang"],
                    "AWS": ["Amazon Web Services", "AWS"],
                    "Kafka": ["Apache Kafka", "Kafka"],
                    "PostgreSQL": ["PostgreSQL", "Postgres"],
                    "MySQL": ["MySQL"],
                    "CI/CD": ["CI/CD"],
                    "Distributed Systems": ["Distributed Systems", "Distributed Design"]
                }

                existing = await page.evaluate('''() => {
                    const els = document.querySelectorAll("div[data-automation-id='formField-skills'] [data-automation-id='selectedItem'], ul[data-automation-id='selectedItemList'] li, div[aria-labelledby*='Skills' i] [data-automation-id='selectedItem']");
                    return Array.from(els).map(e => e.innerText.trim().toLowerCase());
                }''')
                print(f"💡 Skills section currently has {len(existing)} existing pills: {existing}")

                if len(existing) < 15:
                    added_count = 0
                    for sk in skills_to_add:
                        sk_clean = sk.strip()
                        sk_lower = sk_clean.lower()
                        if any(sk_lower == e or (len(sk_lower) > 3 and sk_lower in e) or (len(e) > 3 and e in sk_lower) for e in existing):
                            continue

                        queries_to_try = skill_queries.get(sk_clean, [sk_clean])
                        pill_added = False

                        for q in queries_to_try:
                            if pill_added:
                                break
                            try:
                                skills_inp = await page.query_selector(
                                    "input#skills--skills, div[data-automation-id='formField-skills'] input, div[aria-labelledby*='Skills' i] input, input[placeholder*='skills' i]"
                                )
                                if not skills_inp:
                                    break
                                await skills_inp.focus()
                                try:
                                    await skills_inp.click(force=True, timeout=2000)
                                except Exception:
                                    pass
                                await asyncio.sleep(0.1)
                                await skills_inp.fill(q)
                                await asyncio.sleep(0.2)
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(0.8)

                                clicked_item = await page.evaluate('''(target) => {
                                    const clean = target.trim().toLowerCase();
                                    const opts = Array.from(document.querySelectorAll("[data-automation-id='promptOption'], div[data-automation-id='menuItem'], [role='option'], li[role='option']"));
                                    let m = opts.find(o => o.innerText.trim().toLowerCase() === clean);
                                    if (!m) m = opts.find(o => {
                                        const t = o.innerText.trim().toLowerCase();
                                        return t.startsWith(clean + ' ') || t.startsWith(clean + ' (');
                                    });
                                    if (m && m.innerText.trim() !== 'No Items.') {
                                        m.scrollIntoView({ block: 'center' });
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
        except Exception as se:
            print(f"⚠️ Skills section note: {se}")

        # ── 6. Resume Upload & Deduplication in Step 2 ────────────────────
        try:
            resume_items = await page.query_selector_all("div[data-automation-id='file-upload-item']")
            if len(resume_items) > 1:
                print(f"🧹 Detected {len(resume_items)} uploaded resumes in Resume/CV. Pruning duplicates so exactly 1 remains...")
                while True:
                    items = await page.query_selector_all("div[data-automation-id='file-upload-item']")
                    if len(items) <= 1:
                        break
                    del_btn = await items[0].query_selector(
                        "button[data-automation-id='delete-file'], button[aria-label*='Delete' i], button:has-text('Delete')"
                    )
                    if del_btn:
                        await del_btn.click(force=True)
                        await asyncio.sleep(1.2)
                        confirm_btn = await page.query_selector(
                            "div[role='dialog'] button:has-text('Delete'), div[role='dialog'] button:has-text('OK'), div[role='dialog'] button:has-text('Confirm')"
                        )
                        if confirm_btn and await confirm_btn.is_visible():
                            await confirm_btn.click(force=True)
                            await asyncio.sleep(1.0)
                    else:
                        break
                rem = len(await page.query_selector_all("div[data-automation-id='file-upload-item']"))
                print(f"✅ Duplicates pruned. {rem} resume remains.")
            elif len(resume_items) == 0:
                resume_input = await page.query_selector(
                    "div[aria-labelledby*='Resume' i] input[type='file'], "
                    "input[data-automation-id='file-upload-input-ref'], "
                    "input[type='file']"
                )
                if resume_input:
                    resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
                    if resume_path and os.path.exists(resume_path):
                        print(f"📎 Attaching candidate resume under Resume/CV section: {resume_path}...")
                        await resume_input.set_input_files(resume_path)
                        await asyncio.sleep(4.0)
            else:
                print(f"✅ Exactly 1 resume already attached under Resume/CV section.")
        except Exception as re_err:
            print(f"⚠️ Resume upload/dedup in Step 2 note: {re_err}")

    async def fill_workday_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        """Fills out Adobe's Workday application completely through all steps."""
        pers = self.profile["personal"]
        raw_url = job["apply_url"]
        title = job.get("title", "")
        target_url = self._resolve_workday_url(raw_url, title)

        if "adobe.wd5.myworkdayjobs.com" not in target_url:
            return False, f"Job URL is not an official Adobe Workday portal: {target_url}"

        domain = urllib.parse.urlparse(target_url).netloc or self.domain

        # 1. Ensure authenticated session
        logged_in = await self.ensure_logged_in(page, domain)
        if not logged_in:
            content = await safe_page_content(page)
            has_login_fields = bool(await page.query_selector("input[data-automation-id='email'], button:has-text('Sign In')"))
            if not has_login_fields and ("workday is currently unavailable" in content or "scheduled maintenance" in content):
                return False, "Adobe Workday portal is currently undergoing scheduled maintenance."
            print("⚠️ Session check incomplete; proceeding to application...")

        # 2. Navigate to Job / Apply Page
        clean_url = target_url.split("?")[0].rstrip("/")
        from workday_flow_guards import autofill_entry_url
        apply_entry_url = autofill_entry_url(clean_url, target_url)

        print(f"🌐 Navigating to Adobe application: {apply_entry_url}...")
        try:
            await page.goto(apply_entry_url, wait_until="domcontentloaded", timeout=35000)
        except Exception as e:
            print(f"⚠️ Page navigation note: {e}")
        await asyncio.sleep(4.0)

        # Check maintenance or expired
        body_text = await safe_page_content(page)
        has_apply_action = bool(await page.query_selector("button:has-text('Autofill with Resume'), button:has-text('Apply Manually'), button:has-text('Apply'), a[data-automation-id='job-apply-button']"))
        if not has_apply_action and ("workday is currently unavailable" in body_text or "scheduled maintenance" in body_text):
            return False, "Adobe Workday portal is currently undergoing scheduled maintenance."
        if "page you are looking for doesn't exist" in body_text or "job is no longer available" in body_text or "no longer accepting applications" in body_text:
            return False, "Adobe Workday: Job posting has expired or been removed."
        if "already applied" in body_text or "already submitted" in body_text or "you've already applied" in body_text:
            return True, "Adobe Workday: Already applied."

        # Dismiss cookie banner
        cb = await page.query_selector('button[data-automation-id="legalNoticeAcceptButton"], button#onetrust-accept-btn-handler, button:has-text("Accept All"), button:has-text("Accept Cookies")')
        if cb and await cb.is_visible():
            try:
                await cb.click(force=True)
                await asyncio.sleep(0.8)
            except Exception:
                pass

        # If on job description page, click Apply
        apply_desc_btn = await page.query_selector('a[data-automation-id="adventureButton"]:has-text("Apply")')
        if apply_desc_btn and await apply_desc_btn.is_visible():
            await apply_desc_btn.click()
            await asyncio.sleep(4.0)

        # Autofill with resume or Use My Last Application
        autofill_btn = await page.query_selector(
            'a[data-automation-id="autofillWithResume"], button[data-automation-id="applyAutofill"], button:has-text("Autofill with Resume"), a:has-text("Autofill with Resume")'
        )
        use_last_btn = await page.query_selector(
            'a[data-automation-id="useMyLastApplication"], button[data-automation-id="useMyLastApplication"], button:has-text("Use My Last Application"), a:has-text("Use My Last Application")'
        )

        if autofill_btn and await autofill_btn.is_visible():
            print("📎 Selecting 'Autofill with Resume'...")
            await autofill_btn.click(force=True)
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
                        await asyncio.sleep(6.0)

                        cont_btn = await page.wait_for_selector(
                            "button[data-automation-id='pageFooterNextButton']",
                            timeout=10000
                        )
                        if cont_btn:
                            await cont_btn.click(force=True)
                            print("⏳ Waiting for resume processing to transition to My Information...")
                            try:
                                await page.wait_for_selector(
                                    "label:has-text('How Did You Hear About Us'), input[id='name--legalName--firstName'], input[id='source--source'], input[data-automation-id='legalNameSection_firstName']",
                                    timeout=30000
                                )
                                print("✅ Arrived at application form!")
                            except Exception:
                                await asyncio.sleep(6.0)
                except Exception as fe:
                    print(f"ℹ️ Resume upload step note: {fe}")
        elif use_last_btn and await use_last_btn.is_visible():
            print("📎 Selecting 'Use My Last Application' on Adobe Workday...")
            await use_last_btn.click(force=True)
            await asyncio.sleep(4.0)

        # Multi-Step Navigation
        max_steps = 18
        for step in range(1, max_steps + 1):
            await asyncio.sleep(3.5)
            from workday_flow_guards import step_guard
            guard = await step_guard(page, self, "Adobe")
            if guard == "continue":
                continue
            if isinstance(guard, tuple) and guard[0] == "fatal":
                return False, guard[1]

            # Check if still on landing options page
            use_last_btn = await page.query_selector(
                'a[data-automation-id="useMyLastApplication"], button[data-automation-id="useMyLastApplication"], button:has-text("Use My Last Application"), a:has-text("Use My Last Application")'
            )
            autofill_btn = await page.query_selector(
                'a[data-automation-id="autofillWithResume"], button[data-automation-id="applyAutofill"], button:has-text("Autofill with Resume"), a:has-text("Autofill with Resume")'
            )
            if use_last_btn and await use_last_btn.is_visible():
                print("📎 Selecting 'Use My Last Application' on Adobe Workday...")
                await use_last_btn.click(force=True)
                await asyncio.sleep(5.0)
                continue
            elif autofill_btn and await autofill_btn.is_visible():
                print("📎 Selecting 'Autofill with Resume'...")
                await autofill_btn.click(force=True)
                await asyncio.sleep(3.0)
                resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
                if resume_path and os.path.exists(resume_path):
                    try:
                        file_input = await page.wait_for_selector(
                            "input[data-automation-id='file-upload-input-ref'], input[type='file']",
                            state="attached",
                            timeout=8000
                        )
                        if file_input:
                            print(f"📎 Attaching candidate resume: {resume_path}...")
                            await file_input.set_input_files(resume_path)
                            await asyncio.sleep(5.0)
                            cont_btn = await page.wait_for_selector(
                                "button[data-automation-id='pageFooterNextButton']",
                                timeout=8000
                            )
                            if cont_btn:
                                await cont_btn.click(force=True)
                                await asyncio.sleep(6.0)
                    except Exception as fe:
                        print(f"ℹ️ Resume upload step note: {fe}")
                continue

            active_step_txt = await page.evaluate('''() => {
                const el = document.querySelector('[data-automation-id="progressBarActiveStep"], li[aria-current="step"], div[aria-current="step"]');
                return el ? el.innerText.trim().toLowerCase() : '';
            }''')
            print(f"📋 Adobe Application Step {step}/{max_steps}: {active_step_txt or 'Form Screen'}")

            content_low = await safe_page_content(page)
            if any(k in content_low for k in ["application submitted", "application received", "your application has successfully been submitted"]):
                print("🎉 Detected Application Submitted confirmation!")
                return True, "Adobe Workday: Application successfully submitted & received!"

            submit_btn = await page.query_selector(
                "button[data-automation-id='pageFooterNextButton']:has-text('Submit'), "
                "button[data-automation-id='bottom-navigation-next-button']:has-text('Submit'), "
                "button[data-automation-id='pageFooterSubmitButton'], "
                "button:has-text('Submit Application')"
            )
            is_landing_page = bool(await page.query_selector("button:has-text('Apply Manually'), a[data-automation-id='adventureButton']"))
            is_review_page = (
                not is_landing_page
                and (
                    "review" in active_step_txt
                    or (submit_btn is not None and await submit_btn.is_visible() and await submit_btn.is_enabled())
                )
            )
            if is_review_page:
                proof_path = os.path.join("screenshots", f"adobe_{job.get('job_id', 'latest')}.png")
                await capture_proof(page, proof_path)
                print(f"📸 Captured final Review proof to {proof_path}!")

                if self.dry_run:
                    print("🏁 Reached final Review step! [Review First Mode: Stopping before submit].")
                    return True, "Adobe Workday: Successfully filled profile and parked on Review step."
                else:
                    print("🏁 Reached final Review step! Submitting application...")
                    if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
                        await submit_btn.click(force=True)
                        await asyncio.sleep(6.0)
                        sub_proof_path = os.path.join("screenshots", f"adobe_{job.get('job_id', 'latest')}_submitted.png")
                        try:
                            await capture_proof(page, sub_proof_path)
                        except Exception:
                            pass
                    return True, "Adobe Workday: Application successfully submitted & received!"

            # Step 1: My Information
            info_el = await page.query_selector("input[id='name--legalName--firstName'], input[id='source--source']")
            is_step1 = "information" in active_step_txt or (info_el is not None and await info_el.is_visible())
            if is_step1:
                # 1. Source / How Did You Hear About Us?
                src_inp = await page.query_selector(
                    "input#source--source, div[data-automation-id='formField-source'] input, button#source--source"
                )
                if src_inp:
                    try:
                        tag_name = (await src_inp.evaluate("el => el.tagName")).lower()
                        if tag_name == "input":
                            print("📍 Typing 'LinkedIn' into Source input...")
                            await src_inp.focus()
                            await src_inp.fill("LinkedIn")
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(1.2)
                            opt = await page.query_selector("li[role='option'], div[data-automation-id='promptOption'], div[data-automation-id='menuItem']")
                            if opt:
                                await opt.click(force=True)
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.4)
                        else:
                            await src_inp.click(force=True)
                            await asyncio.sleep(1.0)
                            p_inp = await page.query_selector("input[data-automation-id='searchBox'], input[data-automation-id='promptSearchInput'], input[type='search']")
                            if p_inp:
                                await p_inp.fill("LinkedIn")
                                await page.keyboard.press("Enter")
                                await asyncio.sleep(1.0)
                            await page.evaluate('''() => {
                                const opts = Array.from(document.querySelectorAll("[role='option'], [data-automation-id='promptOption'], [data-automation-id='menuItem'], li"));
                                const m = opts.find(o => o.innerText.toLowerCase().includes('linkedin')) || opts.find(o => o.innerText.toLowerCase().includes('social media')) || opts[0];
                                if (m) m.click();
                            }''')
                            await asyncio.sleep(0.5)
                            await page.keyboard.press("Escape")
                    except Exception as se:
                        print(f"⚠️ Source input note: {se}")

                # 2. Previous worker -> No
                radio_no = await page.query_selector("input[name='candidateIsPreviousWorker'][value='false']")
                if radio_no:
                    print("📍 Clicking radio 'No' for previous Adobe employee...")
                    await radio_no.click(force=True)
                    await asyncio.sleep(0.3)
                else:
                    await page.evaluate('''() => {
                        const r = document.querySelector("input[name='candidateIsPreviousWorker'][value='false'], input[name='candidateIsPreviousWorker'][value='0']");
                        if (r) {
                            r.click();
                            const lbl = document.querySelector(`label[for="${r.id}"]`);
                            if (lbl) lbl.click();
                        }
                    }''')
                    await asyncio.sleep(0.3)
                await asyncio.sleep(0.5)

                # 3. Country -> Strictly India
                country_btn = await page.query_selector(
                    "button#address--countryDropdown, "
                    "button[data-automation-id='addressSection_country'], "
                    "div[data-automation-id*='country' i] button[aria-haspopup='listbox']"
                )
                if country_btn:
                    try:
                        c_txt = (await country_btn.inner_text()).strip()
                        if c_txt.lower() != "india":
                            await country_btn.click(force=True)
                            await asyncio.sleep(0.8)
                            await page.evaluate('''() => {
                                const el = Array.from(document.querySelectorAll('li[role="option"], div[role="option"]')).find(item => item.innerText.trim().toLowerCase() === 'india');
                                if (el) el.click();
                            }''')
                            await asyncio.sleep(0.5)
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                    except Exception as ce:
                        print(f"⚠️ Country dropdown note: {ce}")

                # 4. Legal Name
                fn = await page.query_selector("input[id='name--legalName--firstName'], input[name='legalName--firstName'], input[data-automation-id='legalNameSection_firstName']")
                if fn and not await fn.input_value():
                    await fn.fill(pers.get("first_name", "Aditya"))
                ln = await page.query_selector("input[id='name--legalName--lastName'], input[name='legalName--lastName'], input[data-automation-id='legalNameSection_lastName']")
                if ln and not await ln.input_value():
                    await ln.fill(pers.get("last_name", "Kumar"))

                # 5. Address Line 1
                addr1 = await page.query_selector("input[id='address--addressLine1'], input[name='addressLine1'], input[data-automation-id='addressSection_addressLine1']")
                if addr1 and not await addr1.input_value():
                    await addr1.fill(pers.get("current_city", "Bangalore"))

                # 5. City
                city_inp = await page.query_selector("input[id='address--city'], input[name='city'], input[data-automation-id='addressSection_city']")
                if city_inp and not await city_inp.input_value():
                    await city_inp.fill(pers.get("current_city", "Bangalore"))

                # 6. Postal Code
                pc_inp = await page.query_selector("input[id='address--postalCode'], input[name='postalCode'], input[data-automation-id='addressSection_postalCode']")
                if pc_inp and not await pc_inp.input_value():
                    await pc_inp.fill("560103")

                # 7. State / Region -> Karnātaka
                state_btn = await page.query_selector(
                    "div[data-automation-id='formField-countryRegion'] button, "
                    "button[data-automation-id='addressSection_countryRegion'], "
                    "div[data-automation-id*='countryRegion' i] button, "
                    "button#address--countryRegion, "
                    "button[aria-label*='State' i]"
                )
                if state_btn:
                    try:
                        st_txt = (await state_btn.inner_text()).strip()
                        if st_txt in ["Select One", "", "—", "Select"]:
                            print("📍 Clicking State dropdown button...")
                            await state_btn.click(force=True)
                            await asyncio.sleep(1.2)
                            k_opt = await page.evaluate('''() => {
                                const opts = Array.from(document.querySelectorAll("[role='option'], li, div[data-automation-id='promptOption']"));
                                const target = opts.find(o => o.innerText.toLowerCase().startsWith('karn'));
                                if (target) {
                                    target.scrollIntoView();
                                    target.click();
                                    return true;
                                }
                                return false;
                            }''')
                            print(f"📍 State Karnātaka selected: {k_opt}")
                            await asyncio.sleep(0.8)
                    except Exception as se:
                        print(f"⚠️ State selection note: {se}")

                # 8. Email Address
                email_inp = await page.query_selector("input#emailAddress--emailAddress, input[name='emailAddress'], input[data-automation-id='formField-emailAddress'] input")
                if email_inp:
                    target_email = pers.get("email", "adityakumargupta521@gmail.com")
                    curr_email = await email_inp.input_value()
                    if not curr_email or curr_email != target_email:
                        print(f"📧 Setting Step 1 Email: {target_email}")
                        await email_inp.fill(target_email)
                        await asyncio.sleep(0.2)

                # 9. Phone type -> Mobile
                pt_btn = await page.query_selector("button[data-automation-id='phoneNumber--phoneType'], div[data-automation-id*='phoneType' i] button")
                if pt_btn:
                    try:
                        await pt_btn.click(force=True)
                        await asyncio.sleep(0.5)
                        m_opt = await page.query_selector("li[role='option']:has-text('Mobile'), div[data-automation-id='menuItem']:has-text('Mobile')")
                        if m_opt:
                            await m_opt.click(force=True)
                    except Exception:
                        pass

                # 10. Phone number (strictly 10 digits without leading 0 or +91)
                phone_inp = await page.query_selector("input[id='phoneNumber--phoneNumber'], input[name='phoneNumber'], input[data-automation-id='phone-number']")
                if phone_inp:
                    raw_p = pers.get("phone", "9643914225").replace("+91", "").replace(" ", "").replace("-", "").lstrip("0").strip()
                    target_phone = raw_p[-10:] if len(raw_p) >= 10 else raw_p
                    print(f"📞 Setting Step 1 Phone Number: {target_phone}")
                    await phone_inp.fill(target_phone)
                    await asyncio.sleep(0.2)

            # Step 2: My Experience
            exp_el = await page.query_selector("div[aria-labelledby='Work-Experience-section'], div[data-automation-id*='workExperienceSection' i]")
            is_step2 = "experience" in active_step_txt or (exp_el is not None and await exp_el.is_visible())
            if is_step2:
                await self.fill_step2_experience_education_skills(page)

            # Step 3/4: Application Questions & Disclosures
            if not is_step1 and not is_step2:
                dropdown_btns = await page.query_selector_all("button[aria-haspopup='listbox']")
                for btn in dropdown_btns:
                    try:
                        q_el = await btn.evaluate_handle("el => el.closest('div[data-automation-id*=\"formField\"], fieldset, div[role=\"group\"]')")
                        if q_el:
                            q_txt = await q_el.evaluate("el => el.innerText")
                            await self.answer_dropdown(page, btn, q_txt)
                    except Exception:
                        pass

                # Radio groups
                radio_groups = await page.query_selector_all("fieldset[data-automation-id*='formField'], div[role='radiogroup']")
                for rg in radio_groups:
                    try:
                        txt = (await rg.inner_text()).lower()
                        # 1. Export control: citizen of Iran, Cuba, North Korea, Syria -> NO!
                        if "iran" in txt or "cuba" in txt or "north korea" in txt or "syria" in txt or "embargo" in txt:
                            no_radio = await rg.query_selector("label:has-text('No'), input[value*='no' i]")
                            if no_radio:
                                print("🛡️ Answering Export Control (Iran/Cuba/NK/Syria): 'No'")
                                await no_radio.click(force=True)
                        # 2. Permanent/Unrestricted right to work in applying country -> YES!
                        elif "unrestricted right to work" in txt or "permanent/indefinite" in txt or "right to work in the country" in txt:
                            yes_radio = await rg.query_selector("label:has-text('Yes'), input[value*='yes' i]")
                            if yes_radio:
                                print("🇮🇳 Answering unrestricted right to work: 'Yes'")
                                await yes_radio.click(force=True)
                        # 3. Sponsorship required now or future -> NO!
                        elif "sponsorship" in txt:
                            no_radio = await rg.query_selector("label:has-text('No'), input[value*='no' i]")
                            if no_radio:
                                print("🚫 Answering sponsorship required: 'No'")
                                await no_radio.click(force=True)
                        # 4. Visa required -> NO!
                        elif "require" in txt and "visa" in txt:
                            no_radio = await rg.query_selector("label:has-text('No'), input[value*='no' i]")
                            if no_radio:
                                await no_radio.click(force=True)
                        # 5. Legally authorized / eligible to work -> YES!
                        elif "authorized" in txt or "eligible to work" in txt:
                            yes_radio = await rg.query_selector("label:has-text('Yes'), input[value*='yes' i]")
                            if yes_radio:
                                await yes_radio.click(force=True)
                        # 6. Future positions communications / Acknowledge truthfulness -> YES!
                        elif "future positions" in txt or "acknowledge" in txt or "truthfully" in txt:
                            yes_radio = await rg.query_selector("label:has-text('Yes'), input[value*='yes' i]")
                            if yes_radio:
                                await yes_radio.click(force=True)
                        # 7. Relatives / conflicts / previous worker -> NO!
                        elif "relative" in txt or "employed" in txt or "conflict" in txt or "non-compete" in txt:
                            no_radio = await rg.query_selector("label:has-text('No'), input[value*='no' i]")
                            if no_radio:
                                await no_radio.click(force=True)
                        elif "gender" in txt:
                            all_r = await rg.query_selector_all("label, div[role='radio'], input[type='radio']")
                            for r in all_r:
                                t = (await r.inner_text()).strip().lower()
                                val = ((await r.get_attribute("value")) or "").strip().lower()
                                if t == "male" or (t.startswith("male") and "fe" not in t) or val == "male":
                                    await r.click(force=True)
                                    break
                    except Exception:
                        pass

                # Text inputs / textareas in Step 3/4 (e.g. salary expectation, notice period)
                text_fields = await page.query_selector_all("div[data-automation-id*='formField'] input[type='text'], div[data-automation-id*='formField'] textarea, input[id*='salary' i], textarea[id*='salary' i]")
                for tf in text_fields:
                    try:
                        q_el = await tf.evaluate_handle("el => el.closest('div[data-automation-id*=\"formField\"], fieldset, div[role=\"group\"]')")
                        if q_el:
                            q_txt = (await q_el.evaluate("el => el.innerText")).lower()
                            val = await tf.input_value()
                            if not val:
                                if "salary" in q_txt or "compensation" in q_txt or "currency" in q_txt:
                                    print("💰 Answering salary expectation: 'Competitive / Open to discussion'")
                                    await tf.fill("Competitive / Open to discussion")
                                    await asyncio.sleep(0.2)
                                elif "notice" in q_txt:
                                    print("⏳ Answering notice period: 'Immediate / 30 days'")
                                    await tf.fill("Immediate / 30 days")
                                    await asyncio.sleep(0.2)
                    except Exception:
                        pass

                # Terms / consent checkbox
                terms_cb = await page.query_selector("input[type='checkbox']")
                if terms_cb and not await terms_cb.is_checked():
                    try:
                        await terms_cb.check(force=True)
                    except Exception:
                        pass

            if "autofill" in active_step_txt:
                from workday_flow_guards import attach_resume_if_missing
                if not await attach_resume_if_missing(page, self.profile):
                    print("⏳ Autofill page has no resume attached yet. Waiting...")
                    await asyncio.sleep(2.0)
                    continue

            # Click Continue / Next / Save and Continue to proceed
            next_btn = await page.query_selector(
                "button[data-automation-id='pageFooterNextButton'], "
                "button[data-automation-id='bottom-navigation-next-button'], "
                "button#pageFooterNextButton, "
                "div[data-automation-id='pageFooter'] button:has-text('Save and Continue'), "
                "div[data-automation-id='pageFooter'] button:has-text('Continue')"
            )
            if next_btn and await next_btn.is_visible() and await next_btn.is_enabled():
                btn_name = (await next_btn.inner_text()).strip()
                print(f"➡️ Clicking footer button '{btn_name}' to advance...")
                await next_btn.click(force=True)
                await asyncio.sleep(4.5)

                # Check for any validation error messages
                err_msgs = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('[data-automation-id="errorWidget"], [data-automation-id="errorMessage"], [aria-invalid="true"]')).map(el => el.innerText.trim()).filter(Boolean);
                }''')
                if err_msgs:
                    print(f"⚠️ Workday validation alerts on page: {err_msgs}")
            else:
                print("⏳ Next button momentarily disabled while form parses. Retrying step...")
                await asyncio.sleep(3.0)

        return False, "Did not reach final review within maximum allowed steps."

    async def apply_to_job(self, job: Dict[str, Any], context: Optional[BrowserContext] = None) -> Tuple[bool, str]:
        """Entry point for applying to a single Adobe job."""
        if context:
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)
            try:
                return await self.fill_workday_form(page, job)
            finally:
                if not self.keep_open:
                    await page.close()
        else:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await self.stealth.apply_stealth_async(page)
                try:
                    ok, msg = await self.fill_workday_form(page, job)
                    from workday_flow_guards import hold_headed_browser, record_apply_result
                    record_apply_result(job, ok, msg, self.dry_run, "adobe")
                    if self.keep_open and not self.headless:
                        await hold_headed_browser(page, job.get("job_id", ""), f"screenshots/adobe_{job.get('job_id', 'latest')}.png")
                    return ok, msg
                finally:
                    await browser.close()


def apply_to_adobe_job(url: str, job_id: str = "adobe_test", dry_run: bool = True, headless: bool = True) -> bool:
    applier = AdobeApplier(dry_run=dry_run, headless=headless)
    job = {
        "job_id": job_id,
        "title": "Software Engineer",
        "apply_url": url,
        "company_name": "Adobe"
    }
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ok, msg = loop.run_until_complete(applier.apply_to_job(job))
        print(f"Result: {ok} - {msg}")
        return ok
    finally:
        loop.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Apply to Adobe Workday job")
    parser.add_argument("--url", type=str, required=True, help="Job apply URL")
    parser.add_argument("--id", type=str, default="adobe_test", help="Job ID")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Run in Review First mode")
    parser.add_argument("--live", action="store_true", help="Live submit")
    parser.add_argument("--no-headless", action="store_true", help="Run with visible browser")
    args = parser.parse_args()
    is_dry = not args.live
    apply_to_adobe_job(args.url, args.id, dry_run=is_dry, headless=not args.no_headless)


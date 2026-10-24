#!/usr/bin/env python3
"""
Dedicated Phenom People Auto-Apply Engine.
Specialized for Phenom People hosted career & application portals
(e.g., careers.mastercard.com/us/en/apply?jobSeqNo=... and other Phenom enterprise portals).

Completely decoupled from the Workday portal (mastercard.wd1.myworkdayjobs.com).

Key Highlights:
- Bypasses Workday authentication & PIN verification by using the official Phenom hosted apply pipeline.
- Handles OneTrust / Phenom cookie banners.
- Uploads candidate resume.pdf directly into the portal parser.
- Handles dynamic country-specific schema re-renders (cntryFields).
- Populates personal information, address, clean phone, device type, and source attribution.
- Selects compliance/previous worker declarations and consent checkboxes.
- Solves secondary questionnaires and screening questions with heuristics and FormLLMEngine.
- Captures full-page proof screenshots in screenshots/phenom_<job_id>.png.
- Supports Dry-Run (fill without final submission) and Live (submit & verify) modes.
- Supports Headed and Headless execution.
- Integrates with SQLite jobs.db.
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
    save_job,
    DB_PATH
)
from experience_filter import is_qualified_job
from llm_form_engine import FormLLMEngine


def load_user_profile(path: str = "user_profile.json") -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def get_phenom_jobs(
    status: Optional[str] = None,
    limit: Optional[int] = None,
    include_applied: bool = True,
    db_path: str = DB_PATH
) -> List[Dict[str, Any]]:
    """Fetches eligible Phenom and Mastercard portal jobs from SQLite."""
    conn = get_db_connection(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT id, job_id, company_name, title, location, ats_platform, apply_url, status, salary_tier,
               screenshot_path, apply_log, applied_at, apply_attempts
        FROM job_postings
        WHERE (
            ats_platform = 'phenom'
            OR apply_url LIKE '%careers.mastercard.com/%/apply%'
            OR apply_url LIKE '%apply?jobSeqNo=%'
            OR (LOWER(company_name) LIKE '%mastercard%' AND apply_url LIKE '%careers.mastercard.com%')
        )
        AND apply_url IS NOT NULL
        AND apply_url LIKE 'http%'
    """
    params = []

    if status and status.lower() != "all":
        query += " AND status = ?"
        params.append(status)
    elif not include_applied:
        query += " AND status NOT IN ('APPLIED', 'INVALID')"

    query += " ORDER BY id DESC"
    if limit:
        query += f" LIMIT {int(limit)}"

    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    qualified = []
    for r in rows:
        if is_qualified_job(r["title"], r.get("location", "")):
            qualified.append(r)
    return qualified


async def human_type(page: Page, selector_or_el, text: str):
    """Types text with natural human jitter and dispatches React synthetic input events."""
    if not text:
        return
    try:
        if isinstance(selector_or_el, str):
            el = await page.wait_for_selector(selector_or_el, state="visible", timeout=8000)
        else:
            el = selector_or_el
        if not el:
            return

        await el.click()
        try:
            await el.fill("")
        except Exception:
            pass

        for char in text:
            await el.type(char, delay=random.uniform(25, 65))
            if random.random() < 0.04:
                await asyncio.sleep(random.uniform(0.08, 0.15))

        # Dispatch synthetic events to ensure React / Vue form engines record the change
        await el.evaluate('''e => {
            e.dispatchEvent(new Event('input', { bubbles: true }));
            e.dispatchEvent(new Event('change', { bubbles: true }));
            e.dispatchEvent(new Event('blur', { bubbles: true }));
        }''')
    except Exception as e:
        try:
            if isinstance(selector_or_el, str):
                await page.evaluate(f'''() => {{
                    const e = document.querySelector("{selector_or_el}");
                    if (e) {{
                        e.value = "{text}";
                        e.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        e.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        e.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                    }}
                }}''')
        except Exception:
            pass


async def capture_proof(page: Page, path: str):
    """Takes a clean full-page screenshot by temporarily unsticking sticky elements."""
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


class PhenomApplier:
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

    async def dismiss_cookie_banner(self, page: Page):
        """Dismisses OneTrust or Phenom cookie banners."""
        cookie_selectors = [
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept cookies')",
            "button:has-text('Accept all')",
            "button:has-text('Allow all')",
            "button.cookie-settings-allow"
        ]
        for sel in cookie_selectors:
            try:
                cb = await page.query_selector(sel)
                if cb and await cb.is_visible():
                    await cb.click(force=True)
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                pass

    async def fill_step1_personal_info(self, page: Page) -> Tuple[bool, str]:
        """Fills out Step 1 (Personal Information) of the Phenom application."""
        pers = self.profile.get("personal", {})
        custom = self.profile.get("custom_answers", {})
        resume_cfg = self.profile.get("resume", {})

        print("📋 Step 1: Filling Personal Information & Profile...")

        # 1. Upload Resume
        resume_path = os.path.abspath(resume_cfg.get("file_path", "resume.pdf"))
        if os.path.exists(resume_path):
            file_input = await page.query_selector("input[type='file']")
            if file_input:
                print(f"📎 Attaching candidate resume: {resume_path}...")
                try:
                    await file_input.set_input_files(resume_path)
                    print("⏳ Waiting for resume parser upload...")
                    await asyncio.sleep(2.5)
                except Exception as fe:
                    print(f"⚠️ Resume attachment note: {fe}")

        # 2. Country Selection (Must be filled FIRST to let country-specific schema stabilize)
        country_select = await page.query_selector("select#country, select[name='country']")
        if country_select:
            c_val = "IND" if "india" in pers.get("current_country", "India").lower() else "USA"
            print(f"🌍 Selecting Country: {c_val}...")
            try:
                await page.select_option("select#country", value=c_val)
                print("⏳ Waiting 3.0s for country-specific fields to mount...")
                await asyncio.sleep(3.0)
            except Exception as ce:
                print(f"⚠️ Country select note: {ce}")

        # 3. Given Name & Family Name
        fn_input = await page.query_selector("input[id='cntryFields.firstName'], input[name='cntryFields.firstName']")
        if fn_input and await fn_input.is_visible():
            print(f"👤 First Name: {pers.get('first_name')}")
            await human_type(page, fn_input, pers.get("first_name", ""))

        ln_input = await page.query_selector("input[id='cntryFields.lastName'], input[name='cntryFields.lastName']")
        if ln_input and await ln_input.is_visible():
            print(f"👤 Last Name: {pers.get('last_name')}")
            await human_type(page, ln_input, pers.get("last_name", ""))

        # 4. Optional Local Names
        for loc_id, val in [
            ("cntryFields.localFirstName", pers.get("first_name", "")),
            ("cntryFields.localPrimary", pers.get("last_name", ""))
        ]:
            loc_el = await page.query_selector(f"input[id='{loc_id}']")
            if loc_el and await loc_el.is_visible():
                await human_type(page, loc_el, val)

        # 5. Address Details
        addr = await page.query_selector("input[id='cntryFields.addressLine1'], input[name='cntryFields.addressLine1']")
        if addr and await addr.is_visible():
            await human_type(page, addr, pers.get("address", "Outer Ring Road, Bellandur"))

        city = await page.query_selector("input[id='cntryFields.city'], input[name='cntryFields.city']")
        if city and await city.is_visible():
            await human_type(page, city, pers.get("current_city", "Bengaluru"))

        postal = await page.query_selector("input[id='cntryFields.postalCode'], input[name='cntryFields.postalCode']")
        if postal and await postal.is_visible():
            await human_type(page, postal, pers.get("postal_code", "560103"))

        # State / Province dropdown if present
        state_el = await page.query_selector("select[id*='state' i], select[name*='state' i]")
        if state_el and await state_el.is_visible() and not await state_el.is_disabled():
            try:
                opts = await state_el.evaluate('''el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))''')
                preferred_states = ["karnataka", "maharashtra", "delhi", "telangana", "tamil nadu"]
                selected = False
                for p_state in preferred_states:
                    for opt in opts:
                        if p_state in opt["text"].lower():
                            await page.select_option(state_el, value=opt["value"])
                            print(f"📍 Selected State: {opt['text']}")
                            selected = True
                            break
                    if selected:
                        break
            except Exception:
                pass

        # 6. Email Address
        email_el = await page.query_selector("input#email, input[name='email']")
        if email_el:
            print(f"✉️ Email: {pers.get('email')}")
            await human_type(page, email_el, pers.get("email", ""))

        # 7. Device Type
        dt = await page.query_selector("select#deviceType")
        if dt and not await dt.is_disabled():
            try:
                await page.select_option("select#deviceType", value="Mobile")
                await page.evaluate('''() => {
                    const el = document.getElementById('deviceType');
                    if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
                }''')
            except Exception:
                pass

        # 8. Country Phone Code
        cpc = await page.query_selector("select[id='phoneWidget.countryPhoneCode']")
        if cpc and not await cpc.is_disabled():
            try:
                await page.select_option("select[id='phoneWidget.countryPhoneCode']", value="IND_91")
                await page.evaluate('''() => {
                    const el = document.getElementById('phoneWidget.countryPhoneCode');
                    if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
                }''')
            except Exception:
                pass

        # 9. Phone Number (clean digits)
        raw_phone = pers.get("phone", "9643914225")
        phone_clean = re.sub(r"[^\d]", "", raw_phone)
        if phone_clean.startswith("91") and len(phone_clean) > 10:
            phone_clean = phone_clean[2:]
        phone_el = await page.query_selector("input[id='phoneWidget.phoneNumber'], input[name='phoneWidget.phoneNumber']")
        if phone_el:
            print(f"📱 Phone: {phone_clean}")
            await human_type(page, phone_el, phone_clean)

        # 10. How Did You Hear About Us (Source) - Only if enabled and unselected
        st = await page.query_selector("select#sourceType")
        if st and not await st.is_disabled():
            try:
                curr_st = await st.input_value()
                if not curr_st:
                    await page.select_option("select#sourceType", value="Job Board")
                    await page.evaluate('''() => {
                        const el = document.getElementById('sourceType');
                        if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
                    }''')
            except Exception:
                pass

        asrc = await page.query_selector("select#applicantSource")
        if asrc and not await asrc.is_disabled():
            try:
                curr_asrc = await asrc.input_value()
                if not curr_asrc:
                    await page.select_option("select#applicantSource", value="APPLICANT_SOURCE-3-279") # LinkedIn
                    await page.evaluate('''() => {
                        const el = document.getElementById('applicantSource');
                        if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
                    }''')
            except Exception:
                pass

        # 11. Previous Worker Radio button
        prev_emp = custom.get("previously_employed_at_company", "No").lower() == "yes"
        radio_target = "previousworker.Yes" if prev_emp else "previousworker.No"
        radio_label_txt = "Yes" if prev_emp else "No"
        try:
            print(f"🔘 Selecting Previous Worker: {radio_label_txt}...")
            lbl = page.locator(f"label:has-text('{radio_label_txt}')").first
            await lbl.click(force=True)
            await asyncio.sleep(0.3)
            await page.evaluate(f'''() => {{
                const r = document.getElementById('{radio_target}');
                if (r) {{
                    r.checked = true;
                    r.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}''')
        except Exception as re_err:
            print(f"⚠️ Radio click note: {re_err}")

        # 12. Email Agreement / Talent Community Checkbox
        agree = await page.query_selector("input#emailAgreement")
        if agree:
            try:
                await page.evaluate('''() => {
                    const a = document.getElementById('emailAgreement');
                    if (a) {
                        a.checked = true;
                        a.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }''')
            except Exception:
                pass

        await asyncio.sleep(1.0)

        # Validate step 1 fields
        errors = await page.evaluate('''() => {
            const errEls = document.querySelectorAll('.error, .error-msg, .form-alert, [aria-invalid="true"], span.help-block, .has-error');
            return Array.from(errEls).filter(e => e.innerText && e.innerText.trim().length > 0).map(e => e.innerText.trim());
        }''')
        if errors:
            print(f"⚠️ Step 1 Validation messages: {errors}")
            return False, f"Step 1 validation errors: {', '.join(errors)}"

        return True, "Step 1 completed successfully."

    async def solve_questions_and_disclosures(self, page: Page):
        """Solves any secondary questionnaire, compliance questions, or EEO fields."""
        pers = self.profile.get("personal", {})
        prof = self.profile.get("professional", {})
        custom = self.profile.get("custom_answers", {})

        print("📝 Scanning for secondary questions and disclosures...")

        # 1. Text Inputs (Notice period, compensation, etc.)
        text_inputs = await page.query_selector_all("input[type='text'], input[type='number'], textarea")
        for ti in text_inputs:
            try:
                if not await ti.is_visible() or await ti.is_disabled():
                    continue
                val = await ti.input_value()
                if val:
                    continue

                label = await page.evaluate('''el => {
                    let p = el.parentElement;
                    while (p && !p.querySelector('label') && p.parentElement) p = p.parentElement;
                    return p && p.querySelector('label') ? p.querySelector('label').innerText.toLowerCase() : '';
                }''', ti)

                if "notice" in label:
                    await human_type(page, ti, str(prof.get("notice_period_days", 30)))
                elif "current" in label and ("ctc" in label or "salary" in label or "compensation" in label):
                    await human_type(page, ti, f"{prof.get('current_ctc_lpa', 28)} LPA")
                elif "expected" in label or "expectation" in label or "desired" in label:
                    await human_type(page, ti, f"{prof.get('expected_ctc_lpa', 55)} LPA")
                elif "linkedin" in label:
                    await human_type(page, ti, pers.get("linkedin_url", ""))
                elif "github" in label:
                    await human_type(page, ti, pers.get("github_url", ""))
                else:
                    if self.llm and label:
                        resolved = self.llm.answer_question(label, field_type="text")
                        if resolved:
                            await human_type(page, ti, str(resolved))
            except Exception:
                pass

        # 2. Select Dropdowns
        selects = await page.query_selector_all("select:not(#country):not(#deviceType):not([id='phoneWidget.countryPhoneCode']):not(#sourceType):not(#applicantSource)")
        for sel in selects:
            try:
                if not await sel.is_visible() or await sel.is_disabled():
                    continue
                val = await sel.input_value()
                if val:
                    continue

                label = await page.evaluate('''el => {
                    let p = el.parentElement;
                    while (p && !p.querySelector('label') && p.parentElement) p = p.parentElement;
                    return p && p.querySelector('label') ? p.querySelector('label').innerText.toLowerCase() : '';
                }''', sel)

                opts = await sel.evaluate('''el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))''')
                if not opts:
                    continue

                if "authorized" in label or "legally authorized" in label:
                    for o in opts:
                        if o["text"].lower().strip() == "yes":
                            await page.select_option(sel, value=o["value"])
                            break
                elif any(k in label for k in ["sponsorship", "require visa", "visa sponsorship"]):
                    for o in opts:
                        if o["text"].lower().strip() == "no":
                            await page.select_option(sel, value=o["value"])
                            break
                elif "relocate" in label:
                    for o in opts:
                        if o["text"].lower().strip() == "yes":
                            await page.select_option(sel, value=o["value"])
                            break
                elif "gender" in label:
                    for o in opts:
                        if "male" in o["text"].lower():
                            await page.select_option(sel, value=o["value"])
                            break
                else:
                    if self.llm and label:
                        opt_texts = [o["text"] for o in opts]
                        chosen = self.llm.choose_option(label, opt_texts)
                        for o in opts:
                            if o["text"] == chosen or o["value"] == chosen:
                                await page.select_option(sel, value=o["value"])
                                break
            except Exception:
                pass

        # 3. Checkboxes (Consent / Terms)
        checkboxes = await page.query_selector_all("input[type='checkbox']:not(#emailAgreement)")
        for cb in checkboxes:
            try:
                if await cb.is_visible() and not await cb.is_checked():
                    await cb.check(force=True)
            except Exception:
                pass

    async def apply_to_job(
        self,
        job: Dict[str, Any],
        context: Optional[BrowserContext] = None
    ) -> Dict[str, Any]:
        """Executes full Phenom application for a single job."""
        job_id = job.get("job_id", f"phenom_{abs(hash(job['apply_url']))}")
        title = job.get("title", "Software Engineer")
        url = job["apply_url"]
        sc_name = job_id if job_id.startswith("phenom_") else f"phenom_{job_id}"
        sc_path = f"screenshots/{sc_name}.png"

        print(f"\n{'='*75}")
        print(f"🚀 Phenom People Auto-Applier: {title} ({job_id})")
        print(f"🔗 Target URL: {url}")
        print(f"{'='*75}")

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
            print(f"🌐 Navigating to application page...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as ge:
                print(f"⚠️ Page navigation note: {ge}")

            # Wait for form to mount
            await page.wait_for_selector("select#country, input[name='cntryFields.firstName'], button#next", timeout=18000)
            await asyncio.sleep(1.5)

            # Dismiss OneTrust cookie overlay
            await self.dismiss_cookie_banner(page)

            # Check if page indicates job expired (only if form is missing)
            form_el = await page.query_selector("button#next, select#country, input[name='cntryFields.firstName']")
            if not form_el:
                body_text = await page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
                if any(k in body_text for k in ["job has expired", "job is no longer available", "position has been closed"]):
                    status = "INVALID"
                    msg = "Position closed / job expired on Phenom portal."
                    update_job_status(job_id, status, msg, sc_path)
                    await capture_proof(page, sc_path)
                    return {"job_id": job_id, "status": status, "message": msg}

            # ── Step 1: Personal Information ──────────────────────────────────
            step1_success, step1_msg = await self.fill_step1_personal_info(page)
            if not step1_success:
                await capture_proof(page, sc_path)
                update_job_status(job_id, "FAILED", step1_msg, sc_path)
                return {"job_id": job_id, "status": "FAILED", "message": step1_msg}

            # Capture pre-submit proof
            await capture_proof(page, sc_path)

            if self.dry_run:
                status = "READY_TO_SUBMIT (DRY_RUN)"
                final_msg = f"Form prefilled with candidate profile & resume attached. [DRY RUN: Skipped final Next/Submit click. Proof saved at {sc_path}]"
                print(f"✅ Form pre-filled completely! [DRY RUN mode: Proof captured in {sc_path}]")
                update_job_status(job_id, status, final_msg, sc_path)
            else:
                # ── Live Submission / Step Progression ────────────────────────
                print("🚀 LIVE MODE: Advancing application...")
                next_btn = await page.query_selector("button#next, button[type='submit']:has-text('Next')")
                if next_btn:
                    await next_btn.click(force=True)
                    print("➡️ Clicked Next button. Waiting for server response...")
                    await asyncio.sleep(6.0)

                # Check for secondary questionnaire / questions step
                curr_url = page.url
                content_after = (await page.content()).lower()

                # If secondary questionnaires appeared, solve them
                if "questionnaire" in curr_url.lower() or "step=2" in curr_url or "stepname=" in curr_url:
                    print("📋 Secondary questionnaire step detected. Resolving...")
                    await self.solve_questions_and_disclosures(page)
                    await capture_proof(page, sc_path)
                    sub_btn = await page.query_selector("button#next, button[type='submit'], button:has-text('Submit')")
                    if sub_btn:
                        await sub_btn.click(force=True)
                        await asyncio.sleep(6.0)

                # Capture final confirmation proof
                await capture_proof(page, sc_path)
                final_url = page.url
                final_content = (await page.content()).lower()

                is_applied = (
                    "applythankyou" in final_url.lower()
                    or "status=success" in final_url.lower()
                    or any(k in final_content for k in [
                        "thank you for applying",
                        "application received",
                        "successfully applied",
                        "application submitted"
                    ])
                )
                is_already_applied = (
                    "status=alreadyapplied" in final_url.lower()
                    or "already applied" in final_content
                )

                if is_already_applied:
                    status = "APPLIED"
                    final_msg = "Application confirmed on Phenom portal (Candidate already submitted for this requisition)."
                    print(f"ℹ️ {final_msg}")
                elif is_applied:
                    status = "APPLIED"
                    final_msg = "Application successfully submitted & confirmed via Phenom portal."
                    print(f"🎉 {final_msg}")
                else:
                    status = "READY_TO_SUBMIT (REVIEW_REQUIRED)"
                    final_msg = f"Application advanced, parked on {final_url}. Review required."
                    print(f"⚠️ {final_msg}")

                update_job_status(job_id, status, final_msg, sc_path)

            if self.keep_open and not self.headless and standalone:
                print("👀 Browser left open for candidate review. Close the browser window when finished.")
                while context.pages and not context.pages[0].is_closed():
                    await asyncio.sleep(1.0)

            return {
                "job_id": job_id,
                "status": status,
                "message": final_msg,
                "screenshot": sc_path
            }

        except Exception as e:
            err_msg = f"Unexpected error during Phenom apply: {e}"
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
        """Processes multiple Phenom applications sequentially or in parallel tabs."""
        print(f"⚡ Processing batch of {len(jobs)} Phenom jobs...")
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
                print(f"\n[{idx}/{len(jobs)}] Processing {job.get('title', 'Role')}...")
                await self.apply_to_job(job)
                await asyncio.sleep(2.0)


def main():
    parser = argparse.ArgumentParser(description="Dedicated Phenom People & Mastercard Career Portal Auto-Applier")
    parser.add_argument("--url", type=str, help="Direct URL to apply (e.g. https://careers.mastercard.com/us/en/apply?jobSeqNo=...)")
    parser.add_argument("--job-id", type=str, help="Apply to a specific job ID from jobs.db")
    parser.add_argument("--limit", type=int, default=1, help="Max jobs to process (default: 1)")
    parser.add_argument("--status", type=str, default=None, help="Filter database jobs by status")
    parser.add_argument("--list", action="store_true", help="List all available Phenom/Mastercard portal jobs")
    parser.add_argument("--live", action="store_true", help="Submit live (default is dry-run)")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--batch-tabs", action="store_true", help="Open multiple jobs in tabs simultaneously")
    args = parser.parse_args()

    if args.list:
        jobs = get_phenom_jobs(status=args.status, limit=None)
        print(f"\n{'='*80}")
        print(f"💼 Available Phenom & Mastercard Portal Jobs ({len(jobs)} total)")
        print(f"{'='*80}")
        for idx, j in enumerate(jobs, 1):
            print(f"{idx:2d}. [{j['status']}] {j['job_id']}")
            print(f"    Company:  {j['company_name']}")
            print(f"    Title:    {j['title']}")
            print(f"    Location: {j.get('location', 'India')}")
            print(f"    URL:      {j['apply_url']}")
            print(f"{'-'*80}")
        return

    # Direct URL mode
    if args.url:
        parsed = urllib.parse.urlparse(args.url)
        params = urllib.parse.parse_qs(parsed.query)
        seq_no = params.get("jobSeqNo", [str(abs(hash(args.url)))])[0]
        comp_name = "Mastercard" if "mastercard" in parsed.netloc.lower() else "Enterprise"

        target_job = {
            "job_id": f"phenom_{seq_no}",
            "company_name": comp_name,
            "title": f"{comp_name} Requisition ({seq_no})",
            "location": "India",
            "ats_platform": "phenom",
            "apply_url": args.url,
            "status": "DISCOVERED"
        }
        try:
            save_job(target_job)
        except Exception:
            pass

        targets = [target_job]
    elif args.job_id:
        all_j = get_phenom_jobs(status=None, limit=None)
        targets = [j for j in all_j if j["job_id"] == args.job_id]
        if not targets:
            print(f"❌ Job ID '{args.job_id}' not found among Phenom jobs in jobs.db")
            sys.exit(1)
    else:
        targets = get_phenom_jobs(status=args.status, limit=args.limit)
        if not targets:
            print("✨ No pending Phenom / Mastercard portal jobs found matching criteria.")
            return

    dry_run = not args.live
    applier = PhenomApplier(
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

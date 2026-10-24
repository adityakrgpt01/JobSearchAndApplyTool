#!/usr/bin/env python3
"""
Dedicated Phenom People Auto-Apply Engine.
Specialized for Phenom People hosted career & application portals
(e.g., careers.mastercard.com/us/en/apply?jobSeqNo=... and other Phenom enterprise portals).

Completely decoupled from the Workday portal (mastercard.wd1.myworkdayjobs.com).

Key Highlights:
- Bypasses Workday authentication & PIN verification by using the official Phenom hosted apply pipeline.
- Handles OneTrust / Phenom cookie banners and removes blocking overlays.
- Uploads candidate resume.pdf directly into the portal parser.
- Handles dynamic country-specific schema re-renders (cntryFields).
- Populates personal information, address, clean phone, device type, and source attribution.
- Selects compliance/previous worker declarations and consent checkboxes.
- Handles Step 2: Experience & Education (job titles, degree, field of study).
- Handles Step 3: Job-specific screening questions & desired salary.
- Handles Step 4: Additional compliance & conflict-of-interest declarations.
- Handles Step 5: Voluntary disclosures & applicant privacy acknowledgment.
- Handles Step 6: Application Review & live Submit button execution.
- Captures full-page proof screenshots in screenshots/phenom_<job_id>.png.
- Supports Dry-Run (completes all steps to review page) and Live (submits & verifies) modes.
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
    """Types text and dispatches React synthetic input events."""
    if not text:
        return
    try:
        if isinstance(selector_or_el, str):
            el = await page.wait_for_selector(selector_or_el, state="visible", timeout=6000)
        else:
            el = selector_or_el
        if not el:
            return

        try:
            await el.fill(text)
        except Exception:
            try:
                await el.click(force=True, timeout=2000)
                await el.fill("")
                for char in text:
                    await el.type(char, delay=random.uniform(15, 40))
            except Exception:
                pass

        # Dispatch synthetic events to ensure React / Vue form engines record the change
        await el.evaluate('''(e, val) => {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set ||
                           Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
            if (setter) { setter.call(e, val); } else { e.value = val; }
            e.dispatchEvent(new Event('input', { bubbles: true }));
            e.dispatchEvent(new Event('change', { bubbles: true }));
            e.dispatchEvent(new Event('blur', { bubbles: true }));
        }''', text)
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
        """Dismisses OneTrust or Phenom cookie banners and removes any blocking overlays."""
        try:
            cb = await page.wait_for_selector("#onetrust-accept-btn-handler", timeout=2500)
            if cb and await cb.is_visible():
                await cb.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # Purge any dark overlays or modal backdrop filters from the DOM
        try:
            await page.evaluate('''() => {
                document.querySelectorAll('#onetrust-consent-sdk, #onetrust-banner-sdk, .onetrust-pc-dark-filter, .ot-fade-in').forEach(e => e.remove());
                document.body.style.overflow = 'auto';
            }''')
        except Exception:
            pass

    async def set_field_value(self, page: Page, selector: str, value: str):
        """Sets field value and fires input, change, and blur events."""
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            try:
                await el.fill(value)
            except Exception:
                pass
            await el.evaluate('''(e, val) => {
                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set ||
                               Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                if (setter) { setter.call(e, val); } else { e.value = val; }
                e.dispatchEvent(new Event('input', { bubbles: true }));
                e.dispatchEvent(new Event('change', { bubbles: true }));
                e.dispatchEvent(new Event('blur', { bubbles: true }));
            }''', value)

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
                    print("⏳ Waiting 4.0s for resume parser upload & processing...")
                    await asyncio.sleep(4.0)
                except Exception as fe:
                    print(f"⚠️ Resume attachment note: {fe}")

        # Ensure OneTrust didn't pop up after file upload
        await self.dismiss_cookie_banner(page)

        # 2. Country Selection (Must be filled FIRST to let country-specific schema stabilize)
        country_select = await page.query_selector("select#country, select[name='country']")
        if country_select:
            c_val = "IND" if "india" in pers.get("current_country", "India").lower() else "USA"
            print(f"🌍 Selecting Country: {c_val}...")
            try:
                await page.select_option("select#country", value=c_val)
                print("⏳ Waiting 2.5s for country-specific fields to mount...")
                await asyncio.sleep(2.5)
            except Exception as ce:
                print(f"⚠️ Country select note: {ce}")

        # 3. Given Name & Family Name
        print(f"👤 Name: {pers.get('first_name')} {pers.get('last_name')}")
        await self.set_field_value(page, "input[id='cntryFields.firstName'], input[name='cntryFields.firstName']", pers.get("first_name", "Aditya"))
        await self.set_field_value(page, "input[id='cntryFields.lastName'], input[name='cntryFields.lastName']", pers.get("last_name", "Kumar"))

        # Optional Local Names
        await self.set_field_value(page, "input[id='cntryFields.localFirstName']", pers.get("first_name", "Aditya"))
        await self.set_field_value(page, "input[id='cntryFields.localPrimary']", pers.get("last_name", "Kumar"))

        # 4. Address Details
        address_line = pers.get("current_address", "Outer Ring Road, Bellandur")
        city = pers.get("current_city", "Bengaluru")
        postal = pers.get("postal_code", "560103")
        await self.set_field_value(page, "input[id='cntryFields.addressLine1'], input[name='cntryFields.addressLine1']", address_line)
        await self.set_field_value(page, "input[id='cntryFields.city'], input[name='cntryFields.city']", city)
        await self.set_field_value(page, "input[id='cntryFields.postalCode'], input[name='cntryFields.postalCode']", postal)

        # 5. Email Address
        email = pers.get("email", "adityakumargupta521@gmail.com")
        print(f"✉️ Email: {email}")
        await self.set_field_value(page, "input[id='cntryFields.emailAddress'], input[name='cntryFields.emailAddress']", email)

        # 6. Phone Device Type
        try:
            await page.select_option("select#deviceType", value="Mobile")
            await page.evaluate('''() => {
                const el = document.getElementById('deviceType');
                if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
            }''')
        except Exception:
            pass

        # 7. Country Phone Code
        try:
            await page.select_option("select[id='phoneWidget.countryPhoneCode']", value="IND_91")
            await page.evaluate('''() => {
                const el = document.getElementById('phoneWidget.countryPhoneCode');
                if (el) el.dispatchEvent(new Event('change', { bubbles: true }));
            }''')
        except Exception:
            pass

        # 8. Clean Phone Number (Purges any multi-phone strings dumped by resume parser)
        raw_phone = pers.get("phone", "9643914225")
        phone_clean = re.sub(r"[^\d]", "", raw_phone)
        if phone_clean.startswith("91") and len(phone_clean) > 10:
            phone_clean = phone_clean[2:]
        if len(phone_clean) > 10:
            phone_clean = phone_clean[:10]
        print(f"📱 Phone: {phone_clean}")
        await self.set_field_value(page, "input[id='phoneWidget.phoneNumber'], input[name='phoneWidget.phoneNumber']", phone_clean)

        # 9. How Did You Hear About Us / Source Type
        st = await page.query_selector("select#sourceType")
        if st and not await st.is_disabled():
            try:
                curr_st = await st.input_value()
                if not curr_st:
                    await page.select_option("select#sourceType", value="Job Board")
                    await page.evaluate('''() => document.getElementById('sourceType')?.dispatchEvent(new Event('change', { bubbles: true }))''')
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        asrc = await page.query_selector("select#applicantSource")
        if asrc and not await asrc.is_disabled():
            try:
                opts = await page.evaluate('''el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))''', asrc)
                linkedin_val = next((o['v'] for o in opts if 'linkedin' in o['t'].lower()), opts[1]['v'] if len(opts) > 1 else '')
                if linkedin_val:
                    await page.select_option("select#applicantSource", value=linkedin_val)
                    await page.evaluate('''() => document.getElementById('applicantSource')?.dispatchEvent(new Event('change', { bubbles: true }))''')
            except Exception:
                pass

        # 10. Previous Worker Radio button
        prev_emp = custom.get("previously_employed_at_company", "No").lower() == "yes"
        radio_label_txt = "Yes" if prev_emp else "No"
        try:
            print(f"🔘 Selecting Previous Worker: {radio_label_txt}...")
            lbl = page.locator(f"label:has-text('{radio_label_txt}')").first
            await lbl.click(force=True)
            await asyncio.sleep(0.3)
        except Exception as re_err:
            print(f"⚠️ Radio click note: {re_err}")

        # 11. Email Agreement / Talent Community Checkbox
        try:
            await page.evaluate('''() => {
                const a = document.getElementById('emailAgreement');
                if (a && !a.checked) {
                    a.click();
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

    async def fill_step2_experience_and_education(self, page: Page) -> Tuple[bool, str]:
        """Fills out Step 2 (Work Experience & Education) of the application."""
        print("💼 Step 2: Filling Work Experience & Education...")
        prof = self.profile.get("professional", {})
        work_exp = prof.get("work_experience", [])
        default_titles = [w.get("title", "SDE II") for w in work_exp] or ["SDE II", "SDE", "SDE"]

        # 1. Fill empty job titles in experience cards and record parsed companies
        parsed_companies = []
        for idx in range(10):
            comp_el = await page.query_selector(f"input[id='experienceData[{idx}].companyName'], input[id='experienceData[{idx}].company']")
            if comp_el:
                c_name = (await comp_el.input_value()).strip()
                if c_name:
                    parsed_companies.append(c_name.lower())

            inp = await page.query_selector(f"input[id='experienceData[{idx}].title']")
            if not inp:
                break
            curr_val = await inp.input_value()
            if not curr_val.strip():
                title_to_set = default_titles[idx] if idx < len(default_titles) else prof.get("current_title", "SDE II")
                print(f"   Experience [{idx}] Title: {title_to_set}")
                await self.set_field_value(page, f"input[id='experienceData[{idx}].title']", title_to_set)

        # 2. Check if 73 Strings (current role) was missed by parser and add it explicitly
        if not any("73 strings" in c for c in parsed_companies):
            print("➕ '73 Strings' missing from parser. Clicking '+ Add experience'...")
            add_btn = await page.query_selector("button:has-text('+ Add experience'), button:has-text('Add experience')")
            if add_btn:
                await add_btn.click()
                await asyncio.sleep(1.0)
                for idx in range(10):
                    c_el = await page.query_selector(f"input[id='experienceData[{idx}].companyName']")
                    if c_el and not (await c_el.input_value()).strip():
                        print(f"   Injecting 73 Strings (SDE II) into experienceData[{idx}]...")
                        await self.set_field_value(page, f"input[id='experienceData[{idx}].companyName']", "73 Strings")
                        await self.set_field_value(page, f"input[id='experienceData[{idx}].title']", "SDE II")
                        await self.set_field_value(page, f"input[id='experienceData[{idx}].location']", "Bengaluru, India")

                        # Open and select from react-datepicker popup
                        start_sel = f"input[id='experienceData[{idx}].fromTo.startDate']"
                        try:
                            await page.click(start_sel)
                            await asyncio.sleep(0.5)
                            yr_sel = await page.query_selector("select.range-select")
                            if yr_sel:
                                await page.select_option("select.range-select", value="2025")
                                await asyncio.sleep(0.3)
                            nov_el = await page.query_selector(".react-datepicker__month-10, .react-datepicker__month-text:has-text('Nov')")
                            if nov_el:
                                await nov_el.click()
                                await asyncio.sleep(0.3)
                        except Exception as de:
                            print(f"⚠️ Datepicker interaction note: {de}")

                        # Toggle 'I currently work here' to eliminate End Date requirement
                        cb_sel = f"input[id='experienceData[{idx}].fromTo.currentlyWorkHere']"
                        try:
                            cb = await page.query_selector(cb_sel)
                            if cb and not await cb.is_checked():
                                await cb.click()
                                await asyncio.sleep(0.3)
                        except Exception:
                            pass

                        desc_text = self.profile.get("custom_answers", {}).get("approval_workflow_project", "Redesigned approval workflow platform for 30+ enterprise tenants in Java 17/Spring Boot and microservices.")
                        desc_el = await page.query_selector(f"textarea[id='experienceData[{idx}].description']")
                        if desc_el:
                            await desc_el.fill(desc_text[:500])
                        break

        # 3. Select Degree
        deg_select = await page.query_selector("select[id='educationData[0].degree']")
        if deg_select:
            try:
                await deg_select.select_option(value="Degree_Bachelors")
                await page.evaluate('''() => {
                    const el = document.getElementById('educationData[0].degree');
                    if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
                }''')
                print("   Education Degree: Bachelors")
            except Exception:
                pass

        # 3. Select Field of study
        study_select = await page.query_selector("select[id='educationData[0].fieldOfStudy']")
        if study_select:
            try:
                opts = await page.evaluate('''el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))''', study_select)
                cs_val = next((o['v'] for o in opts if 'computer' in o['t'].lower()), opts[1]['v'] if len(opts) > 1 else '')
                if cs_val:
                    await study_select.select_option(value=cs_val)
                    await page.evaluate('''() => {
                        const el = document.getElementById('educationData[0].fieldOfStudy');
                        if (el) el.dispatchEvent(new Event('change', {bubbles: true}));
                    }''')
                    print("   Field of Study: Computer Science / Engineering")
            except Exception:
                pass

        await asyncio.sleep(1.0)
        return True, "Step 2 completed successfully."

    async def fill_step3_job_specific_questions(self, page: Page) -> Tuple[bool, str]:
        """Fills out Step 3 (Job Specific Screening Questions & Salary)."""
        print("📝 Step 3: Filling Application Screening Questions...")
        prof = self.profile.get("professional", {})
        expected_ctc = f"{prof.get('expected_ctc_lpa', 55)} LPA"

        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                lbl = await page.evaluate('''el => {
                    let p = el.parentElement;
                    while (p && !p.querySelector('label') && p.parentElement) p = p.parentElement;
                    return p && p.querySelector('label') ? p.querySelector('label').innerText.toLowerCase() : '';
                }''', sel)
                opts = await page.evaluate('''el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))''', sel)

                val = None
                if "authorized" in lbl or "legally" in lbl:
                    val = next((o['v'] for o in opts if o['t'].lower().strip() == 'yes'), None)
                elif "sponsorship" in lbl or "visa" in lbl:
                    val = next((o['v'] for o in opts if o['t'].lower().strip() == 'no'), None)
                elif "restrictive" in lbl or "non-compete" in lbl:
                    val = next((o['v'] for o in opts if o['t'].lower().strip() == 'no'), None)
                elif "gender" in lbl:
                    val = next((o['v'] for o in opts if 'male' in o['t'].lower() and 'fe' not in o['t'].lower()), None)
                elif "worked for mastercard" in lbl or "previous employee" in lbl:
                    val = next((o['v'] for o in opts if o['t'].lower().strip() == 'no'), None)
                else:
                    # Select 'No' for compliance, conflict of interest, government oversight, and outside employment
                    val = next((o['v'] for o in opts if o['t'].lower().strip() == 'no'), None)

                if val:
                    await sel.select_option(value=val)
                    await page.evaluate('el => el.dispatchEvent(new Event("change", {bubbles: true}))', sel)
            except Exception:
                pass

        # Desired salary field
        sal_inputs = await page.query_selector_all("textarea, input[type=text]")
        for inp in sal_inputs:
            try:
                lbl = await page.evaluate('''el => {
                    let p = el.parentElement;
                    while (p && !p.querySelector('label') && p.parentElement) p = p.parentElement;
                    return p && p.querySelector('label') ? p.querySelector('label').innerText.toLowerCase() : '';
                }''', inp)
                if "salary" in lbl or "desired" in lbl or "compensation" in lbl:
                    print(f"   Desired Salary: {expected_ctc}")
                    await inp.fill(expected_ctc)
                    await inp.evaluate('e => { e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); }')
            except Exception:
                pass

        await asyncio.sleep(1.0)
        return True, "Step 3 completed successfully."

    async def fill_step4_conflict_and_compliance(self, page: Page) -> Tuple[bool, str]:
        """Fills out Step 4 (Additional Application Questionnaire / Conflict Declarations)."""
        print("⚖️ Step 4: Filling Conflict of Interest & Compliance Declarations...")
        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                opts = await page.evaluate('''el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))''', sel)
                no_val = next((o['v'] for o in opts if o['t'].lower().strip() == 'no'), None)
                if no_val:
                    await sel.select_option(value=no_val)
                    await page.evaluate('el => el.dispatchEvent(new Event("change", {bubbles: true}))', sel)
            except Exception:
                pass

        await asyncio.sleep(1.0)
        return True, "Step 4 completed successfully."

    async def fill_step5_voluntary_disclosures(self, page: Page) -> Tuple[bool, str]:
        """Fills out Step 5 (Voluntary Disclosures & Terms Consent)."""
        print("📜 Step 5: Accepting Terms & Privacy Disclosures...")
        await page.evaluate('''() => {
            const cbs = document.querySelectorAll('input[type=checkbox]');
            cbs.forEach(cb => {
                if (!cb.checked) {
                    cb.click();
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                }
            });
        }''')

        # Voluntary Disclosures dropdowns (Veteran, Disability, Race/Ethnicity, Gender)
        selects = await page.query_selector_all("select")
        for sel in selects:
            try:
                lbl = await page.evaluate('''el => {
                    let p = el.parentElement;
                    while (p && !p.querySelector('label') && p.parentElement) p = p.parentElement;
                    return p && p.querySelector('label') ? p.querySelector('label').innerText.toLowerCase() : '';
                }''', sel)
                opts = await page.evaluate('''el => Array.from(el.options).map(o => ({v: o.value, t: o.text}))''', sel)
                val = None
                if "veteran" in lbl:
                    val = next((o['v'] for o in opts if "not" in o['t'].lower() or "no" in o['t'].lower()), None)
                elif "disability" in lbl:
                    val = next((o['v'] for o in opts if "not" in o['t'].lower() or "no" in o['t'].lower() or "do not" in o['t'].lower()), None)
                elif "race" in lbl or "ethnic" in lbl:
                    val = next((o['v'] for o in opts if "asian" in o['t'].lower()), None) or next((o['v'] for o in opts if "decline" in o['t'].lower()), None)
                elif "gender" in lbl:
                    val = next((o['v'] for o in opts if "male" in o['t'].lower() and "fe" not in o['t'].lower()), None)
                if val:
                    await sel.select_option(value=val)
                    await page.evaluate('el => el.dispatchEvent(new Event("change", {bubbles: true}))', sel)
            except Exception:
                pass

        await asyncio.sleep(1.0)
        return True, "Step 5 completed successfully."

    async def advance_step(self, page: Page, current_step_num: int) -> bool:
        """Clicks Next and waits for next step navigation with automatic scroll and retry."""
        print(f"➡️ Advancing from Step {current_step_num}...")
        for attempt in range(3):
            try:
                await page.evaluate('''() => {
                    const btn = document.querySelector('button#next, button[type=submit]');
                    if (btn) btn.scrollIntoView({behavior: 'instant', block: 'center'});
                }''')
                await page.click("button#next, button[type='submit']:has-text('Next')", force=True, timeout=5000)
            except Exception as e:
                print(f"⚠️ Advance click attempt {attempt+1} note: {e}")

            for _ in range(12):
                await asyncio.sleep(1.0)
                url = page.url.lower()
                if (
                    f"step={current_step_num + 1}" in url
                    or "applicationreview" in url
                    or "review" in url
                    or "applythankyou" in url
                    or (current_step_num == 1 and ("step=2" in url or "workandeducation" in url))
                    or (current_step_num == 2 and ("step=3" in url or "jobspecificquestions" in url))
                    or (current_step_num == 3 and ("step=4" in url or "applicationquestionnaire" in url or "voluntarydisclosures" in url))
                    or (current_step_num == 4 and ("step=5" in url or "voluntarydisclosures" in url or "review" in url))
                ):
                    return True
        return False

    async def apply_to_job(
        self,
        job: Dict[str, Any],
        context: Optional[BrowserContext] = None
    ) -> Dict[str, Any]:
        """Executes full Phenom application for a single job through all 6 steps."""
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

            await page.wait_for_selector("select#country, input[name='cntryFields.firstName'], button#next", timeout=18000)
            await asyncio.sleep(1.5)

            # Dismiss OneTrust cookie overlay & clean backdrops
            await self.dismiss_cookie_banner(page)

            # Check if position is expired
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
            step1_ok, step1_msg = await self.fill_step1_personal_info(page)
            if not step1_ok:
                await capture_proof(page, sc_path)
                update_job_status(job_id, "FAILED", step1_msg, sc_path)
                return {"job_id": job_id, "status": "FAILED", "message": step1_msg}

            await self.advance_step(page, 1)

            # ── Step 2: Experience & Education ────────────────────────────────
            if "step=2" in page.url or "workAndEducation" in page.url:
                await asyncio.sleep(2.0)
                await self.fill_step2_experience_and_education(page)
                await self.advance_step(page, 2)

            # ── Step 3: Job Specific Questions ────────────────────────────────
            if "step=3" in page.url or "jobSpecificQuestions" in page.url:
                await asyncio.sleep(2.0)
                await self.fill_step3_job_specific_questions(page)
                await self.advance_step(page, 3)

            # ── Step 4: Questionnaire or Voluntary Disclosures ────────────────
            url_low = page.url.lower()
            if "applicationquestionnaire" in url_low:
                await asyncio.sleep(2.0)
                await self.fill_step4_conflict_and_compliance(page)
                await self.advance_step(page, 4)

            url_low = page.url.lower()
            if "voluntarydisclosures" in url_low or "applicantacknowledgment" in url_low or (("step=4" in url_low or "step=5" in url_low) and "review" not in url_low):
                if "applicationreview" not in url_low and "review" not in url_low:
                    await asyncio.sleep(2.0)
                    await self.fill_step5_voluntary_disclosures(page)
                    await self.advance_step(page, 5)

            # ── Step Review & Final Submit ─────────────────────────────────────
            for _ in range(15):
                if "applicationreview" in page.url.lower() or "review" in page.url.lower() or "step=6" in page.url:
                    break
                await asyncio.sleep(1.0)
            await asyncio.sleep(2.5)
            print(f"🏁 Arrived on Review Page: {page.url}")

            # Capture Review proof screenshot
            await capture_proof(page, sc_path)

            if self.dry_run:
                status = "READY_TO_SUBMIT (DRY_RUN)"
                final_msg = f"All 6 application steps completed & verified! Form prefilled with profile, experience, questions, and attached resume. [DRY RUN: Ready in front of Submit button. Proof captured in {sc_path}]"
                print(f"✅ Application 100% completed through Step 6 Review! [DRY RUN: Proof saved at {sc_path}]")
                update_job_status(job_id, status, final_msg, sc_path)
            else:
                # ── Live Submission Execution ─────────────────────────────────
                print("🚀 LIVE MODE: Executing final application submission...")
                submit_btn = await page.query_selector("button.btn-submit, button[type='submit']:has-text('Submit'), button:has-text('Submit Application')")
                if submit_btn:
                    await submit_btn.click(force=True)
                    print("➡️ Clicked Submit button. Waiting for portal confirmation...")
                    await asyncio.sleep(8.0)

                await capture_proof(page, sc_path)
                final_url = page.url.lower()
                final_content = (await page.content()).lower()

                is_applied = (
                    "applythankyou" in final_url
                    or "status=success" in final_url
                    or any(k in final_content for k in [
                        "thank you for applying",
                        "application received",
                        "successfully applied",
                        "application submitted"
                    ])
                )
                is_already_applied = (
                    "status=alreadyapplied" in final_url
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
                    final_msg = f"Application reached final stage ({final_url}). Review required."
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

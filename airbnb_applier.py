#!/usr/bin/env python3
"""
Airbnb Dedicated Applier Engine.
Automates Airbnb Greenhouse job applications:
- Resolves embedded Greenhouse iframe on careers.airbnb.com
- Expands iframe height to prevent layout clipping and enable smooth dropdown interactions
- Robust identity, phone (+91 India), city (Bangalore), resume, and screening question answers
- Captures pre-submission Review screenshot -> screenshots/airbnb_{job_id}.png
- Completes Greenhouse email security-code step via Gmail IMAP
- Verifies post-submission confirmation banner -> screenshots/airbnb_{job_id}_submitted.png
- Zero false positives: Only marks APPLIED on verified confirmation
"""

import asyncio
import os
import json
import re
import random
import time
from typing import Dict, Any, Optional, Tuple, List
from playwright.async_api import async_playwright, Page, BrowserContext

from database import update_job_status, get_db_connection
from llm_form_engine import FormLLMEngine
from greenhouse_email_verify import (
    CONFIRM_PHRASES,
    ERROR_PHRASES,
    handle_greenhouse_security_code,
    page_has_phrases,
)


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
    """Takes a full-page screenshot after temporarily unsticking sticky headers and expanding iframe."""
    try:
        await page.evaluate('''() => {
            const ifr = document.querySelector('#grnhse_iframe, iframe[src*="greenhouse"]');
            if (ifr) {
                ifr.style.height = '3500px';
                ifr.style.minHeight = '3500px';
            }
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'sticky' || (style.position === 'fixed' && !el.className.includes('grecaptcha'))) {
                    el.style.position = 'static';
                }
            });
        }''')
        await asyncio.sleep(1.0)
    except Exception:
        pass
    await page.screenshot(path=screenshot_path, full_page=True)


class AirbnbApplier:
    def __init__(
        self,
        profile_path: str = "user_profile.json",
        dry_run: bool = True,
        headless: bool = True,
        keep_open: bool = False
    ):
        self.profile = load_user_profile(profile_path)
        self.dry_run = dry_run
        self.headless = headless
        self.keep_open = keep_open
        self.llm = FormLLMEngine(self.profile)

    async def fill_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        job_id = job["job_id"]
        url = job["apply_url"]
        pers = self.profile["personal"]
        prof = self.profile["professional"]

        # Resolve Greenhouse URL to direct careers.airbnb.com URL
        clean_url = url
        m = re.search(r'jobs/(\d+)', url)
        if m:
            clean_url = f"https://careers.airbnb.com/positions/{m.group(1)}/?gh_jid={m.group(1)}"

        print(f"🌐 Navigating to Airbnb application: {clean_url}...")
        try:
            await page.goto(clean_url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(2.0)
        except Exception as e:
            print(f"⚠️ Navigation note: {e}")

        # Check if expired or closed
        content = (await page.content()).lower()
        if any(msg in content for msg in ["job is no longer available", "posting has closed", "this job has expired"]):
            return False, "Airbnb job posting has closed / expired."

        # Click Apply Now or Application tab
        app_btn = await page.wait_for_selector(
            'button.js-job-app, button:has-text("Application"), button:has-text("Apply Now"), a:has-text("Apply Now")',
            timeout=10000
        )
        if app_btn:
            try:
                await app_btn.scroll_into_view_if_needed()
                await app_btn.click()
                print("Clicked Application tab")
                await asyncio.sleep(1.0)
            except Exception:
                pass

        # Detect Greenhouse iframe after tab click
        target = None
        for _ in range(20):
            for f in page.frames:
                if "greenhouse.io" in f.url or "embed/job_app" in f.url:
                    target = f
                    break
            if target:
                break
            await asyncio.sleep(0.5)

        if not target:
            target = page
            print("⚠️ Greenhouse iframe not detected, using main page.")
        else:
            print(f"🎯 Target frame resolved: iframe ({target.url[:50]}...)")
            try:
                await target.wait_for_load_state("domcontentloaded")
                await target.wait_for_selector("#first_name", timeout=15000)
            except Exception:
                pass

        # Expand iframe height immediately so React components render cleanly
        try:
            await page.evaluate('''() => {
                const ifr = document.querySelector('#grnhse_iframe, iframe[src*="greenhouse"]');
                if (ifr) {
                    ifr.style.height = '3500px';
                    ifr.style.minHeight = '3500px';
                }
            }''')
            await asyncio.sleep(1.0)
        except Exception:
            pass

        # Dismiss cookie notice if present
        try:
            cb = await page.query_selector(
                "button#onetrust-accept-btn-handler, button:has-text('Accept'), div[aria-label*='cookie'] button"
            )
            if cb:
                await cb.click(force=True)
                await asyncio.sleep(0.5)
        except Exception:
            pass

        # 1. Identity Fields
        fn = await target.query_selector("#first_name, input[name='first_name']")
        if fn:
            await human_type(fn, pers.get("first_name", "Aditya"))

        ln = await target.query_selector("#last_name, input[name='last_name']")
        if ln:
            await human_type(ln, pers.get("last_name", "Kumar"))

        em = await target.query_selector("#email, input[name='email']")
        if em:
            await human_type(em, pers.get("email", "adityakumargupta521@gmail.com"))

        # 2. Country Picker (+91 India)
        c_inp = await target.query_selector("#country")
        if c_inp:
            try:
                ctrl = await c_inp.evaluate_handle('e => e.closest(".select__control")')
                await ctrl.click()
                await asyncio.sleep(0.3)
                await page.keyboard.type("India", delay=30)
                await asyncio.sleep(0.5)
                opts = await target.query_selector_all(".select__option")
                for o in opts:
                    txt = (await o.inner_text()).strip()
                    if txt.startswith("India"):
                        await o.click()
                        print(f"Selected Country: {txt}")
                        break
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
            except Exception:
                pass

        # 3. Phone (10 digits)
        ph = await target.query_selector("#phone, input[name='phone']")
        if ph:
            raw_phone = pers.get("phone", "9643914225")
            clean_phone = re.sub(r'\D', '', raw_phone)[-10:]
            await human_type(ph, clean_phone)

        # 4. Location (Bangalore, India)
        loc_inp = await target.query_selector("#candidate-location, input[id*='location' i]")
        if loc_inp:
            try:
                loc_ctrl = await loc_inp.evaluate_handle('e => e.closest(".select__control")')
                await loc_ctrl.click()
                await asyncio.sleep(0.3)
                await page.keyboard.type("Bangalore", delay=30)
                await asyncio.sleep(1.2)
                opts = await target.query_selector_all(".select__option")
                for o in opts:
                    txt = (await o.inner_text()).strip()
                    if "Bangalore" in txt or "Bengaluru" in txt:
                        await o.click()
                        print(f"Selected Location: {txt}")
                        break
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
            except Exception:
                pass

        # 5. Text inputs & custom questions
        text_inputs = await target.query_selector_all("input[type='text']:not(.select__input), textarea")
        for ti in text_inputs:
            try:
                ti_id = await ti.get_attribute("id") or ""
                p_label = await ti.evaluate_handle('''e => {
                    let p = e.parentElement;
                    while (p && p !== document.body) {
                        const l = p.querySelector('label');
                        if (l) return l.innerText.trim();
                        p = p.parentElement;
                    }
                    return "";
                }''')
                lbl = await p_label.json_value()
                lbl_low = lbl.lower()

                # Skip identity & contact fields already handled
                if ti_id in ["first_name", "last_name", "email", "phone"] or any(k in lbl_low for k in ["first name", "last name", "email", "phone"]):
                    continue

                val = await ti.input_value()
                if not val and lbl:
                    if "linkedin" in lbl_low:
                        ans = pers.get("linkedin_url", "https://www.linkedin.com/in/aditya-kumar-a518571b5/")
                    elif "current job location" in lbl_low or "current location" in lbl_low:
                        ans = "Bangalore, India"
                    elif "current company" in lbl_low:
                        ans = "73 Strings"
                    elif "location" in lbl_low and "relocate" in lbl_low:
                        ans = "Bangalore, India. Yes, open and willing to relocate."
                    else:
                        ans = self.llm.answer_question(lbl, job_context=job)
                    print(f"Answering text question: '{lbl[:40]}' -> '{ans}'")
                    await human_type(ti, ans)
                    await asyncio.sleep(0.2)
            except Exception:
                pass

        # 6. Targeted Dropdown Selection (Strict Prefix Matching)
        dropdown_queries = [
            ("hear about this job", "Social media", False),
            ("years of work experience", "3 - 6 years", True),
            ("Jira cloud", "Yes", True),
            ("gender", "Male", True),
            ("Privacy Notice", "I have read and consent", True),
            ("employed by Airbnb", "No", True),
            ("blood relatives", "No", True),
            ("legally authorized", "Yes", True),
            ("sponsorship", "No", True),
            ("Attestation", "I agree", False),
        ]

        for q_txt, target_val, is_start in dropdown_queries:
            try:
                c = await target.query_selector(f".select__container:has-text('{q_txt}')")
                if not c:
                    continue
                # Check if already answered
                sv = await c.query_selector(".select__single-value")
                if sv and (await sv.inner_text()).strip():
                    continue

                ctrl = await c.query_selector(".select__control")
                if not ctrl:
                    continue
                await ctrl.click()
                await asyncio.sleep(0.4)
                opts = await target.query_selector_all(".select__option")
                for o in opts:
                    ot = (await o.inner_text()).strip()
                    matched = ot.lower().startswith(target_val.lower()) if is_start else target_val.lower() in ot.lower()
                    if matched:
                        await o.click()
                        print(f"[{q_txt}] -> Selected: {ot}")
                        break
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
            except Exception:
                pass

        # Dynamic fallback for any other select containers
        try:
            containers = await target.query_selector_all(".select__container")
            for sc in containers:
                sv = await sc.query_selector(".select__single-value")
                if sv and (await sv.inner_text()).strip():
                    continue
                lbl_el = await sc.query_selector("label")
                if not lbl_el:
                    continue
                lbl = (await lbl_el.inner_text()).strip()
                lbl_low = lbl.lower()
                if "country" in lbl_low or "location" in lbl_low:
                    continue
                ctrl = await sc.query_selector(".select__control")
                if not ctrl:
                    continue
                await ctrl.click()
                await asyncio.sleep(0.4)
                opts = await target.query_selector_all(".select__option")
                opt_texts = [(await o.inner_text()).strip() for o in opts]
                if opt_texts:
                    chosen = self.llm.answer_question(lbl, opt_texts, job_context=job)
                    for o in opts:
                        ot = (await o.inner_text()).strip()
                        if ot.lower() == chosen.lower() or chosen.lower() in ot.lower():
                            await o.click()
                            print(f"Fallback selected '{ot}' for '{lbl}'")
                            break
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.2)
        except Exception:
            pass

        # 7. Resume Upload (Uploaded at the end so background parsing doesn't block dropdowns)
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await target.query_selector("input#resume, input[type='file'][id*='resume' i], input[type='file']")
            if file_input:
                try:
                    await file_input.set_input_files(os.path.abspath(resume_path))
                    print(f"📄 Attached resume: {resume_path}, waiting 3s for Greenhouse upload...")
                    await asyncio.sleep(3.0)
                except Exception as fe:
                    print(f"⚠️ Resume attach note: {fe}")

        # Capture Pre-Submission Review Screenshot
        os.makedirs("screenshots", exist_ok=True)
        sc_path = f"screenshots/airbnb_{job_id}.png"
        await capture_clean_screenshot(page, sc_path)
        print(f"📸 Captured Airbnb Review screenshot: {sc_path}")

        if self.dry_run:
            return True, f"Airbnb Greenhouse: Form populated successfully [Dry Run parked on Review: {sc_path}]"

        # 8. Submit Application (Live mode only)
        submit_btn = await target.query_selector(
            "#submit_app, button[type='submit'], input[type='submit'], button:has-text('Submit Application'), button:has-text('Submit')"
        )
        if submit_btn and await submit_btn.is_visible() and await submit_btn.is_enabled():
            print("🚀 Clicking Submit Application button...")
            submit_start = time.time()
            await submit_btn.click(force=True)
            await asyncio.sleep(2.0)

            await handle_greenhouse_security_code(
                page,
                extra_targets=[target],
                sender_keywords=("airbnb", "greenhouse"),
                email_addr=pers.get("email"),
                received_after_ts=submit_start,
            )

            submitted = False
            had_error = False
            for _ in range(12):
                submitted = await page_has_phrases(page, CONFIRM_PHRASES, extra_targets=[target])
                had_error = await page_has_phrases(page, ERROR_PHRASES, extra_targets=[target])
                if submitted or had_error:
                    break
                await asyncio.sleep(1.0)

            sub_sc = f"screenshots/airbnb_{job_id}_submitted.png"
            try:
                await capture_clean_screenshot(page, sub_sc)
            except Exception:
                pass

            if submitted and not had_error:
                return True, f"Airbnb Greenhouse: Application submitted successfully! Proof: {sub_sc}"
            if had_error:
                return False, f"Airbnb Greenhouse: Submit hit a portal/security-code error. Proof: {sub_sc}"
            return False, "Airbnb Greenhouse: Submit clicked but confirmation banner not verified (email security code may still be pending)."

        return False, "Submit button not found or disabled."

    async def apply_to_job(self, job: Dict[str, Any], context: Optional[BrowserContext] = None) -> Tuple[bool, str]:
        if context:
            page = await context.new_page()
            try:
                return await self.fill_form(page, job)
            finally:
                if not self.keep_open:
                    await page.close()
        else:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                ctx = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 900}
                )
                page = await ctx.new_page()
                try:
                    return await self.fill_form(page, job)
                finally:
                    await browser.close()

#!/usr/bin/env python3
"""
DoorDash Dedicated Stealth Applier Engine.
Automates DoorDash Greenhouse job applications:
- Stealth browser context with jitter keystrokes
- Resume upload, contact info, and custom question answers
- Captures pre-submission Review screenshot -> screenshots/doordash_{job_id}.png
- Completes Greenhouse email security-code step via Gmail IMAP
- Verifies post-submission confirmation banner -> screenshots/doordash_{job_id}_submitted.png
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
from playwright_stealth import Stealth

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
    """Types text with natural human delays between keystrokes."""
    if not text:
        return
    try:
        await element.fill("")
    except Exception:
        pass
    for char in text:
        await element.type(char, delay=random.uniform(25, 65))
        if random.random() < 0.04:
            await asyncio.sleep(random.uniform(0.08, 0.2))


async def capture_clean_screenshot(page: Page, screenshot_path: str):
    """Takes a full-page screenshot after temporarily unsticking sticky headers."""
    try:
        await page.evaluate('''() => {
            document.querySelectorAll('*').forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'sticky' || (style.position === 'fixed' && !el.className.includes('grecaptcha'))) {
                    el.style.position = 'static';
                }
            });
        }''')
    except Exception:
        pass
    await page.screenshot(path=screenshot_path, full_page=True)


class DoorDashApplier:
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
        self.stealth = Stealth()
        self.llm = FormLLMEngine(self.profile)

    async def fill_form(self, page: Page, job: Dict[str, Any]) -> Tuple[bool, str]:
        job_id = job["job_id"]
        url = job["apply_url"]
        pers = self.profile["personal"]
        prof = self.profile["professional"]

        print(f"🌐 Navigating to DoorDash application: {url}...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2.5)
        except Exception as e:
            print(f"⚠️ Navigation note: {e}")

        # Check if expired or closed
        content = (await page.content()).lower()
        if any(msg in content for msg in ["job is no longer available", "posting has closed", "this job has expired"]):
            return False, "DoorDash job posting has closed / expired."

        # Detect Greenhouse iframe if embedded
        target = page
        for f in page.frames:
            if "greenhouse.io" in f.url or "gh_jid" in f.url:
                target = f
                break

        # 1. Identity Fields
        fn = await target.query_selector("#first_name, input[name='first_name'], input[id*='first_name']")
        if fn:
            await human_type(fn, pers.get("first_name", "Aditya"))

        ln = await target.query_selector("#last_name, input[name='last_name'], input[id*='last_name']")
        if ln:
            await human_type(ln, pers.get("last_name", "Kumar"))

        em = await target.query_selector("#email, input[name='email'], input[id*='email']")
        if em:
            await human_type(em, pers.get("email", "adityakumargupta521@gmail.com"))

        # Select India (+91) in phone country picker
        try:
            await page.evaluate('''() => {
                const btn = document.querySelector('button.iti__selected-country, .iti__selected-country');
                if (btn) btn.click();
            }''')
            await asyncio.sleep(0.4)
            await page.evaluate('''() => {
                const item = document.querySelector('#iti-0__item-in, li[data-country-code="in"]');
                if (item) item.click();
            }''')
            await asyncio.sleep(0.2)
        except Exception:
            pass

        ph = await target.query_selector("#phone, input[name='phone'], input[id*='phone']")
        if ph:
            raw_phone = pers.get("phone", "9643914225")
            clean_phone = re.sub(r'\D', '', raw_phone)[-10:]
            await human_type(ph, clean_phone)

        # Links (LinkedIn, GitHub, Portfolio)
        for field, val in [("linkedin", pers.get("linkedin_url")), ("github", pers.get("github_url")), ("website", pers.get("linkedin_url"))]:
            if val:
                el = await target.query_selector(f"input[name*='{field}' i], input[id*='{field}' i], input[aria-label*='{field}' i]")
                if el and not await el.input_value():
                    await human_type(el, val)

        # 2. Location (Bangalore, India)
        loc_el = await target.query_selector("#candidate-location, input[id*='location' i], input[name*='location' i]")
        if loc_el:
            try:
                await loc_el.click()
                await human_type(loc_el, "Bangalore, Karnataka, India")
                await asyncio.sleep(1.2)
                opt = await target.query_selector("div.select__option, div[id*='option-0']")
                if opt:
                    await opt.click(force=True)
                else:
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
            except Exception:
                pass

        # 3. Resume Upload
        resume_path = self.profile.get("resume", {}).get("file_path", "resume.pdf")
        if resume_path and os.path.exists(resume_path):
            file_input = await target.query_selector("input#resume, input[type='file'][id*='resume' i], input[type='file']")
            if file_input:
                try:
                    await file_input.set_input_files(resume_path)
                    print(f"📄 Attached resume: {resume_path}")
                    await asyncio.sleep(1.5)
                except Exception as fe:
                    print(f"⚠️ Resume attach note: {fe}")

        # 4. Custom Screening Questions (Sponsorship, Authorization, Experience)
        fields = await target.query_selector_all("div.field, div[class*='field'], li.custom-question")
        for f in fields:
            try:
                label_el = await f.query_selector("label")
                if not label_el:
                    continue
                q_text = (await label_el.inner_text()).strip()
                if not q_text or len(q_text) < 3:
                    continue

                q_low = q_text.lower()
                text_inp = await f.query_selector("input[type='text']:not(.select__input), textarea")
                if text_inp and not await text_inp.input_value():
                    if "location" in q_low and "relocate" in q_low:
                        ans = "Bangalore, India. Yes, open and willing to relocate."
                    else:
                        ans = self.llm.answer_question(q_text, job_context=job)
                    if ans:
                        await human_type(text_inp, ans)

                # Select dropdown
                select_el = await f.query_selector("select")
                if select_el:
                    opts = await select_el.query_selector_all("option")
                    opt_texts = [await o.inner_text() for o in opts if await o.get_attribute("value")]
                    if opt_texts:
                        chosen = self.llm.answer_question(q_text, opt_texts, job_context=job)
                        if chosen:
                            await select_el.select_option(label=chosen)
            except Exception:
                pass

        # 5. Targeted Greenhouse Specific Fields (Zero Blunders)
        # 5a. School Name -> 'Other'
        school_inp = await target.query_selector("input#school--0, input[id*='school' i]")
        if school_inp:
            try:
                await school_inp.focus()
                await page.keyboard.type("Other", delay=50)
                await asyncio.sleep(1.0)
                await page.evaluate('''() => {
                    const listboxId = document.querySelector('input#school--0').getAttribute('aria-controls');
                    const listbox = listboxId ? document.getElementById(listboxId) : document;
                    const opts = Array.from(listbox.querySelectorAll('[role="option"]'));
                    const exact = opts.find(o => o.innerText.trim() === 'Other');
                    if (exact) exact.click();
                }''')
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"⚠️ School selection note: {e}")

        # 5b. Degree -> Bachelor's Degree
        deg_inp = await target.query_selector("input#degree--0, input[id*='degree' i]")
        if deg_inp:
            try:
                await deg_inp.click(force=True)
                await asyncio.sleep(0.5)
                await page.evaluate('''() => {
                    const opts = Array.from(document.querySelectorAll('[role="option"], .select__option, li'));
                    const m = opts.find(o => o.innerText.toLowerCase().includes("bachelor"));
                    if (m) m.click();
                }''')
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"⚠️ Degree selection note: {e}")

        # 5c. Have you worked at DoorDash? -> 'I have not worked at DoorDash'
        dd_emp = await target.query_selector("[id='question_56389201'], input[id*='worked_at_doordash' i], [aria-label*='worked at DoorDash' i]")
        if dd_emp:
            try:
                await dd_emp.click(force=True)
                await asyncio.sleep(0.5)
                await page.evaluate('''() => {
                    const opts = Array.from(document.querySelectorAll('[role="option"], .select__option, li'));
                    const m = opts.find(o => o.innerText.toLowerCase().includes("not worked at doordash") || o.innerText.toLowerCase().includes("have not worked"));
                    if (m) m.click();
                }''')
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"⚠️ DoorDash employment question note: {e}")

        # 5d. Demographic fields (Hispanic: No, Race: Asian, Gender: Male, Trans: No, Veteran: Not protected, Disability: No)
        demo_mappings = [
            ("1332", ["no"], "Hispanic/Latinx: No"),
            ("1333", ["asian"], "Race: Asian"),
            ("864", ["male"], "Gender: Male"),
            ("1328", ["no"], "Transgender: No"),
            ("1336", ["not a protected veteran", "not a veteran", "no"], "Veteran: Not protected"),
            ("1337", ["no, i don’t have a disability", "no, i do not have a disability", "no"], "Disability: No")
        ]
        for field_id, target_keywords, desc in demo_mappings:
            try:
                el = await target.query_selector(f"[id='{field_id}']")
                if el:
                    await el.click(force=True)
                    await asyncio.sleep(0.5)
                    await page.evaluate('''(keywords) => {
                        const opts = Array.from(document.querySelectorAll('[role="option"], .select__option, li'));
                        for (const kw of keywords) {
                            const m = opts.find(o => {
                                const t = o.innerText.trim().toLowerCase();
                                if (kw === 'male') return t === 'male' || (t.startsWith('male') && !t.includes('female'));
                                if (kw === 'no') return t === 'no';
                                return t.includes(kw);
                            });
                            if (m) { m.click(); return; }
                        }
                    }''', target_keywords)
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.2)
            except Exception as e:
                print(f"⚠️ Demographic {desc} note: {e}")

        # 6. Any Remaining Greenhouse Comboboxes (select__control)
        combos = await target.query_selector_all("div.select__control")
        for c in combos:
            try:
                parent = await c.evaluate_handle('e => e.closest(".select__container") || e.closest("div.field") || e.parentElement')
                label = await parent.query_selector("label")
                if not label:
                    continue
                lbl_txt = (await label.inner_text()).strip()
                lbl_low = lbl_txt.lower()
                if not lbl_txt or "location (city)" in lbl_low:
                    continue

                # Skip any question that was already answered in section 5
                if any(k in lbl_low for k in ["gender", "school", "degree", "doordash", "hispanic", "latinx", "race", "transgender", "veteran", "disability"]):
                    continue

                has_val = await parent.query_selector("div.select__single-value, div.select__multi-value")
                if has_val:
                    continue

                await c.click(force=True)
                await asyncio.sleep(0.4)
                options = await target.query_selector_all("div[class*='option'].select__option")
                if not options:
                    await page.keyboard.press("Escape")
                    continue
                opt_texts = [(await o.inner_text()).strip() for o in options]

                if "worked at doordash" in lbl_low or ("worked" in lbl_low and "doordash" in lbl_low):
                    chosen = "I have not worked at DoorDash"
                    for ot in opt_texts:
                        if "not worked at" in ot.lower() or "have not worked" in ot.lower():
                            chosen = ot
                            break
                elif "hispanic" in lbl_low or "latinx" in lbl_low:
                    chosen = "No"
                elif "degree" in lbl_low:
                    chosen = "Bachelor"
                    for ot in opt_texts:
                        if "bachelor" in ot.lower():
                            chosen = ot
                            break
                elif "school" in lbl_low or "university" in lbl_low:
                    chosen = "Other"
                    for ot in opt_texts:
                        if ot.lower() == "other":
                            chosen = ot
                            break
                else:
                    chosen = self.llm.answer_question(lbl_txt, opt_texts, job_context=job)

                if chosen:
                    for o in options:
                        o_txt = (await o.inner_text()).strip().lower()
                        if chosen.lower() == o_txt or chosen.lower() in o_txt:
                            await o.click(force=True)
                            break
                await page.keyboard.press("Escape")
            except Exception:
                pass

        # Capture Pre-Submission Review Screenshot
        os.makedirs("screenshots", exist_ok=True)
        sc_path = f"screenshots/doordash_{job_id}.png"
        await capture_clean_screenshot(page, sc_path)
        print(f"📸 Captured DoorDash Review screenshot: {sc_path}")

        if self.dry_run:
            return True, f"DoorDash Greenhouse: Form populated successfully [Dry Run parked on Review: {sc_path}]"

        # 6. Submit Application
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
                sender_keywords=("doordash", "greenhouse"),
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

            sub_sc = f"screenshots/doordash_{job_id}_submitted.png"
            try:
                await capture_clean_screenshot(page, sub_sc)
            except Exception:
                pass

            if submitted and not had_error:
                return True, f"DoorDash Greenhouse: Application submitted successfully! Proof: {sub_sc}"
            if had_error:
                return False, f"DoorDash Greenhouse: Submit hit a portal/security-code error. Proof: {sub_sc}"
            return False, "DoorDash Greenhouse: Submit clicked but confirmation banner not verified (email security code may still be pending)."

        return False, "Submit button not found or disabled."

    async def apply_to_job(self, job: Dict[str, Any], context: Optional[BrowserContext] = None) -> Tuple[bool, str]:
        if context:
            page = await context.new_page()
            await self.stealth.apply_stealth_async(page)
            try:
                return await self.fill_form(page, job)
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
                    return await self.fill_form(page, job)
                finally:
                    await browser.close()
